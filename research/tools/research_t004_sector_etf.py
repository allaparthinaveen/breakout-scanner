"""
Pre-Registered Study T004: US Sector ETF Rotation (12-1m Momentum + 15% Vol-Target
& SPY > SMA200 Regime Overlay, 2000-2026, Zero Survivorship Bias).

Implements research_out/phase1/PREREG_T004_US_SECTOR_ETF.md strictly:
  - Stage 1 (`--stage 1`): DEV-only (2000-01..2015-12, 192 mos) tercile event study.
    HOLDOUT is NEVER touched in Stage 1.
  - Stage 2 (`--stage 2`): Executed ONCE only if Stage 1 passes. Evaluates S0, S1, S2
    against matched controls EW_S0, EW_S1, EW_S2 and SPY using point-in-time ^IRX
    T-bill excess-return Sharpe at 1x (10 bps RT) and 2x (20 bps RT) costs.
"""

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.tools.research_momentum_12_1 import (
    build_monthly_schedule,
    compute_basket_vol_weight,
    compute_benchmark_regime,
    compute_mom_12_1_panel,
    compute_rebalance_cost,
)

DATA_DIR = ROOT / "data" / "research_etf"
OUT_DIR = ROOT / "research" / "phase1" / "T004"

SECTOR_ETFS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
DEV_START = "2000-01"
DEV_END = "2015-12"
HOLDOUT_START = "2016-01"

ONE_WAY_1X = 0.0005  # 5 bps one-way = 10 bps round-trip
ONE_WAY_2X = 0.0010  # 10 bps one-way = 20 bps round-trip
VOL_LOOKBACK = 60
TARGET_VOL = 0.15
REGIME_SMA = 200

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("research_t004")


def load_etf_dataset() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    spy_df = pd.read_pickle(DATA_DIR / "SPY_1d.pkl").sort_index()
    irx_df = pd.read_pickle(DATA_DIR / "^IRX_1d.pkl").sort_index()
    sector_dfs: Dict[str, pd.DataFrame] = {}
    for sym in SECTOR_ETFS:
        sector_dfs[sym] = pd.read_pickle(DATA_DIR / f"{sym}_1d.pkl").sort_index()
    return sector_dfs, spy_df, irx_df


def lookup_causal_tbill_monthly(irx_close: pd.Series, sig_dt: pd.Timestamp) -> float:
    """Strictly causal monthly risk-free return from ^IRX annualized yield (%) <= sig_dt."""
    if irx_close is None or irx_close.empty:
        return 0.0
    pos = irx_close.index.searchsorted(sig_dt, side="right") - 1
    if pos < 0:
        return 0.0
    y_pct = max(0.0, float(irx_close.iloc[pos]))
    y_ann = y_pct / 100.0
    return float((1.0 + y_ann) ** (1.0 / 12.0) - 1.0)


def assign_terciles(scores: Dict[str, float]) -> Tuple[List[str], List[str], List[str]]:
    """Sort ascending by (score, symbol) and split 9 sectors into T1 (bottom 3), T2 (mid 3), T3 (top 3)."""
    ordered = [s for s, _ in sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))]
    parts = [list(a) for a in np.array_split(ordered, 3)]
    return parts[0], parts[1], parts[2]


def _open_on_or_after(series: pd.Series, dt: pd.Timestamp, max_days: int = 4) -> Optional[float]:
    valid = series.dropna()
    if valid.empty:
        return None
    pos = valid.index.searchsorted(dt, side="left")
    if pos >= len(valid):
        return None
    if (valid.index[pos] - dt).days > max_days:
        return None
    v = float(valid.iloc[pos])
    return v if v > 0 else None


def _open_on_or_before(series: pd.Series, dt: pd.Timestamp, max_days: int = 4) -> Optional[float]:
    valid = series.dropna()
    if valid.empty:
        return None
    pos = valid.index.searchsorted(dt, side="right") - 1
    if pos < 0:
        return None
    if (dt - valid.index[pos]).days > max_days:
        return None
    v = float(valid.iloc[pos])
    return v if v > 0 else None


