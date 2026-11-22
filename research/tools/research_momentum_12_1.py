"""
Pre-Registered Study T003: 12-1 Month Cross-Sectional Momentum + Vol-Target /
Regime Overlay (Free-Data Asymmetric Kill Test).

Implements research_out/phase1/PREREG_T003_MOMENTUM_12_1.md (Rev 2) strictly:
  - Stage 1 (`--stage 1`): DEV-only (2016-01..2021-12) quintile event study kill gate.
    HOLDOUT is NEVER read or evaluated in Stage 1.
  - Stage 2 (`--stage 2`): Executed ONCE only if Stage 1 clears the DEV kill gate.
    Evaluates M0, M1, M2 against matched overlay controls EW_M0, EW_M1, EW_M2
    and market benchmark (SPY / ^NSEI) at 1x and 2x costs, then writes REPORT.md,
    summary.json, and monthly_returns.csv immediately.
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
DATA_DIR = ROOT / "data" / "research_daily"
OUT_DIR = ROOT / "research" / "phase1" / "T003"
US_WATCHLIST = ROOT / "config" / "us_watchlist.txt"
NSE_WATCHLIST = ROOT / "config" / "nse_watchlist.txt"

DEV_START = "2016-01"
DEV_END = "2021-12"
HOLDOUT_START = "2022-01"

MOM_LONG = 252
MOM_SKIP = 21
VOL_LOOKBACK = 60
TARGET_VOL = 0.15
REGIME_SMA = 200
RF_INFO_ANNUAL = 0.04

MARKET_CONFIG = {
    "US": {
        "watchlist": US_WATCHLIST,
        "benchmark": "SPY",
        "one_way_cost_1x": 0.0010,  # 10 bps one-way = 20 bps round-trip
        "one_way_cost_2x": 0.0020,  # 20 bps one-way = 40 bps round-trip
    },
    "NSE": {
        "watchlist": NSE_WATCHLIST,
        "benchmark": "^NSEI",
        "one_way_cost_1x": 0.00175,  # 17.5 bps one-way = 35 bps round-trip
        "one_way_cost_2x": 0.00350,  # 35 bps one-way = 70 bps round-trip
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("research_t003")


# ---------------------------------------------------------------------------
# Pure Causal Math Helpers (Unit-Tested for Zero Lookahead)
# ---------------------------------------------------------------------------

def load_watchlist(path: Path) -> List[str]:
    syms: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if line:
                syms.append(line)
    return syms


def load_market_data(market: str) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    cfg = MARKET_CONFIG[market]
    bench_fp = DATA_DIR / f"{cfg['benchmark']}_1d.pkl"
    if not bench_fp.exists():
        raise FileNotFoundError(f"Missing benchmark data: {bench_fp}")
    bench_df = pd.read_pickle(bench_fp).sort_index()

    universe_dfs: Dict[str, pd.DataFrame] = {}
    for sym in load_watchlist(cfg["watchlist"]):
        fp = DATA_DIR / f"{sym}_1d.pkl"
        if fp.exists():
            df = pd.read_pickle(fp).sort_index()
            if len(df) >= 500:
                universe_dfs[sym] = df
    return universe_dfs, bench_df


def compute_mom_12_1_panel(
    universe_dfs: Dict[str, pd.DataFrame],
    long_lb: int = MOM_LONG,
    skip_lb: int = MOM_SKIP,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Strictly causal 12-1m momentum panel:
      Mom_12_1(t) = Close[t - 21] / Close[t - 252] - 1
    Returns (mom_df, close_df, open_df) aligned on union sorted date index.
    """
    mom_map: Dict[str, pd.Series] = {}
    close_map: Dict[str, pd.Series] = {}
    open_map: Dict[str, pd.Series] = {}

    for sym, df in universe_dfs.items():
        if df is None or df.empty:
            continue
        d = df.sort_index()
        c = d["Close"].astype(float)
        o = d["Open"].astype(float)
        mom_map[sym] = c.shift(skip_lb) / c.shift(long_lb) - 1.0
        close_map[sym] = c
        open_map[sym] = o

    mom_df = pd.DataFrame(mom_map).sort_index()
    close_df = pd.DataFrame(close_map).sort_index()
    open_df = pd.DataFrame(open_map).sort_index()
    return mom_df, close_df, open_df


def compute_benchmark_regime(bench_df: pd.DataFrame, sma_len: int = REGIME_SMA) -> pd.Series:
    """Strictly causal regime: Close[t] > SMA200[t]."""
    close = bench_df["Close"].sort_index().astype(float)
    sma = close.rolling(sma_len, min_periods=sma_len).mean()
    return (close > sma).fillna(False).astype(bool)