def simulate_sector_rotation(
    sector_dfs: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    irx_df: pd.DataFrame,
    max_period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Monthly open-to-open simulation for T1..T3, S0..S2, matched EW_S0..EW_S2, SPY, and ^IRX T-bill.
    If `max_period` is set (e.g., '2015-12' for Stage 1), later periods are NEVER computed.
    """
    mom_df, close_df, open_df = compute_mom_12_1_panel(sector_dfs)
    daily_rets_df = close_df.pct_change()
    regime_series = compute_benchmark_regime(spy_df, sma_len=REGIME_SMA)
    spy_open = spy_df["Open"].sort_index().astype(float)
    irx_close = irx_df["Close"].sort_index().astype(float)

    schedule = build_monthly_schedule(spy_df)
    schedule = [p for p in schedule if p["period"] >= DEV_START]
    if max_period is not None:
        schedule = [p for p in schedule if p["period"] <= max_period]

    strat_names = ["T1", "T2", "T3", "S0", "S1", "S2", "EW_S0", "EW_S1", "EW_S2"]
    prev_weights: Dict[str, Dict[str, float]] = {k: {} for k in strat_names}
    prev_rets: Dict[str, Dict[str, float]] = {k: {} for k in strat_names}
    prev_gross: Dict[str, float] = {k: 0.0 for k in strat_names}

    rows: List[Dict[str, Any]] = []
    sector_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]] = {k: {} for k in ["S0", "S1", "S2"]}

    for step in schedule:
        period = step["period"]
        sig_dt = step["signal_date"]
        fill_dt = step["fill_date"]
        next_fill_dt = step["next_fill_date"]
        split = "DEV" if period <= DEV_END else "HOLDOUT"

        s_o0 = _open_on_or_after(spy_open, fill_dt)
        s_o1 = _open_on_or_before(spy_open, next_fill_dt)
        if s_o0 is None or s_o1 is None:
            continue
        spy_ret = (s_o1 / s_o0) - 1.0
        rf_m = lookup_causal_tbill_monthly(irx_close, sig_dt)

        if sig_dt not in mom_df.index:
            continue
        mom_row = mom_df.loc[sig_dt].dropna()
        if len(mom_row) < 9:
            continue

        scores: Dict[str, float] = {}
        sec_rets: Dict[str, float] = {}
        for sym in SECTOR_ETFS:
            if sym not in mom_row.index or not math.isfinite(float(mom_row[sym])):
                continue
            o0 = _open_on_or_after(open_df[sym], fill_dt)
            o1 = _open_on_or_before(open_df[sym], next_fill_dt)
            if o0 is None or o1 is None:
                continue
            scores[sym] = float(mom_row[sym])
            sec_rets[sym] = (o1 / o0) - 1.0

        if len(scores) < 9:
            continue

        t1, t2, t3 = assign_terciles(scores)
        all_secs = sorted(scores.keys())

        reg_pos = regime_series.index.searchsorted(sig_dt, side="right") - 1
        regime_ok = bool(regime_series.iloc[reg_pos]) if reg_pos >= 0 else False

        w_vol_t3 = compute_basket_vol_weight(daily_rets_df, t3, sig_dt, VOL_LOOKBACK, TARGET_VOL)
        w_vol_ew = compute_basket_vol_weight(daily_rets_df, all_secs, sig_dt, VOL_LOOKBACK, TARGET_VOL)

        w_reg_t3 = w_vol_t3 if regime_ok else 0.0
        w_reg_ew = w_vol_ew if regime_ok else 0.0

        targets: Dict[str, Dict[str, float]] = {
            "T1": {s: 1.0 / len(t1) for s in t1},
            "T2": {s: 1.0 / len(t2) for s in t2},
            "T3": {s: 1.0 / len(t3) for s in t3},
            "S0": {s: 1.0 / len(t3) for s in t3},
            "S1": {s: w_vol_t3 / len(t3) for s in t3},
            "S2": {s: w_reg_t3 / len(t3) for s in t3},
            "EW_S0": {s: 1.0 / len(all_secs) for s in all_secs},
            "EW_S1": {s: w_vol_ew / len(all_secs) for s in all_secs},
            "EW_S2": {s: w_reg_ew / len(all_secs) for s in all_secs},
        }

        rec: Dict[str, Any] = {
            "period": period,
            "year": step["year"],
            "split": split,
            "signal_date": str(sig_dt.date()),
            "fill_date": str(fill_dt.date()),
            "next_fill_date": str(next_fill_dt.date()),
            "regime_ok": regime_ok,
            "rf_m": rf_m,
            "spy_ret": spy_ret,
            "top3_sectors": ",".join(t3),
        }

        for name in strat_names:
            w_new = targets[name]
            gross_exp = sum(w_new.values())
            eq_gross = sum(w * sec_rets[s] for s, w in w_new.items())
            cash_yield = (1.0 - gross_exp) * rf_m

            to_1w, cost_1x = compute_rebalance_cost(
                prev_weights[name], prev_rets[name], prev_gross[name], w_new, ONE_WAY_1X
            )
            _, cost_2x = compute_rebalance_cost(
                prev_weights[name], prev_rets[name], prev_gross[name], w_new, ONE_WAY_2X
            )

            rec[f"{name}_net_1x_tbill"] = eq_gross + cash_yield - cost_1x
            rec[f"{name}_net_2x_tbill"] = eq_gross + cash_yield - cost_2x
            rec[f"{name}_net_1x_0cash"] = eq_gross - cost_1x
            rec[f"{name}_net_2x_0cash"] = eq_gross - cost_2x
            rec[f"{name}_turnover"] = to_1w
            rec[f"{name}_exposure"] = gross_exp

            if name in sector_contribs:
                for s, w in w_new.items():
                    sector_contribs[name].setdefault(s, []).append((period, w, sec_rets[s]))

            prev_weights[name] = w_new
            prev_rets[name] = {s: sec_rets[s] for s in w_new.keys()}
            prev_gross[name] = eq_gross + cash_yield

        rows.append(rec)

    return {"monthly_df": pd.DataFrame(rows), "sector_contribs": sector_contribs}


def compute_etf_metrics(
    returns_tbill: pd.Series,
    rf_series: pd.Series,
    years: pd.Series,
    returns_0cash: Optional[pd.Series] = None,
    turnover: Optional[pd.Series] = None,
    exposure: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    r = returns_tbill.to_numpy(dtype=float)
    rf = rf_series.to_numpy(dtype=float)
    n = int(len(r))
    if n == 0:
        return {}

    eq = np.cumprod(1.0 + r)
    cagr = (float(eq[-1]) ** (12.0 / n) - 1.0) if eq[-1] > 0 else -1.0
    sd_m = float(np.std(r, ddof=1)) if n >= 2 else 0.0
    ann_vol = sd_m * math.sqrt(12.0)

    ex = r - rf
    sd_ex = float(np.std(ex, ddof=1)) if n >= 2 else 0.0
    sharpe_tbill = (float(np.mean(ex)) / sd_ex) * math.sqrt(12.0) if sd_ex > 0 else 0.0

    # Transparency metrics (0% cash credit, rf=0)
    r0 = returns_0cash.to_numpy(dtype=float) if returns_0cash is not None else r
    eq0 = np.cumprod(1.0 + r0)
    cagr_0cash = (float(eq0[-1]) ** (12.0 / n) - 1.0) if eq0[-1] > 0 else -1.0
    sd0 = float(np.std(r0, ddof=1)) if n >= 2 else 0.0
    sharpe_rf0 = (float(np.mean(r0)) / sd0) * math.sqrt(12.0) if sd0 > 0 else 0.0

    eq_s = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(eq_s)
    max_dd = float(np.max((peak - eq_s) / peak))

    df_tmp = pd.DataFrame({"r": r, "year": years.to_numpy()})
    yr_rets = df_tmp.groupby("year")["r"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    total_yrs = int(len(yr_rets))
    pos_yrs = int((yr_rets > 0.0).sum())
    pct_pos = (100.0 * pos_yrs / total_yrs) if total_yrs > 0 else 0.0

    return {
        "n_months": n,
        "cagr": round(cagr, 5),
        "cagr_0cash": round(cagr_0cash, 5),
        "ann_vol": round(ann_vol, 5),
        "sharpe_tbill": round(sharpe_tbill, 3),
        "sharpe_rf0": round(sharpe_rf0, 3),
        "max_dd": round(max_dd, 5),
        "avg_monthly_turnover": round(float(turnover.mean()), 4) if turnover is not None else 0.0,
        "avg_exposure": round(float(exposure.mean()), 4) if exposure is not None else 1.0,
        "positive_years": pos_yrs,
        "total_years": total_yrs,
        "pct_positive_years": round(pct_pos, 2),
        "yearly_returns": {int(y): round(float(v), 5) for y, v in yr_rets.items()},
    }


def evaluate_stage1_dev(dev_df: pd.DataFrame) -> Dict[str, Any]:
    t_metrics: Dict[str, Any] = {}
    for k in ["T1", "T2", "T3", "EW_S0"]:
        t_metrics[k] = {
            "net_1x": compute_etf_metrics(
                dev_df[f"{k}_net_1x_tbill"], dev_df["rf_m"], dev_df["year"],
                dev_df[f"{k}_net_1x_0cash"], dev_df[f"{k}_turnover"], dev_df[f"{k}_exposure"],
            ),
            "net_2x": compute_etf_metrics(
                dev_df[f"{k}_net_2x_tbill"], dev_df["rf_m"], dev_df["year"],
                dev_df[f"{k}_net_2x_0cash"], dev_df[f"{k}_turnover"], dev_df[f"{k}_exposure"],
            ),
        }
    t_metrics["SPY"] = compute_etf_metrics(dev_df["spy_ret"], dev_df["rf_m"], dev_df["year"])
    t_metrics["TBILL"] = compute_etf_metrics(dev_df["rf_m"], dev_df["rf_m"], dev_df["year"])

    t3_cagr = t_metrics["T3"]["net_1x"]["cagr"]
    ew_cagr = t_metrics["EW_S0"]["net_1x"]["cagr"]
    excess_cagr = t3_cagr - ew_cagr

    spread = dev_df["T3_net_1x_tbill"] - dev_df["T1_net_1x_tbill"]
    mean_spread = float(spread.mean())
    sd_spread = float(spread.std(ddof=1)) if len(spread) >= 2 else 0.0
    t_spread = (mean_spread / (sd_spread / math.sqrt(len(spread)))) if sd_spread > 0 else 0.0

    c_excess = excess_cagr >= 0.01
    c_spread = mean_spread > 0.0
    return {
        "verdict": "PASS" if (c_excess and c_spread) else "FAIL",
        "checks": {
            "t3_minus_ew_cagr_1x_ge_1pct": bool(c_excess),
            "t3_minus_t1_mean_1x_gt_0": bool(c_spread),
        },
        "t3_cagr_1x": round(t3_cagr, 5),
        "ew_s0_cagr_1x": round(ew_cagr, 5),
        "excess_cagr_1x": round(excess_cagr, 5),
        "t3_minus_t1_monthly_mean_1x": round(mean_spread, 5),
        "t3_minus_t1_t_stat": round(t_spread, 3),
        "tercile_metrics": t_metrics,
    }


def _check_sector_concentration(
    ho_df: pd.DataFrame,
    strat: str,
    ctrl: str,
    contribs: Dict[str, List[Tuple[str, float, float]]],
) -> Dict[str, Any]:
    # (a) Drop single best calendar year
    yr_perf = ho_df.groupby("year")[f"{strat}_net_1x_tbill"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    best_year = int(yr_perf.idxmax())
    sub = ho_df[ho_df["year"] != best_year]
    m_ny = compute_etf_metrics(sub[f"{strat}_net_1x_tbill"], sub["rf_m"], sub["year"])
    c_ny = compute_etf_metrics(sub[f"{ctrl}_net_1x_tbill"], sub["rf_m"], sub["year"])
    ex_no_best_yr = m_ny["cagr"] - c_ny["cagr"]

    # (b) Drop single highest-contributing sector ETF on HOLDOUT
    ho_periods = set(ho_df["period"].tolist())
    ctrl_by_p = dict(zip(ho_df["period"], ho_df[f"{ctrl}_net_1x_tbill"]))
    rf_by_p = dict(zip(ho_df["period"], ho_df["rf_m"]))

    sec_totals: Dict[str, float] = {}
    sec_pmap: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for sym, entries in contribs.items():
        tot = 0.0
        for p, w, r_s in entries:
            if p in ho_periods:
                tot += w * (r_s - rf_by_p[p])
                sec_pmap.setdefault(sym, {})[p] = (w, r_s)
        if sym in sec_pmap:
            sec_totals[sym] = tot

    if sec_totals:
        top_sec = max(sec_totals.items(), key=lambda kv: kv[1])[0]
        top_contrib = sec_totals[top_sec]
        adj_rets = []
        for row in ho_df.itertuples():
            p = row.period
            base_r = getattr(row, f"{strat}_net_1x_tbill")
            if p in sec_pmap.get(top_sec, {}):
                w, r_s = sec_pmap[top_sec][p]
                adj_r = base_r - w * (r_s - ctrl_by_p[p])
            else:
                adj_r = base_r
            adj_rets.append(adj_r)
        m_ns = compute_etf_metrics(pd.Series(adj_rets), ho_df["rf_m"], ho_df["year"])
        c_full = compute_etf_metrics(ho_df[f"{ctrl}_net_1x_tbill"], ho_df["rf_m"], ho_df["year"])
        ex_no_top_sec = m_ns["cagr"] - c_full["cagr"]
    else:
        top_sec, top_contrib, ex_no_top_sec = None, 0.0, 0.0

    return {
        "best_year_dropped": best_year,
        "excess_cagr_without_best_year": round(ex_no_best_yr, 5),
        "survives_drop_best_year": bool(ex_no_best_yr > 0.0),
        "top_sector_dropped": top_sec,
        "top_sector_excess_contrib": round(top_contrib, 5),
        "excess_cagr_without_top_sector": round(ex_no_top_sec, 5),
        "survives_drop_top_sector": bool(ex_no_top_sec > 0.0),
    }


def evaluate_stage2_graduation(
    mdf: pd.DataFrame,
    sector_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]],
) -> Dict[str, Any]:
    dev_df = mdf[mdf["split"] == "DEV"].copy()
    ho_df = mdf[mdf["split"] == "HOLDOUT"].copy()

    spy_ho = compute_etf_metrics(ho_df["spy_ret"], ho_df["rf_m"], ho_df["year"])
    tbill_ho = compute_etf_metrics(ho_df["rf_m"], ho_df["rf_m"], ho_df["year"])

    candidates: Dict[str, Any] = {}
    any_pass = False

    for strat, ctrl in [("S1", "EW_S1"), ("S2", "EW_S2")]:
        dev_1x = compute_etf_metrics(dev_df[f"{strat}_net_1x_tbill"], dev_df["rf_m"], dev_df["year"], dev_df[f"{strat}_net_1x_0cash"], dev_df[f"{strat}_turnover"], dev_df[f"{strat}_exposure"])
        dev_2x = compute_etf_metrics(dev_df[f"{strat}_net_2x_tbill"], dev_df["rf_m"], dev_df["year"], dev_df[f"{strat}_net_2x_0cash"], dev_df[f"{strat}_turnover"], dev_df[f"{strat}_exposure"])
        c_dev_1x = compute_etf_metrics(dev_df[f"{ctrl}_net_1x_tbill"], dev_df["rf_m"], dev_df["year"], dev_df[f"{ctrl}_net_1x_0cash"], dev_df[f"{ctrl}_turnover"], dev_df[f"{ctrl}_exposure"])
        c_dev_2x = compute_etf_metrics(dev_df[f"{ctrl}_net_2x_tbill"], dev_df["rf_m"], dev_df["year"], dev_df[f"{ctrl}_net_2x_0cash"], dev_df[f"{ctrl}_turnover"], dev_df[f"{ctrl}_exposure"])

        ho_1x = compute_etf_metrics(ho_df[f"{strat}_net_1x_tbill"], ho_df["rf_m"], ho_df["year"], ho_df[f"{strat}_net_1x_0cash"], ho_df[f"{strat}_turnover"], ho_df[f"{strat}_exposure"])
        ho_2x = compute_etf_metrics(ho_df[f"{strat}_net_2x_tbill"], ho_df["rf_m"], ho_df["year"], ho_df[f"{strat}_net_2x_0cash"], ho_df[f"{strat}_turnover"], ho_df[f"{strat}_exposure"])
        c_ho_1x = compute_etf_metrics(ho_df[f"{ctrl}_net_1x_tbill"], ho_df["rf_m"], ho_df["year"], ho_df[f"{ctrl}_net_1x_0cash"], ho_df[f"{ctrl}_turnover"], ho_df[f"{ctrl}_exposure"])
        c_ho_2x = compute_etf_metrics(ho_df[f"{ctrl}_net_2x_tbill"], ho_df["rf_m"], ho_df["year"], ho_df[f"{ctrl}_net_2x_0cash"], ho_df[f"{ctrl}_turnover"], ho_df[f"{ctrl}_exposure"])

        conc = _check_sector_concentration(ho_df, strat, ctrl, sector_contribs[strat])

        dev_ex_1x = dev_1x["cagr"] - c_dev_1x["cagr"]
        dev_ex_2x = dev_2x["cagr"] - c_dev_2x["cagr"]
        ho_ex_1x = ho_1x["cagr"] - c_ho_1x["cagr"]
        ho_ex_2x = ho_2x["cagr"] - c_ho_2x["cagr"]

        c1 = (ho_1x["sharpe_tbill"] >= 0.50) and (ho_2x["sharpe_tbill"] >= 0.45)
        c2 = (dev_ex_1x >= 0.01) and (dev_ex_2x > 0.0) and (ho_ex_1x >= 0.01) and (ho_ex_2x > 0.0)
        c3 = (ho_1x["sharpe_tbill"] > spy_ho["sharpe_tbill"]) or (
            (spy_ho["max_dd"] - ho_1x["max_dd"] >= 0.05) and (ho_1x["cagr"] > tbill_ho["cagr"])
        )
        c4 = (ho_1x["pct_positive_years"] >= 60.0) and conc["survives_drop_best_year"] and conc["survives_drop_top_sector"]
        c5 = ho_1x["max_dd"] <= 0.25

        checks = {
            "c1_sharpe_tbill_1x_ge_050_and_2x_ge_045": bool(c1),
            "c2_matched_excess_dev_and_holdout": bool(c2),
            "c3_beats_spy_sharpe_or_dd_5pp": bool(c3),
            "c4_pos_years_ge_60pct_and_concentration": bool(c4),
            "c5_max_dd_le_25pct": bool(c5),
        }
        passed = all(checks.values())
        if passed:
            any_pass = True

        candidates[strat] = {
            "matched_control": ctrl,
            "passed": passed,
            "checks": checks,
            "dev_excess_cagr_1x": round(dev_ex_1x, 5),
            "dev_excess_cagr_2x": round(dev_ex_2x, 5),
            "ho_excess_cagr_1x": round(ho_ex_1x, 5),
            "ho_excess_cagr_2x": round(ho_ex_2x, 5),
            "concentration": conc,
        }

    return {"verdict": "PASS" if any_pass else "FAIL", "candidates": candidates}


def _collect_split_metrics(df_sub: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ["S0", "EW_S0", "S1", "EW_S1", "S2", "EW_S2"]:
        out[k] = {
            "net_1x": compute_etf_metrics(
                df_sub[f"{k}_net_1x_tbill"], df_sub["rf_m"], df_sub["year"],
                df_sub[f"{k}_net_1x_0cash"], df_sub[f"{k}_turnover"], df_sub[f"{k}_exposure"],
            ),
            "net_2x": compute_etf_metrics(
                df_sub[f"{k}_net_2x_tbill"], df_sub["rf_m"], df_sub["year"],
                df_sub[f"{k}_net_2x_0cash"], df_sub[f"{k}_turnover"], df_sub[f"{k}_exposure"],
            ),
        }
    out["SPY"] = compute_etf_metrics(df_sub["spy_ret"], df_sub["rf_m"], df_sub["year"])
    out["TBILL"] = compute_etf_metrics(df_sub["rf_m"], df_sub["rf_m"], df_sub["year"])
    return out


def _fmt_pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _fmt_dd(v: float) -> str:
    return f"-{v * 100:.2f}%"


def _table_terciles(tm: Dict[str, Any], cost_key: str = "net_1x") -> str:
    lines = [
        "| Bucket | Months | CAGR (TBill cash) | Ann Vol | Sharpe (excess ^IRX) | Sharpe (rf=0) | Max DD | 1-Way TO/mo | +Years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in ["T1", "T2", "T3", "EW_S0", "SPY", "TBILL"]:
        m = tm[k] if k in ("SPY", "TBILL") else tm[k][cost_key]
        py = f"{m['positive_years']}/{m['total_years']} ({m['pct_positive_years']:.0f}%)"
        lines.append(
            f"| **{k}** | {m['n_months']} | {_fmt_pct(m['cagr'])} | {m['ann_vol']*100:.2f}% | "
            f"{m['sharpe_tbill']:+.3f} | {m['sharpe_rf0']:+.3f} | {_fmt_dd(m['max_dd'])} | "
            f"{m['avg_monthly_turnover']*100:.1f}% | {py} |"
        )
    return "\n".join(lines)


def _table_strategies(sm: Dict[str, Any], cost_key: str = "net_1x") -> str:
    lines = [
        "| Series | Mos | CAGR (TBill) | CAGR (0% cash) | Ann Vol | Sharpe (excess ^IRX) | Sharpe (rf=0) | Max DD | TO/mo | Avg Exp | +Years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in ["S0", "EW_S0", "S1", "EW_S1", "S2", "EW_S2", "SPY", "TBILL"]:
        m = sm[k] if k in ("SPY", "TBILL") else sm[k][cost_key]
        py = f"{m['positive_years']}/{m['total_years']} ({m['pct_positive_years']:.0f}%)"
        lines.append(
            f"| **{k}** | {m['n_months']} | {_fmt_pct(m['cagr'])} | {_fmt_pct(m['cagr_0cash'])} | "
            f"{m['ann_vol']*100:.2f}% | {m['sharpe_tbill']:+.3f} | {m['sharpe_rf0']:+.3f} | "
            f"{_fmt_dd(m['max_dd'])} | {m['avg_monthly_turnover']*100:.1f}% | {m['avg_exposure']*100:.1f}% | {py} |"
        )
    return "\n".join(lines)


def _table_yearly(mdf: pd.DataFrame) -> str:
    lines = [
        "| Year | Split | S0 | EW_S0 | S1 | EW_S1 | S2 | EW_S2 | SPY | TBILL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for y in sorted(mdf["year"].unique()):
        sub = mdf[mdf["year"] == y]
        sp = sub["split"].iloc[0]
        vals = [
            _fmt_pct(float(np.prod(1.0 + sub[f"{k}_net_1x_tbill"].to_numpy()) - 1.0))
            for k in ["S0", "EW_S0", "S1", "EW_S1", "S2", "EW_S2"]
        ]
        vals.append(_fmt_pct(float(np.prod(1.0 + sub["spy_ret"].to_numpy()) - 1.0)))
        vals.append(_fmt_pct(float(np.prod(1.0 + sub["rf_m"].to_numpy()) - 1.0)))
        lines.append(f"| {y} | {sp} | " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(summary: Dict[str, Any], mdf: pd.DataFrame, out_path: Path) -> None:
    s1 = summary["stage1_dev"]
    s2 = summary["stage2_graduation"]
    verdict = summary["overall_verdict"]

    lines = [
        "# Study `T004` Report: US Sector ETF Rotation (`2000–2026`, Zero Survivorship Bias)",
        "",
        f"## Final Pre-Registered Verdict: **{verdict}**",
        "",
        f"- **Stage 1 Kill Gate (DEV `2000–2015`, 192 mos):** **{s1['verdict']}** "
        f"(`T3 - EW_S0` 1× CAGR = `{_fmt_pct(s1['excess_cagr_1x'])}`, "
        f"`T3 - T1` monthly mean = `{_fmt_pct(s1['t3_minus_t1_monthly_mean_1x'])}`, `t = {s1['t3_minus_t1_t_stat']:+.2f}`)",
        f"- **Stage 2 Graduation Gate (HOLDOUT `2016–2026`, 130 mos, Single Touch):** **{s2['verdict'] if s2 else 'SKIPPED (Stage 1 FAIL — HOLDOUT SEALED)'}**",
        "",
        "### Methodological Disclosures",
        "- **Zero Survivorship Bias:** Fixed 9 original SPDR Select Sector ETFs (`XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY`), all launched simultaneously on `1998-12-22`, continuously traded through `2026-11-20`.",
        "- **Sharpe & Cash Convention:** Uninvested cash earns strictly causal `^IRX` 13-week T-bill yield; `Sharpe_tbill` uses true excess returns (`r_p - r_f`) identically across `S0..S2`, `EW_S0..EW_S2`, and `SPY`. Zero-cash CAGR and `rf=0` Sharpe are also shown.",
        "- **Costs:** `1× = 10 bps` round-trip (`5 bps` one-way); `2× = 20 bps` round-trip (`10 bps` one-way) on exact drifted-weight turnover.",
        "- **Statistical Power:** 130 monthly HOLDOUT observations (10.83 yrs) give Sharpe SE ~`±0.31`; full 26-year span = 322 monthly observations.",
        "",
    ]

    if s2 is not None:
        lines += [
            "### Stage 2 Pre-Registered Graduation Checklist (HOLDOUT `2016–2026`, Net of Costs)",
            "",
            "| Candidate | Matched Ctrl | C1: Sharpe 1x>=0.50 & 2x>=0.45 | C2: Matched Excess DEV & HO | C3: Beats SPY Sharpe or -5pp DD | C4: >=60% +Yrs & Conc. | C5: MaxDD<=25% | Gate |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        fb = lambda b: "YES" if b else "NO"
        for strat in ("S1", "S2"):
            cand = s2["candidates"][strat]
            c = cand["checks"]
            lines.append(
                f"| **{strat}** | `{cand['matched_control']}` | {fb(c['c1_sharpe_tbill_1x_ge_050_and_2x_ge_045'])} | "
                f"{fb(c['c2_matched_excess_dev_and_holdout'])} | {fb(c['c3_beats_spy_sharpe_or_dd_5pp'])} | "
                f"{fb(c['c4_pos_years_ge_60pct_and_concentration'])} | {fb(c['c5_max_dd_le_25pct'])} | "
                f"**{'PASS' if cand['passed'] else 'FAIL'}** |"
            )
        lines += ["", "#### Matched-Overlay Excess CAGR & Concentration Details", ""]
        for strat in ("S1", "S2"):
            cand = s2["candidates"][strat]
            conc = cand["concentration"]
            lines.append(
                f"- **`{strat}` vs `{cand['matched_control']}`:** "
                f"DEV excess = `{_fmt_pct(cand['dev_excess_cagr_1x'])}` (1×) / `{_fmt_pct(cand['dev_excess_cagr_2x'])}` (2×); "
                f"HOLDOUT excess = `{_fmt_pct(cand['ho_excess_cagr_1x'])}` (1×) / `{_fmt_pct(cand['ho_excess_cagr_2x'])}` (2×); "
                f"Drop best year (`{conc['best_year_dropped']}`): `{_fmt_pct(conc['excess_cagr_without_best_year'])}`; "
                f"Drop top sector (`{conc['top_sector_dropped']}`): `{_fmt_pct(conc['excess_cagr_without_top_sector'])}`."
            )

    lines += [
        "",
        "---",
        "",
        "## Stage 1 — DEV Tercile Event Study (`2000–2015`, 192 mos, 1× Costs)",
        "",
        _table_terciles(s1["tercile_metrics"], "net_1x"),
        "",
    ]

    if summary["stage2_metrics"] is not None:
        for split_name, label in [
            ("HOLDOUT", "HOLDOUT (`2016–2026`, 130 mos)"),
            ("DEV", "DEV (`2000–2015`, 192 mos)"),
            ("FULL", "FULL 26-YEAR SPAN (`2000–2026`, 322 mos)"),
            ("HOLDOUT_2022_2026", "Informational Sub-Window (`2022–2026`, 58 mos)"),
        ]:
            lines += [
                f"## {label} — 1× Costs (10 bps RT)",
                "",
                _table_strategies(summary["stage2_metrics"][split_name], "net_1x"),
                "",
                f"### {label} — 2× Stress Costs (20 bps RT)",
                "",
                _table_strategies(summary["stage2_metrics"][split_name], "net_2x"),
                "",
            ]
        lines += [
            "## HOLDOUT Tercile Breakdown (`2016–2026`, 1× Costs, Informational)",
            "",
            _table_terciles(summary["holdout_terciles"], "net_1x"),
            "",
            "## Calendar-Year Returns (`2000–2026`, 1× Costs, T-Bill Cash)",
            "",
            _table_yearly(mdf),
            "",
        ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_t004(stage: int = 1) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sector_dfs, spy_df, irx_df = load_etf_dataset()
    max_period = DEV_END if stage == 1 else None

    sim = simulate_sector_rotation(sector_dfs, spy_df, irx_df, max_period=max_period)
    mdf = sim["monthly_df"]
    dev_df = mdf[mdf["split"] == "DEV"].copy()
    s1 = evaluate_stage1_dev(dev_df)

    if stage == 1:
        print("\n=== T004 STAGE 1: DEV-ONLY TERCILE EVENT STUDY (2000-01 .. 2015-12; HOLDOUT SEALED) ===")
        print(_table_terciles(s1["tercile_metrics"], "net_1x"))
        print(
            f"\nT004 Stage 1 Gate: {s1['verdict']} | "
            f"T3-EW_S0 1x CAGR = {_fmt_pct(s1['excess_cagr_1x'])} (need >=+1.00%) | "
            f"T3-T1 monthly mean = {_fmt_pct(s1['t3_minus_t1_monthly_mean_1x'])} (t={s1['t3_minus_t1_t_stat']:+.2f})"
        )
        summary = {
            "stage_executed": 1,
            "overall_verdict": "PENDING_STAGE_2" if s1["verdict"] == "PASS" else "FAIL",
            "stage1_dev": s1,
            "stage2_metrics": None,
            "stage2_graduation": None,
            "holdout_terciles": None,
        }
        if s1["verdict"] == "FAIL":
            # Stage 1 kill -> write report immediately with HOLDOUT sealed!
            mdf.to_csv(OUT_DIR / "monthly_returns_dev.csv", index=False)
            (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            write_report(summary, mdf, OUT_DIR / "REPORT.md")
            print(f"Stage 1 FAILED on DEV. HOLDOUT remains sealed. Report written to {OUT_DIR / 'REPORT.md'}")
        return summary

    # Stage 2: Single final HOLDOUT evaluation
    ho_df = mdf[mdf["split"] == "HOLDOUT"].copy()
    ho_recent = mdf[mdf["period"] >= "2022-01"].copy()

    ho_terciles = {}
    for k in ["T1", "T2", "T3", "EW_S0"]:
        ho_terciles[k] = {
            "net_1x": compute_etf_metrics(
                ho_df[f"{k}_net_1x_tbill"], ho_df["rf_m"], ho_df["year"],
                ho_df[f"{k}_net_1x_0cash"], ho_df[f"{k}_turnover"], ho_df[f"{k}_exposure"],
            ),
            "net_2x": compute_etf_metrics(
                ho_df[f"{k}_net_2x_tbill"], ho_df["rf_m"], ho_df["year"],
                ho_df[f"{k}_net_2x_0cash"], ho_df[f"{k}_turnover"], ho_df[f"{k}_exposure"],
            ),
        }
    ho_terciles["SPY"] = compute_etf_metrics(ho_df["spy_ret"], ho_df["rf_m"], ho_df["year"])
    ho_terciles["TBILL"] = compute_etf_metrics(ho_df["rf_m"], ho_df["rf_m"], ho_df["year"])

    s2_grad = evaluate_stage2_graduation(mdf, sim["sector_contribs"])
    overall = "PASS" if (s1["verdict"] == "PASS" and s2_grad["verdict"] == "PASS") else "FAIL"

    summary = {
        "stage_executed": 2,
        "overall_verdict": overall,
        "stage1_dev": s1,
        "stage2_graduation": s2_grad,
        "stage2_metrics": {
            "DEV": _collect_split_metrics(dev_df),
            "HOLDOUT": _collect_split_metrics(ho_df),
            "FULL": _collect_split_metrics(mdf),
            "HOLDOUT_2022_2026": _collect_split_metrics(ho_recent),
        },
        "holdout_terciles": ho_terciles,
    }

    mdf.to_csv(OUT_DIR / "monthly_returns.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(summary, mdf, OUT_DIR / "REPORT.md")

    print(f"\n=== T004 FINAL VERDICT: {overall} ===")
    print(f"  Stage 1 (DEV 2000-2015)    : {s1['verdict']}")
    print(f"  Stage 2 (HOLDOUT 2016-2026): {s2_grad['verdict']}")
    print(f"Artifacts written to         : {OUT_DIR}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-registered T004 US Sector ETF Rotation harness.")
    parser.add_argument("--stage", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    run_t004(stage=args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