def build_monthly_schedule(bench_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build completed monthly open-to-open holding periods strictly from benchmark dates:
      - signal_date: last benchmark trading day of month m
      - fill_date: first benchmark trading day of month m+1 (entry Open)
      - next_fill_date: first benchmark trading day of month m+2 (exit Open)
      - period: YYYY-MM of fill_date (the holding month)
    Only completed periods (having both fill_date and next_fill_date) are returned.
    """
    idx = bench_df.index.sort_values().unique()
    s = pd.Series(idx, index=idx)
    month_ends = s.groupby([idx.year, idx.month]).last().tolist()

    schedule: List[Dict[str, Any]] = []
    for k in range(len(month_ends) - 1):
        sig_dt = month_ends[k]
        next_sig_dt = month_ends[k + 1]

        fill_pos = idx.searchsorted(sig_dt, side="right")
        next_fill_pos = idx.searchsorted(next_sig_dt, side="right")
        if fill_pos >= len(idx) or next_fill_pos >= len(idx):
            continue

        fill_dt = idx[fill_pos]
        next_fill_dt = idx[next_fill_pos]
        period = f"{fill_dt.year:04d}-{fill_dt.month:02d}"
        schedule.append(
            {
                "period": period,
                "year": int(fill_dt.year),
                "signal_date": sig_dt,
                "fill_date": fill_dt,
                "next_fill_date": next_fill_dt,
            }
        )
    return schedule


def compute_basket_vol_weight(
    daily_rets_df: pd.DataFrame,
    symbols: List[str],
    signal_date: pd.Timestamp,
    vol_lookback: int = VOL_LOOKBACK,
    target_vol: float = TARGET_VOL,
) -> float:
    """
    Strictly causal portfolio vol-target weight using trailing 60 trading days <= signal_date:
      w = min(1.0, target_vol / annualized_sigma_60d)
    """
    if not symbols:
        return 0.0
    sub = daily_rets_df.loc[:signal_date, symbols].tail(vol_lookback)
    if len(sub) < 20:
        return 1.0
    basket_ret = sub.mean(axis=1, skipna=True).dropna()
    if len(basket_ret) < 20:
        return 1.0
    sigma = float(basket_ret.std(ddof=1) * math.sqrt(252.0))
    if not math.isfinite(sigma) or sigma <= 0:
        return 1.0
    return float(min(1.0, target_vol / sigma))


def compute_rebalance_cost(
    prev_weights: Dict[str, float],
    prev_stock_rets: Dict[str, float],
    prev_port_gross_ret: float,
    new_weights: Dict[str, float],
    one_way_cost: float,
) -> Tuple[float, float]:
    """
    Compute exact drifted weights at rebalance and turnover cost:
      traded_notional = sum_s |w_new[s] - w_drifted[s]|
      one_way_turnover = 0.5 * traded_notional
      cost = traded_notional * one_way_cost
    Returns (one_way_turnover, cost).
    """
    drifted: Dict[str, float] = {}
    denom = 1.0 + prev_port_gross_ret
    if denom > 0:
        for s, w in prev_weights.items():
            r_s = prev_stock_rets.get(s, 0.0)
            drifted[s] = w * (1.0 + r_s) / denom

    all_syms = set(drifted.keys()) | set(new_weights.keys())
    traded_notional = sum(abs(new_weights.get(s, 0.0) - drifted.get(s, 0.0)) for s in all_syms)
    one_way_turnover = 0.5 * traded_notional
    cost = traded_notional * one_way_cost
    return float(one_way_turnover), float(cost)


def _lookup_open_on_or_after(series: pd.Series, dt: pd.Timestamp, max_days: int = 4) -> Optional[float]:
    valid = series.dropna()
    if valid.empty:
        return None
    pos = valid.index.searchsorted(dt, side="left")
    if pos >= len(valid):
        return None
    found_dt = valid.index[pos]
    if (found_dt - dt).days > max_days:
        return None
    val = float(valid.iloc[pos])
    return val if val > 0 else None


def _lookup_open_on_or_before(series: pd.Series, dt: pd.Timestamp, max_days: int = 4) -> Optional[float]:
    valid = series.dropna()
    if valid.empty:
        return None
    pos = valid.index.searchsorted(dt, side="right") - 1
    if pos < 0:
        return None
    found_dt = valid.index[pos]
    if (dt - found_dt).days > max_days:
        return None
    val = float(valid.iloc[pos])
    return val if val > 0 else None


def assign_quintiles(mom_scores: Dict[str, float]) -> List[List[str]]:
    """Deterministic sort ascending by (score, symbol) and split into 5 quintiles Q1..Q5."""
    ordered = [s for s, _ in sorted(mom_scores.items(), key=lambda kv: (kv[1], kv[0]))]
    if len(ordered) < 5:
        return [[] for _ in range(5)]
    return [list(arr) for arr in np.array_split(ordered, 5)]


# ---------------------------------------------------------------------------
# Simulation Engine (Stage 1 Event Study & Stage 2 Portfolio Backtest)
# ---------------------------------------------------------------------------

def simulate_monthly_strategies(
    market: str,
    universe_dfs: Dict[str, pd.DataFrame],
    bench_df: pd.DataFrame,
    max_period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run monthly open-to-open simulation for:
      - Quintiles Q1..Q5
      - Portfolio variants M0, M1, M2
      - Matched overlay controls EW_M0, EW_M1, EW_M2
      - Market benchmark (SPY / ^NSEI)
    If `max_period` is provided (e.g. '2021-12' for Stage 1 DEV-only),
    periods strictly after `max_period` are NEVER computed or read.
    """
    mcfg = MARKET_CONFIG[market]
    c1 = mcfg["one_way_cost_1x"]
    c2 = mcfg["one_way_cost_2x"]

    mom_df, close_df, open_df = compute_mom_12_1_panel(universe_dfs)
    daily_rets_df = close_df.pct_change()
    regime_series = compute_benchmark_regime(bench_df)
    bench_open = bench_df["Open"].sort_index().astype(float)

    schedule = build_monthly_schedule(bench_df)
    schedule = [p for p in schedule if p["period"] >= DEV_START]
    if max_period is not None:
        schedule = [p for p in schedule if p["period"] <= max_period]

    strategy_names = ["Q1", "Q2", "Q3", "Q4", "Q5", "M0", "M1", "M2", "EW_M0", "EW_M1", "EW_M2"]
    prev_weights: Dict[str, Dict[str, float]] = {k: {} for k in strategy_names}
    prev_rets: Dict[str, Dict[str, float]] = {k: {} for k in strategy_names}
    prev_gross: Dict[str, float] = {k: 0.0 for k in strategy_names}

    monthly_rows: List[Dict[str, Any]] = []
    stock_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]] = {
        k: {} for k in ["M0", "M1", "M2"]
    }  # strat -> sym -> list of (period, weight, stock_ret)

    for step in schedule:
        period = step["period"]
        sig_dt = step["signal_date"]
        fill_dt = step["fill_date"]
        next_fill_dt = step["next_fill_date"]
        split = "DEV" if period <= DEV_END else "HOLDOUT"

        # Benchmark open-to-open return
        b_o0 = _lookup_open_on_or_after(bench_open, fill_dt)
        b_o1 = _lookup_open_on_or_before(bench_open, next_fill_dt)
        if b_o0 is None or b_o1 is None:
            continue
        bench_ret = (b_o1 / b_o0) - 1.0

        # Eligible stocks on sig_dt: strictly causal valid Mom_12_1 on sig_dt
        if sig_dt not in mom_df.index:
            continue
        mom_row = mom_df.loc[sig_dt].dropna()

        eligible_scores: Dict[str, float] = {}
        period_stock_rets: Dict[str, float] = {}
        for sym, score in mom_row.items():
            if not math.isfinite(float(score)):
                continue
            o0 = _lookup_open_on_or_after(open_df[sym], fill_dt)
            if o0 is None:
                continue
            o1 = _lookup_open_on_or_before(open_df[sym], next_fill_dt)
            if o1 is None:
                continue
            eligible_scores[sym] = float(score)
            period_stock_rets[sym] = (o1 / o0) - 1.0

        if len(eligible_scores) < 25:
            continue

        quintiles = assign_quintiles(eligible_scores)
        q_map = {f"Q{i+1}": quintiles[i] for i in range(5)}
        all_eligible = sorted(eligible_scores.keys())
        q5_syms = q_map["Q5"]

        # Regime at sig_dt (strictly <= sig_dt)
        reg_pos = regime_series.index.searchsorted(sig_dt, side="right") - 1
        regime_ok = bool(regime_series.iloc[reg_pos]) if reg_pos >= 0 else False

        # Vol-target weights (strictly <= sig_dt)
        w_vol_q5 = compute_basket_vol_weight(daily_rets_df, q5_syms, sig_dt)
        w_vol_ew = compute_basket_vol_weight(daily_rets_df, all_eligible, sig_dt)

        w_reg_q5 = w_vol_q5 if regime_ok else 0.0
        w_reg_ew = w_vol_ew if regime_ok else 0.0

        target_weights: Dict[str, Dict[str, float]] = {}
        for qn, syms in q_map.items():
            target_weights[qn] = {s: 1.0 / len(syms) for s in syms}

        target_weights["M0"] = {s: 1.0 / len(q5_syms) for s in q5_syms}
        target_weights["M1"] = {s: w_vol_q5 / len(q5_syms) for s in q5_syms}
        target_weights["M2"] = {s: w_reg_q5 / len(q5_syms) for s in q5_syms}

        target_weights["EW_M0"] = {s: 1.0 / len(all_eligible) for s in all_eligible}
        target_weights["EW_M1"] = {s: w_vol_ew / len(all_eligible) for s in all_eligible}
        target_weights["EW_M2"] = {s: w_reg_ew / len(all_eligible) for s in all_eligible}

        row_rec: Dict[str, Any] = {
            "market": market,
            "period": period,
            "year": step["year"],
            "split": split,
            "signal_date": str(sig_dt.date()),
            "fill_date": str(fill_dt.date()),
            "next_fill_date": str(next_fill_dt.date()),
            "n_eligible": len(all_eligible),
            "n_q5": len(q5_syms),
            "regime_ok": regime_ok,
            "bench_ret": bench_ret,
        }

        for name in strategy_names:
            w_new = target_weights[name]
            gross_exp = sum(w_new.values())
            gross_ret = sum(w * period_stock_rets[s] for s, w in w_new.items())

            to_1w, cost_1x = compute_rebalance_cost(
                prev_weights[name], prev_rets[name], prev_gross[name], w_new, c1
            )
            _, cost_2x = compute_rebalance_cost(
                prev_weights[name], prev_rets[name], prev_gross[name], w_new, c2
            )

            net_1x = gross_ret - cost_1x
            net_2x = gross_ret - cost_2x

            row_rec[f"{name}_gross"] = gross_ret
            row_rec[f"{name}_net_1x"] = net_1x
            row_rec[f"{name}_net_2x"] = net_2x
            row_rec[f"{name}_turnover"] = to_1w
            row_rec[f"{name}_exposure"] = gross_exp

            if name in stock_contribs:
                for s, w in w_new.items():
                    stock_contribs[name].setdefault(s, []).append((period, w, period_stock_rets[s]))

            prev_weights[name] = w_new
            prev_rets[name] = {s: period_stock_rets[s] for s in w_new.keys()}
            prev_gross[name] = gross_ret

        monthly_rows.append(row_rec)

    return {
        "monthly_df": pd.DataFrame(monthly_rows),
        "stock_contribs": stock_contribs,
    }


# ---------------------------------------------------------------------------
# Metrics, Stage 1 Kill Gate, and Stage 2 Graduation Gate
# ---------------------------------------------------------------------------

def compute_series_metrics(
    returns: pd.Series,
    years: pd.Series,
    turnover: Optional[pd.Series] = None,
    exposure: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    r = returns.dropna().to_numpy(dtype=float)
    n = int(len(r))
    if n == 0:
        return {
            "n_months": 0,
            "cagr": 0.0,
            "ann_vol": 0.0,
            "sharpe_rf0": 0.0,
            "sharpe_rf4": 0.0,
            "max_dd": 0.0,
            "avg_monthly_turnover": 0.0,
            "avg_exposure": 0.0,
            "pct_positive_years": 0.0,
            "positive_years": 0,
            "total_years": 0,
            "yearly_returns": {},
        }

    eq = np.cumprod(1.0 + r)
    total_mult = float(eq[-1])
    cagr = (total_mult ** (12.0 / n) - 1.0) if total_mult > 0 else -1.0
    sd_m = float(np.std(r, ddof=1)) if n >= 2 else 0.0
    ann_vol = sd_m * math.sqrt(12.0)
    mean_m = float(np.mean(r))
    sharpe_rf0 = (mean_m / sd_m) * math.sqrt(12.0) if sd_m > 0 else 0.0

    # Informational Sharpe_rf4: cash weight earns 4%/yr, excess over 4%/yr
    rf_m = RF_INFO_ANNUAL / 12.0
    if exposure is not None:
        exp_arr = exposure.to_numpy(dtype=float)
        r_with_cash = r + (1.0 - exp_arr) * rf_m
    else:
        r_with_cash = r
    ex_rf4 = r_with_cash - rf_m
    sd_ex4 = float(np.std(ex_rf4, ddof=1)) if n >= 2 else 0.0
    sharpe_rf4 = (float(np.mean(ex_rf4)) / sd_ex4) * math.sqrt(12.0) if sd_ex4 > 0 else 0.0

    eq_with_start = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(eq_with_start)
    dd = (peak - eq_with_start) / peak
    max_dd = float(np.max(dd))

    df_tmp = pd.DataFrame({"r": r, "year": years.to_numpy()})
    yr_rets = df_tmp.groupby("year")["r"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    total_yrs = int(len(yr_rets))
    pos_yrs = int((yr_rets > 0.0).sum())
    pct_pos = (100.0 * pos_yrs / total_yrs) if total_yrs > 0 else 0.0

    avg_to = float(turnover.mean()) if turnover is not None and len(turnover) else 0.0
    avg_exp = float(exposure.mean()) if exposure is not None and len(exposure) else 1.0

    return {
        "n_months": n,
        "cagr": round(cagr, 5),
        "ann_vol": round(ann_vol, 5),
        "sharpe_rf0": round(sharpe_rf0, 3),
        "sharpe_rf4": round(sharpe_rf4, 3),
        "max_dd": round(max_dd, 5),
        "avg_monthly_turnover": round(avg_to, 4),
        "avg_exposure": round(avg_exp, 4),
        "pct_positive_years": round(pct_pos, 2),
        "positive_years": pos_yrs,
        "total_years": total_yrs,
        "yearly_returns": {int(y): round(float(v), 5) for y, v in yr_rets.items()},
    }


def evaluate_stage1_dev_gate(dev_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Stage 1 Kill Gate (DEV ONLY: 2016-01..2021-12):
      1. Q5_CAGR_1x - EW_M0_CAGR_1x >= +1.0%/yr (+0.01)
      2. mean(Q5_net_1x - Q1_net_1x) > 0
    """
    q_metrics: Dict[str, Any] = {}
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "EW_M0"]:
        q_metrics[q] = {
            "gross": compute_series_metrics(dev_df[f"{q}_gross"], dev_df["year"], dev_df[f"{q}_turnover"], dev_df[f"{q}_exposure"]),
            "net_1x": compute_series_metrics(dev_df[f"{q}_net_1x"], dev_df["year"], dev_df[f"{q}_turnover"], dev_df[f"{q}_exposure"]),
            "net_2x": compute_series_metrics(dev_df[f"{q}_net_2x"], dev_df["year"], dev_df[f"{q}_turnover"], dev_df[f"{q}_exposure"]),
        }
    q_metrics["BENCH"] = compute_series_metrics(dev_df["bench_ret"], dev_df["year"])

    q5_cagr_1x = q_metrics["Q5"]["net_1x"]["cagr"]
    ew_cagr_1x = q_metrics["EW_M0"]["net_1x"]["cagr"]
    excess_cagr_1x = q5_cagr_1x - ew_cagr_1x

    q5_minus_q1 = dev_df["Q5_net_1x"] - dev_df["Q1_net_1x"]
    mean_spread = float(q5_minus_q1.mean())
    sd_spread = float(q5_minus_q1.std(ddof=1)) if len(q5_minus_q1) >= 2 else 0.0
    t_spread = (mean_spread / (sd_spread / math.sqrt(len(q5_minus_q1)))) if sd_spread > 0 else 0.0

    gate_excess = excess_cagr_1x >= 0.01
    gate_spread = mean_spread > 0.0
    passed = bool(gate_excess and gate_spread)

    return {
        "verdict": "PASS" if passed else "FAIL",
        "checks": {
            "q5_minus_ew_cagr_1x_ge_1pct": gate_excess,
            "q5_minus_q1_mean_1x_gt_0": gate_spread,
        },
        "q5_cagr_1x": round(q5_cagr_1x, 5),
        "ew_m0_cagr_1x": round(ew_cagr_1x, 5),
        "excess_cagr_1x": round(excess_cagr_1x, 5),
        "q5_minus_q1_monthly_mean_1x": round(mean_spread, 5),
        "q5_minus_q1_t_stat": round(t_spread, 3),
        "quintile_metrics": q_metrics,
    }


def _check_concentration_robustness(
    holdout_df: pd.DataFrame,
    strat: str,
    matched_ctrl: str,
    stock_contribs: Dict[str, List[Tuple[str, float, float]]],
) -> Dict[str, Any]:
    """
    Criterion 4 concentration checks on HOLDOUT (at 1x costs):
      (a) Drop single best calendar year of `strat` -> remaining excess CAGR vs matched_ctrl > 0
      (b) Drop single highest-contributing stock in `strat` (replace its contribution with
          matched control return in those months) -> excess CAGR vs matched_ctrl > 0
    """
    # (a) Drop single best calendar year
    yr_perf = holdout_df.groupby("year")[f"{strat}_net_1x"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    best_year = int(yr_perf.idxmax())
    sub_no_yr = holdout_df[holdout_df["year"] != best_year]
    m_no_yr = compute_series_metrics(sub_no_yr[f"{strat}_net_1x"], sub_no_yr["year"])
    c_no_yr = compute_series_metrics(sub_no_yr[f"{matched_ctrl}_net_1x"], sub_no_yr["year"])
    excess_no_best_yr = m_no_yr["cagr"] - c_no_yr["cagr"]

    # (b) Drop single highest-contributing stock on HOLDOUT
    holdout_periods = set(holdout_df["period"].tolist())
    ctrl_by_period = dict(zip(holdout_df["period"], holdout_df[f"{matched_ctrl}_net_1x"]))

    sym_totals: Dict[str, float] = {}
    sym_period_map: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for sym, entries in stock_contribs.items():
        tot = 0.0
        for p, w, r_s in entries:
            if p in holdout_periods:
                tot += w * r_s
                sym_period_map.setdefault(sym, {})[p] = (w, r_s)
        if sym in sym_period_map:
            sym_totals[sym] = tot

    if sym_totals:
        top_stock = max(sym_totals.items(), key=lambda kv: kv[1])[0]
        top_contrib = sym_totals[top_stock]
        # Replace top_stock's monthly return with matched_ctrl return (removes its alpha contribution)
        adj_rets = []
        for row in holdout_df.itertuples():
            p = row.period
            base_r = getattr(row, f"{strat}_net_1x")
            if p in sym_period_map.get(top_stock, {}):
                w, r_s = sym_period_map[top_stock][p]
                ctrl_r = ctrl_by_period[p]
                adj_r = base_r - w * (r_s - ctrl_r)
            else:
                adj_r = base_r
            adj_rets.append(adj_r)
        m_no_stk = compute_series_metrics(pd.Series(adj_rets), holdout_df["year"])
        c_full = compute_series_metrics(holdout_df[f"{matched_ctrl}_net_1x"], holdout_df["year"])
        excess_no_top_stk = m_no_stk["cagr"] - c_full["cagr"]
    else:
        top_stock = None
        top_contrib = 0.0
        excess_no_top_stk = 0.0

    return {
        "best_year_dropped": best_year,
        "excess_cagr_without_best_year": round(excess_no_best_yr, 5),
        "survives_drop_best_year": bool(excess_no_best_yr > 0.0),
        "top_stock_dropped": top_stock,
        "top_stock_total_contrib": round(top_contrib, 5),
        "excess_cagr_without_top_stock": round(excess_no_top_stk, 5),
        "survives_drop_top_stock": bool(excess_no_top_stk > 0.0),
    }


def evaluate_stage2_graduation(
    monthly_df: pd.DataFrame,
    stock_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]],
) -> Dict[str, Any]:
    dev_df = monthly_df[monthly_df["split"] == "DEV"].copy()
    ho_df = monthly_df[monthly_df["split"] == "HOLDOUT"].copy()

    spy_ho = compute_series_metrics(ho_df["bench_ret"], ho_df["year"])

    candidates: Dict[str, Any] = {}
    any_pass = False

    for strat, ctrl in [("M1", "EW_M1"), ("M2", "EW_M2")]:
        dev_1x = compute_series_metrics(dev_df[f"{strat}_net_1x"], dev_df["year"], dev_df[f"{strat}_turnover"], dev_df[f"{strat}_exposure"])
        dev_2x = compute_series_metrics(dev_df[f"{strat}_net_2x"], dev_df["year"], dev_df[f"{strat}_turnover"], dev_df[f"{strat}_exposure"])
        c_dev_1x = compute_series_metrics(dev_df[f"{ctrl}_net_1x"], dev_df["year"], dev_df[f"{ctrl}_turnover"], dev_df[f"{ctrl}_exposure"])
        c_dev_2x = compute_series_metrics(dev_df[f"{ctrl}_net_2x"], dev_df["year"], dev_df[f"{ctrl}_turnover"], dev_df[f"{ctrl}_exposure"])

        ho_1x = compute_series_metrics(ho_df[f"{strat}_net_1x"], ho_df["year"], ho_df[f"{strat}_turnover"], ho_df[f"{strat}_exposure"])
        ho_2x = compute_series_metrics(ho_df[f"{strat}_net_2x"], ho_df["year"], ho_df[f"{strat}_turnover"], ho_df[f"{strat}_exposure"])
        c_ho_1x = compute_series_metrics(ho_df[f"{ctrl}_net_1x"], ho_df["year"], ho_df[f"{ctrl}_turnover"], ho_df[f"{ctrl}_exposure"])
        c_ho_2x = compute_series_metrics(ho_df[f"{ctrl}_net_2x"], ho_df["year"], ho_df[f"{ctrl}_turnover"], ho_df[f"{ctrl}_exposure"])

        conc = _check_concentration_robustness(ho_df, strat, ctrl, stock_contribs[strat])

        dev_ex_1x = dev_1x["cagr"] - c_dev_1x["cagr"]
        dev_ex_2x = dev_2x["cagr"] - c_dev_2x["cagr"]
        ho_ex_1x = ho_1x["cagr"] - c_ho_1x["cagr"]
        ho_ex_2x = ho_2x["cagr"] - c_ho_2x["cagr"]

        c1_sharpe = (ho_1x["sharpe_rf0"] >= 0.50) and (ho_2x["sharpe_rf0"] >= 0.40)
        c2_matched = (
            (dev_ex_1x >= 0.015)
            and (dev_ex_2x > 0.0)
            and (ho_ex_1x >= 0.015)
            and (ho_ex_2x > 0.0)
        )
        c3_spy = (ho_1x["sharpe_rf0"] > spy_ho["sharpe_rf0"]) or (
            (spy_ho["max_dd"] - ho_1x["max_dd"] >= 0.05) and (ho_1x["cagr"] > 0.0)
        )
        c4_robust = (
            (ho_1x["pct_positive_years"] >= 60.0)
            and conc["survives_drop_best_year"]
            and conc["survives_drop_top_stock"]
        )
        c5_dd = ho_1x["max_dd"] <= 0.25

        checks = {
            "c1_sharpe_1x_ge_050_and_2x_ge_040": bool(c1_sharpe),
            "c2_matched_excess_dev_and_holdout": bool(c2_matched),
            "c3_beats_spy_sharpe_or_dd_5pp": bool(c3_spy),
            "c4_pos_years_ge_60pct_and_concentration": bool(c4_robust),
            "c5_max_dd_le_25pct": bool(c5_dd),
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

    return {
        "verdict": "PASS" if any_pass else "FAIL",
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Report Formatting
# ---------------------------------------------------------------------------

def _fmt_pct(val: float) -> str:
    return f"{val * 100:+.2f}%"


def _fmt_dd(val: float) -> str:
    return f"-{val * 100:.2f}%"


def _build_strategy_table(metrics_map: Dict[str, Any], cost_key: str = "net_1x") -> str:
    lines = [
        "| Series | Months | CAGR | Ann Vol | Sharpe (rf=0) | Sharpe (rf=4% info) | Max DD | 1-Way TO/mo | Avg Exp | +Years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = ["M0", "EW_M0", "M1", "EW_M1", "M2", "EW_M2", "BENCH"]
    for k in order:
        if k not in metrics_map:
            continue
        m = metrics_map[k] if k == "BENCH" else metrics_map[k][cost_key]
        pos_yr = f"{m['positive_years']}/{m['total_years']} ({m['pct_positive_years']:.0f}%)"
        lines.append(
            f"| **{k}** | {m['n_months']} | {_fmt_pct(m['cagr'])} | {m['ann_vol']*100:.2f}% | "
            f"{m['sharpe_rf0']:+.3f} | {m['sharpe_rf4']:+.3f} | {_fmt_dd(m['max_dd'])} | "
            f"{m['avg_monthly_turnover']*100:.1f}% | {m['avg_exposure']*100:.1f}% | {pos_yr} |"
        )
    return "\n".join(lines)


def _build_quintile_table(q_metrics: Dict[str, Any], cost_key: str = "net_1x") -> str:
    lines = [
        "| Bucket | Months | CAGR | Ann Vol | Sharpe (rf=0) | Max DD | 1-Way TO/mo | +Years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in ["Q1", "Q2", "Q3", "Q4", "Q5", "EW_M0", "BENCH"]:
        if k not in q_metrics:
            continue
        m = q_metrics[k] if k == "BENCH" else q_metrics[k][cost_key]
        pos_yr = f"{m['positive_years']}/{m['total_years']} ({m['pct_positive_years']:.0f}%)"
        lines.append(
            f"| **{k}** | {m['n_months']} | {_fmt_pct(m['cagr'])} | {m['ann_vol']*100:.2f}% | "
            f"{m['sharpe_rf0']:+.3f} | {_fmt_dd(m['max_dd'])} | {m['avg_monthly_turnover']*100:.1f}% | {pos_yr} |"
        )
    return "\n".join(lines)


def _build_yearly_table(monthly_df: pd.DataFrame, cost_key: str = "net_1x") -> str:
    years = sorted(monthly_df["year"].unique())
    lines = [
        "| Year | Split | M0 | EW_M0 | M1 | EW_M1 | M2 | EW_M2 | BENCH |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for y in years:
        sub = monthly_df[monthly_df["year"] == y]
        sp = sub["split"].iloc[0]
        vals = []
        for k in ["M0", "EW_M0", "M1", "EW_M1", "M2", "EW_M2"]:
            r = float(np.prod(1.0 + sub[f"{k}_{cost_key}"].to_numpy()) - 1.0)
            vals.append(_fmt_pct(r))
        br = float(np.prod(1.0 + sub["bench_ret"].to_numpy()) - 1.0)
        vals.append(_fmt_pct(br))
        lines.append(f"| {y} | {sp} | " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_full_report(summary: Dict[str, Any], market_dfs: Dict[str, pd.DataFrame], out_path: Path) -> None:
    us_s1 = summary["US"]["stage1_dev"]
    us_s2 = summary["US"]["stage2_graduation"]
    verdict = summary["overall_verdict"]

    lines = [
        "# Study `T003` Report: 12–1 Month Cross-Sectional Momentum + Vol-Target & Regime Overlay",
        "",
        f"## Final Pre-Registered Verdict (US Primary): **{verdict}**",
        "",
        f"- **Stage 1 Kill Gate (US DEV `2016–2021`):** **{us_s1['verdict']}** "
        f"(`Q5 - EW_M0` 1× CAGR = `{_fmt_pct(us_s1['excess_cagr_1x'])}`, "
        f"`Q5 - Q1` monthly mean = `{_fmt_pct(us_s1['q5_minus_q1_monthly_mean_1x'])}`, `t = {us_s1['q5_minus_q1_t_stat']:+.2f}`)",
        f"- **Stage 2 Graduation Gate (US HOLDOUT `2022–2026`, Single Touch):** **{us_s2['verdict'] if us_s2 else 'SKIPPED (Stage 1 FAIL)'}**",
        "",
        "### Statistical Power & Survivorship Disclosure (Per Rev 2 Pre-Registration)",
        "- **Screen Only (Not Proof of Alpha):** With ~58 monthly HOLDOUT observations (~4.83 years), annualized Sharpe standard error is ~`±0.45–0.50`. A `PASS` here is **permission to buy clean survivorship-free data for `T004` (`1998–2026`) — nothing more.**",
        "- **Survivorship Nuance:** Today's watchlist contains names that survived and grew large (inflating momentum persistence and likely flattering results). With ~42 stocks/quintile and a mega-cap-heavy list, a `FAIL` is strong evidence against the strategy on this universe, not absolute mathematical proof. Any marginal `PASS` is treated as `FAIL`.",
        "- **Sharpe Convention:** `Sharpe_rf0` (`mean/std * sqrt(12)`, `rf=0%`, `0%` cash yield) is computed identically across `M0..M2`, `EW_M0..EW_M2`, and `SPY`/`^NSEI`. Informational `Sharpe_rf4` (4%/yr cash & rf) is shown alongside.",
        "",
    ]

    if us_s2 is not None:
        lines += [
            "### Stage 2 Pre-Registered Graduation Checklist (US HOLDOUT, Net of Costs)",
            "",
            "| Candidate | Matched Ctrl | C1: Sharpe 1x>=0.50 & 2x>=0.40 | C2: Matched Excess DEV & HO | C3: Beats SPY Sharpe or -5pp DD | C4: >=60% +Yrs & Conc. | C5: MaxDD<=25% | Gate |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        fb = lambda b: "YES" if b else "NO"
        for strat in ("M1", "M2"):
            cand = us_s2["candidates"][strat]
            c = cand["checks"]
            lines.append(
                f"| **{strat}** | `{cand['matched_control']}` | {fb(c['c1_sharpe_1x_ge_050_and_2x_ge_040'])} | "
                f"{fb(c['c2_matched_excess_dev_and_holdout'])} | {fb(c['c3_beats_spy_sharpe_or_dd_5pp'])} | "
                f"{fb(c['c4_pos_years_ge_60pct_and_concentration'])} | {fb(c['c5_max_dd_le_25pct'])} | "
                f"**{'PASS' if cand['passed'] else 'FAIL'}** |"
            )

        lines += ["", "#### Matched-Overlay Excess CAGR & Concentration Details (US)", ""]
        for strat in ("M1", "M2"):
            cand = us_s2["candidates"][strat]
            conc = cand["concentration"]
            lines.append(
                f"- **`{strat}` vs `{cand['matched_control']}`:** "
                f"DEV excess = `{_fmt_pct(cand['dev_excess_cagr_1x'])}` (1×) / `{_fmt_pct(cand['dev_excess_cagr_2x'])}` (2×); "
                f"HOLDOUT excess = `{_fmt_pct(cand['ho_excess_cagr_1x'])}` (1×) / `{_fmt_pct(cand['ho_excess_cagr_2x'])}` (2×); "
                f"Drop best year (`{conc['best_year_dropped']}`): `{_fmt_pct(conc['excess_cagr_without_best_year'])}`; "
                f"Drop top stock (`{conc['top_stock_dropped']}`): `{_fmt_pct(conc['excess_cagr_without_top_stock'])}`."
            )

    lines += ["", "---", ""]

    for mkt in ("US", "NSE"):
        role = "PRIMARY DECISION MARKET" if mkt == "US" else "SECONDARY DIAGNOSTIC ONLY (NOT POOLED)"
        lines += [f"## {mkt} ({role})", ""]
        lines += [f"### {mkt} — Stage 1 Quintile Event Study (DEV `2016–2021`, 1× Costs)", ""]
        lines += [_build_quintile_table(summary[mkt]["stage1_dev"]["quintile_metrics"], "net_1x"), ""]

        if summary[mkt]["stage2_metrics"] is not None:
            for split in ("HOLDOUT", "DEV"):
                lines += [f"### {mkt} — {split} Portfolio Results (1× Costs)", ""]
                lines += [_build_strategy_table(summary[mkt]["stage2_metrics"][split], "net_1x"), ""]
                lines += [f"### {mkt} — {split} Portfolio Results (2× Stress Costs)", ""]
                lines += [_build_strategy_table(summary[mkt]["stage2_metrics"][split], "net_2x"), ""]

            lines += [f"### {mkt} — HOLDOUT Quintile Breakdown (1× Costs, Informational)", ""]
            lines += [_build_quintile_table(summary[mkt]["holdout_quintiles"], "net_1x"), ""]

            lines += [f"### {mkt} — Calendar-Year Returns (1× Costs)", ""]
            lines += [_build_yearly_table(market_dfs[mkt], "net_1x"), ""]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _collect_split_metrics(df_sub: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ["M0", "M1", "M2", "EW_M0", "EW_M1", "EW_M2"]:
        out[k] = {
            "gross": compute_series_metrics(df_sub[f"{k}_gross"], df_sub["year"], df_sub[f"{k}_turnover"], df_sub[f"{k}_exposure"]),
            "net_1x": compute_series_metrics(df_sub[f"{k}_net_1x"], df_sub["year"], df_sub[f"{k}_turnover"], df_sub[f"{k}_exposure"]),
            "net_2x": compute_series_metrics(df_sub[f"{k}_net_2x"], df_sub["year"], df_sub[f"{k}_turnover"], df_sub[f"{k}_exposure"]),
        }
    out["BENCH"] = compute_series_metrics(df_sub["bench_ret"], df_sub["year"])
    return out


def _collect_quintile_metrics(df_sub: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "EW_M0"]:
        out[q] = {
            "gross": compute_series_metrics(df_sub[f"{q}_gross"], df_sub["year"], df_sub[f"{q}_turnover"], df_sub[f"{q}_exposure"]),
            "net_1x": compute_series_metrics(df_sub[f"{q}_net_1x"], df_sub["year"], df_sub[f"{q}_turnover"], df_sub[f"{q}_exposure"]),
            "net_2x": compute_series_metrics(df_sub[f"{q}_net_2x"], df_sub["year"], df_sub[f"{q}_turnover"], df_sub[f"{q}_exposure"]),
        }
    out["BENCH"] = compute_series_metrics(df_sub["bench_ret"], df_sub["year"])
    return out


def run_t003(stage: int = 1) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    max_period = DEV_END if stage == 1 else None

    summary: Dict[str, Any] = {"stage_executed": stage}
    market_dfs: Dict[str, pd.DataFrame] = {}

    for mkt in ("US", "NSE"):
        u_dfs, b_df = load_market_data(mkt)
        logger.info("[%s] Loaded %d symbols; benchmark %s (%d bars).", mkt, len(u_dfs), MARKET_CONFIG[mkt]["benchmark"], len(b_df))
        sim = simulate_monthly_strategies(mkt, u_dfs, b_df, max_period=max_period)
        mdf = sim["monthly_df"]
        market_dfs[mkt] = mdf

        dev_df = mdf[mdf["split"] == "DEV"].copy()
        s1 = evaluate_stage1_dev_gate(dev_df)

        summary[mkt] = {
            "n_symbols": len(u_dfs),
            "stage1_dev": s1,
            "stage2_metrics": None,
            "stage2_graduation": None,
            "holdout_quintiles": None,
        }

        if stage == 2:
            ho_df = mdf[mdf["split"] == "HOLDOUT"].copy()
            summary[mkt]["stage2_metrics"] = {
                "DEV": _collect_split_metrics(dev_df),
                "HOLDOUT": _collect_split_metrics(ho_df),
            }
            summary[mkt]["holdout_quintiles"] = _collect_quintile_metrics(ho_df)
            summary[mkt]["stage2_graduation"] = evaluate_stage2_graduation(mdf, sim["stock_contribs"])

    if stage == 1:
        print("\n=== T003 STAGE 1: DEV-ONLY EVENT STUDY (2016-01 .. 2021-12; HOLDOUT SEALED) ===")
        for mkt in ("US", "NSE"):
            s1 = summary[mkt]["stage1_dev"]
            print(f"\n--- {mkt} DEV Quintiles (1x Costs) ---")
            print(_build_quintile_table(s1["quintile_metrics"], "net_1x"))
            print(
                f"{mkt} Stage 1 Gate: {s1['verdict']} | "
                f"Q5-EW_M0 1x CAGR = {_fmt_pct(s1['excess_cagr_1x'])} (need >=+1.00%) | "
                f"Q5-Q1 monthly mean = {_fmt_pct(s1['q5_minus_q1_monthly_mean_1x'])} (t={s1['q5_minus_q1_t_stat']:+.2f})"
            )
        return summary

    # Stage 2: Enforce Stage 1 DEV gate before declaring overall verdict
    us_s1_pass = summary["US"]["stage1_dev"]["verdict"] == "PASS"
    us_s2_pass = bool(us_s1_pass and summary["US"]["stage2_graduation"]["verdict"] == "PASS")
    summary["overall_verdict"] = "PASS" if us_s2_pass else "FAIL"

    # Write outputs immediately
    comb_monthly = pd.concat([market_dfs["US"], market_dfs["NSE"]], ignore_index=True)
    comb_monthly.to_csv(OUT_DIR / "monthly_returns.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_full_report(summary, market_dfs, OUT_DIR / "REPORT.md")

    print(f"\n=== T003 FINAL VERDICT (US PRIMARY): {summary['overall_verdict']} ===")
    print(f"  US Stage 1 (DEV)    : {summary['US']['stage1_dev']['verdict']}")
    print(f"  US Stage 2 (HOLDOUT): {summary['US']['stage2_graduation']['verdict']}")
    print(f"  NSE Diagnostic S1/S2: {summary['NSE']['stage1_dev']['verdict']} / {summary['NSE']['stage2_graduation']['verdict']}")
    print(f"Artifacts written to  : {OUT_DIR}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-registered T003 12-1m momentum research harness.")
    parser.add_argument("--stage", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    run_t003(stage=args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
