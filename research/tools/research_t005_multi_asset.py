"""
Pre-Registered Study T005: Multi-Asset Time-Series Trend / Dual Momentum
(SPY, EFA, TLT, GLD, DBC + ^IRX T-Bill, 2007-2026, Zero Survivorship Bias).

Implements research_out/phase1/PREREG_T005_MULTI_ASSET_TREND.md strictly:
  - `--download`: fetches missing ETFs (EFA, TLT, GLD, DBC) with auto_adjust=True.
  - `--stage 1`: DEV-only (2007-03..2015-12, 106 mos) trend kill gate (A0 vs EW_5).
    HOLDOUT is NEVER read or computed in Stage 1.
  - `--stage 2`: Executed ONCE only if Stage 1 passes. Evaluates A0, A1, A2 against
    EW_5, RP_5, B60_40, and SPY at 1x (10 bps RT) and 2x (20 bps RT) costs.
"""

import argparse
import json
import logging
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.tools.download_etf_daily import clean_etf_df, _fetch_one
from research.tools.research_momentum_12_1 import (
    build_monthly_schedule,
    compute_basket_vol_weight,
    compute_rebalance_cost,
)
from research.tools.research_t004_sector_etf import (
    _open_on_or_after,
    _open_on_or_before,
    compute_etf_metrics,
    lookup_causal_tbill_monthly,
)

DATA_DIR = ROOT / "data" / "research_etf"
OUT_DIR = ROOT / "research" / "phase1" / "T005"

MACRO_ETFS = ["SPY", "EFA", "TLT", "GLD", "DBC"]
DEV_START = "2007-03"
DEV_END = "2015-12"
HOLDOUT_START = "2016-01"

TREND_LOOKBACK = 252
VOL_LOOKBACK = 60
A2_TARGET_VOL = 0.10
ONE_WAY_1X = 0.0005  # 5 bps one-way = 10 bps RT
ONE_WAY_2X = 0.0010  # 10 bps one-way = 20 bps RT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("research_t005")


def ensure_macro_data(force: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tz_dir = Path(tempfile.gettempdir()) / "breakout_scanner_yf_etf_tz_cache"
    tz_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(tz_dir.resolve()))

    rows = []
    for sym in MACRO_ETFS + ["^IRX"]:
        fp = DATA_DIR / f"{sym}_1d.pkl"
        if not force and fp.exists():
            df = pd.read_pickle(fp)
        else:
            raw = _fetch_one(sym)
            df = clean_etf_df(raw, sym)
            if df is not None and not df.empty:
                df.to_pickle(fp)
            time.sleep(0.3)
        if df is None or df.empty:
            rows.append({"symbol": sym, "bars": 0, "first_date": "", "last_date": "", "status": "FAILED"})
        else:
            rows.append(
                {
                    "symbol": sym,
                    "bars": int(len(df)),
                    "first_date": str(df.index.min().date()),
                    "last_date": str(df.index.max().date()),
                    "status": "OK",
                }
            )
    manifest = pd.DataFrame(rows)
    manifest.to_csv(OUT_DIR / "manifest.csv", index=False)
    return manifest


def load_macro_dataset() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    etf_dfs = {sym: pd.read_pickle(DATA_DIR / f"{sym}_1d.pkl").sort_index() for sym in MACRO_ETFS}
    irx_df = pd.read_pickle(DATA_DIR / "^IRX_1d.pkl").sort_index()
    return etf_dfs, irx_df


def lookup_causal_tbill_annual(irx_close: pd.Series, sig_dt: pd.Timestamp) -> float:
    """Strictly causal annualized T-bill yield (decimal, e.g. 0.045) <= sig_dt."""
    if irx_close is None or irx_close.empty:
        return 0.0
    pos = irx_close.index.searchsorted(sig_dt, side="right") - 1
    if pos < 0:
        return 0.0
    return max(0.0, float(irx_close.iloc[pos]) / 100.0)


def compute_inv_vol_weights(
    daily_rets_df: pd.DataFrame,
    symbols: List[str],
    sig_dt: pd.Timestamp,
    vol_lb: int = VOL_LOOKBACK,
) -> Dict[str, float]:
    """Strictly causal inverse 60-day realized volatility weights summing to 1.0."""
    inv_vols: Dict[str, float] = {}
    for s in symbols:
        sub = daily_rets_df.loc[:sig_dt, s].dropna().tail(vol_lb)
        if len(sub) < 20:
            return {sym: 1.0 / len(symbols) for sym in symbols}
        sd = float(sub.std(ddof=1) * math.sqrt(252.0))
        if not math.isfinite(sd) or sd <= 0:
            return {sym: 1.0 / len(symbols) for sym in symbols}
        inv_vols[s] = 1.0 / sd
    tot = sum(inv_vols.values())
    return {s: v / tot for s, v in inv_vols.items()}


def simulate_multi_asset(
    etf_dfs: Dict[str, pd.DataFrame],
    irx_df: pd.DataFrame,
    max_period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Monthly open-to-open simulation for A0, A1, A2 and static controls EW_5, RP_5, B60_40, SPY.
    Strictly causal <= signal_date. If max_period is set, later periods are NEVER evaluated.
    """
    close_df = pd.DataFrame({s: df["Close"].astype(float) for s, df in etf_dfs.items()}).sort_index()
    open_df = pd.DataFrame({s: df["Open"].astype(float) for s, df in etf_dfs.items()}).sort_index()
    ret_12m_df = close_df / close_df.shift(TREND_LOOKBACK) - 1.0
    daily_rets_df = close_df.pct_change()
    irx_close = irx_df["Close"].sort_index().astype(float)

    spy_df = etf_dfs["SPY"]
    schedule = build_monthly_schedule(spy_df)
    schedule = [p for p in schedule if p["period"] >= DEV_START]
    if max_period is not None:
        schedule = [p for p in schedule if p["period"] <= max_period]

    strat_names = ["A0", "A1", "A2", "EW_5", "RP_5", "B60_40"]
    prev_w: Dict[str, Dict[str, float]] = {k: {} for k in strat_names}
    prev_r: Dict[str, Dict[str, float]] = {k: {} for k in strat_names}
    prev_g: Dict[str, float] = {k: 0.0 for k in strat_names}

    rows: List[Dict[str, Any]] = []
    etf_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]] = {k: {} for k in ["A0", "A1", "A2"]}

    for step in schedule:
        period = step["period"]
        sig_dt = step["signal_date"]
        fill_dt = step["fill_date"]
        next_fill_dt = step["next_fill_date"]
        split = "DEV" if period <= DEV_END else "HOLDOUT"

        if sig_dt not in ret_12m_df.index:
            continue
        r12_row = ret_12m_df.loc[sig_dt]
        if r12_row[MACRO_ETFS].isna().any():
            continue

        asset_rets: Dict[str, float] = {}
        ok_all = True
        for sym in MACRO_ETFS:
            o0 = _open_on_or_after(open_df[sym], fill_dt)
            o1 = _open_on_or_before(open_df[sym], next_fill_dt)
            if o0 is None or o1 is None:
                ok_all = False
                break
            asset_rets[sym] = (o1 / o0) - 1.0
        if not ok_all:
            continue

        rf_m = lookup_causal_tbill_monthly(irx_close, sig_dt)
        rf_ann = lookup_causal_tbill_annual(irx_close, sig_dt)

        excess_12m = {s: float(r12_row[s]) - rf_ann for s in MACRO_ETFS}
        trend_on = {s: (excess_12m[s] > 0.0) for s in MACRO_ETFS}

        # Static controls
        w_ew5 = {s: 1.0 / len(MACRO_ETFS) for s in MACRO_ETFS}
        w_rp5 = compute_inv_vol_weights(daily_rets_df, MACRO_ETFS, sig_dt, VOL_LOOKBACK)
        w_6040 = {"SPY": 0.60, "TLT": 0.40}

        # A0: Equal-weight time-series trend
        w_a0 = {s: (0.20 if trend_on[s] else 0.0) for s in MACRO_ETFS}
        w_a0 = {s: w for s, w in w_a0.items() if w > 0}

        # A1: Inverse-vol time-series trend (matched base weights to RP_5)
        w_a1 = {s: (w_rp5[s] if trend_on[s] else 0.0) for s in MACRO_ETFS}
        w_a1 = {s: w for s, w in w_a1.items() if w > 0}

        # A2: Dual Momentum Top-2 + Absolute Trend + 10% Vol-Target
        ranked = sorted(MACRO_ETFS, key=lambda s: (-excess_12m[s], s))
        top2_on = [s for s in ranked[:2] if trend_on[s]]
        if top2_on:
            # 50% base weight per active slot (unfilled slot stays cash)
            base_a2 = {s: 0.50 for s in top2_on}
            # Basket vol on the active weighted portfolio over trailing 60d <= sig_dt
            sub_ret = daily_rets_df.loc[:sig_dt, top2_on].tail(VOL_LOOKBACK).dropna()
            if len(sub_ret) >= 20:
                port_dret = sum(sub_ret[s] * 0.50 for s in top2_on)
                sig_a2 = float(port_dret.std(ddof=1) * math.sqrt(252.0))
                scale_a2 = min(1.0, A2_TARGET_VOL / sig_a2) if (math.isfinite(sig_a2) and sig_a2 > 0) else 1.0
            else:
                scale_a2 = 1.0
            w_a2 = {s: w * scale_a2 for s, w in base_a2.items()}
        else:
            w_a2 = {}

        targets = {
            "A0": w_a0,
            "A1": w_a1,
            "A2": w_a2,
            "EW_5": w_ew5,
            "RP_5": w_rp5,
            "B60_40": w_6040,
        }

        rec: Dict[str, Any] = {
            "period": period,
            "year": step["year"],
            "split": split,
            "signal_date": str(sig_dt.date()),
            "fill_date": str(fill_dt.date()),
            "next_fill_date": str(next_fill_dt.date()),
            "rf_m": rf_m,
            "spy_ret": asset_rets["SPY"],
            "n_trend_on": int(sum(trend_on.values())),
            "a2_active": ",".join(sorted(w_a2.keys())),
        }

        for name in strat_names:
            w_new = targets[name]
            exp = sum(w_new.values())
            eq_gross = sum(w * asset_rets[s] for s, w in w_new.items())
            cash_y = (1.0 - exp) * rf_m

            to_1w, c1 = compute_rebalance_cost(prev_w[name], prev_r[name], prev_g[name], w_new, ONE_WAY_1X)
            _, c2 = compute_rebalance_cost(prev_w[name], prev_r[name], prev_g[name], w_new, ONE_WAY_2X)

            rec[f"{name}_net_1x_tbill"] = eq_gross + cash_y - c1
            rec[f"{name}_net_2x_tbill"] = eq_gross + cash_y - c2
            rec[f"{name}_net_1x_0cash"] = eq_gross - c1
            rec[f"{name}_net_2x_0cash"] = eq_gross - c2
            rec[f"{name}_turnover"] = to_1w
            rec[f"{name}_exposure"] = exp

            if name in etf_contribs:
                for s, w in w_new.items():
                    etf_contribs[name].setdefault(s, []).append((period, w, asset_rets[s]))

            prev_w[name] = w_new
            prev_r[name] = {s: asset_rets[s] for s in w_new.keys()}
            prev_g[name] = eq_gross + cash_y

        rows.append(rec)

    return {"monthly_df": pd.DataFrame(rows), "etf_contribs": etf_contribs}


def _collect_metrics(df_sub: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ["A0", "A1", "A2", "EW_5", "RP_5", "B60_40"]:
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


def evaluate_stage1_dev(dev_df: pd.DataFrame) -> Dict[str, Any]:
    sm = _collect_metrics(dev_df)
    a0 = sm["A0"]["net_1x"]
    ew = sm["EW_5"]["net_1x"]

    sharpe_diff = a0["sharpe_tbill"] - ew["sharpe_tbill"]
    dd_reduction = ew["max_dd"] - a0["max_dd"]
    c_sharpe = sharpe_diff >= 0.15
    c_dd = dd_reduction >= 0.05

    return {
        "verdict": "PASS" if (c_sharpe and c_dd) else "FAIL",
        "checks": {
            "a0_minus_ew5_sharpe_ge_015": bool(c_sharpe),
            "ew5_minus_a0_maxdd_ge_5pp": bool(c_dd),
        },
        "a0_sharpe_1x": a0["sharpe_tbill"],
        "ew5_sharpe_1x": ew["sharpe_tbill"],
        "sharpe_diff": round(sharpe_diff, 3),
        "a0_max_dd": a0["max_dd"],
        "ew5_max_dd": ew["max_dd"],
        "dd_reduction": round(dd_reduction, 5),
        "dev_metrics": sm,
    }


def _check_macro_concentration(
    ho_df: pd.DataFrame,
    strat: str,
    contribs: Dict[str, List[Tuple[str, float, float]]],
) -> Dict[str, Any]:
    # (a) Drop single best calendar year -> Sharpe_tbill > 0.25
    yr_perf = ho_df.groupby("year")[f"{strat}_net_1x_tbill"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    best_yr = int(yr_perf.idxmax())
    sub = ho_df[ho_df["year"] != best_yr]
    m_ny = compute_etf_metrics(sub[f"{strat}_net_1x_tbill"], sub["rf_m"], sub["year"])

    # (b) Drop single highest-contributing ETF (replace its weight with T-bill cash) -> Sharpe_tbill > 0.25
    ho_periods = set(ho_df["period"].tolist())
    rf_map = dict(zip(ho_df["period"], ho_df["rf_m"]))
    totals: Dict[str, float] = {}
    pmap: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for sym, entries in contribs.items():
        tot = 0.0
        for p, w, r_s in entries:
            if p in ho_periods:
                tot += w * (r_s - rf_map[p])
                pmap.setdefault(sym, {})[p] = (w, r_s)
        if sym in pmap:
            totals[sym] = tot

    if totals:
        top_etf = max(totals.items(), key=lambda kv: kv[1])[0]
        top_c = totals[top_etf]
        adj = []
        for row in ho_df.itertuples():
            p = row.period
            base_r = getattr(row, f"{strat}_net_1x_tbill")
            if p in pmap.get(top_etf, {}):
                w, r_s = pmap[top_etf][p]
                adj_r = base_r - w * (r_s - rf_map[p])
            else:
                adj_r = base_r
            adj.append(adj_r)
        m_ne = compute_etf_metrics(pd.Series(adj), ho_df["rf_m"], ho_df["year"])
        sh_no_etf = m_ne["sharpe_tbill"]
    else:
        top_etf, top_c, sh_no_etf = None, 0.0, 0.0

    return {
        "best_year_dropped": best_yr,
        "sharpe_without_best_year": m_ny["sharpe_tbill"],
        "survives_drop_best_year": bool(m_ny["sharpe_tbill"] > 0.25),
        "top_etf_dropped": top_etf,
        "top_etf_excess_contrib": round(top_c, 5),
        "sharpe_without_top_etf": sh_no_etf,
        "survives_drop_top_etf": bool(sh_no_etf > 0.25),
    }


def evaluate_stage2_graduation(
    mdf: pd.DataFrame,
    etf_contribs: Dict[str, Dict[str, List[Tuple[str, float, float]]]],
) -> Dict[str, Any]:
    dev_m = _collect_metrics(mdf[mdf["split"] == "DEV"])
    ho_df = mdf[mdf["split"] == "HOLDOUT"].copy()
    ho_m = _collect_metrics(ho_df)

    spy_ho = ho_m["SPY"]
    tbill_ho = ho_m["TBILL"]
    b6040_ho_1x = ho_m["B60_40"]["net_1x"]

    candidates: Dict[str, Any] = {}
    any_pass = False

    for strat in ("A1", "A2"):
        d1 = dev_m[strat]["net_1x"]
        d2 = dev_m[strat]["net_2x"]
        h1 = ho_m[strat]["net_1x"]
        h2 = ho_m[strat]["net_2x"]

        conc = _check_macro_concentration(ho_df, strat, etf_contribs[strat])

        c1 = (h1["sharpe_tbill"] >= 0.55) and (h2["sharpe_tbill"] >= 0.50)
        c2 = (
            (d1["sharpe_tbill"] > dev_m["EW_5"]["net_1x"]["sharpe_tbill"])
            and (d1["sharpe_tbill"] > dev_m["RP_5"]["net_1x"]["sharpe_tbill"])
            and (d2["sharpe_tbill"] > dev_m["EW_5"]["net_2x"]["sharpe_tbill"])
            and (d2["sharpe_tbill"] > dev_m["RP_5"]["net_2x"]["sharpe_tbill"])
            and (h1["sharpe_tbill"] > ho_m["EW_5"]["net_1x"]["sharpe_tbill"])
            and (h1["sharpe_tbill"] > ho_m["RP_5"]["net_1x"]["sharpe_tbill"])
            and (h2["sharpe_tbill"] > ho_m["EW_5"]["net_2x"]["sharpe_tbill"])
            and (h2["sharpe_tbill"] > ho_m["RP_5"]["net_2x"]["sharpe_tbill"])
        )
        c3 = (
            (h1["sharpe_tbill"] > b6040_ho_1x["sharpe_tbill"])
            and (spy_ho["max_dd"] - h1["max_dd"] >= 0.05)
            and (h1["cagr"] > tbill_ho["cagr"])
        )
        c4 = (
            (h1["pct_positive_years"] >= 70.0)
            and conc["survives_drop_best_year"]
            and conc["survives_drop_top_etf"]
        )
        c5 = h1["max_dd"] <= 0.15

        checks = {
            "c1_sharpe_1x_ge_055_and_2x_ge_050": bool(c1),
            "c2_beats_ew5_and_rp5_dev_and_ho_1x_2x": bool(c2),
            "c3_beats_6040_and_cuts_spy_dd_5pp": bool(c3),
            "c4_pos_years_ge_70pct_and_concentration": bool(c4),
            "c5_max_dd_le_15pct": bool(c5),
        }
        passed = all(checks.values())
        if passed:
            any_pass = True

        candidates[strat] = {"passed": passed, "checks": checks, "concentration": conc}

    return {"verdict": "PASS" if any_pass else "FAIL", "candidates": candidates}


def _fmt_pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _fmt_dd(v: float) -> str:
    return f"-{v * 100:.2f}%"


def _table(sm: Dict[str, Any], cost_key: str = "net_1x") -> str:
    lines = [
        "| Series | Mos | CAGR (TBill) | CAGR (0% cash) | Ann Vol | Sharpe (excess ^IRX) | Sharpe (rf=0) | Max DD | TO/mo | Avg Exp | +Years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in ["A0", "A1", "A2", "EW_5", "RP_5", "B60_40", "SPY", "TBILL"]:
        m = sm[k] if k in ("SPY", "TBILL") else sm[k][cost_key]
        py = f"{m['positive_years']}/{m['total_years']} ({m['pct_positive_years']:.0f}%)"
        lines.append(
            f"| **{k}** | {m['n_months']} | {_fmt_pct(m['cagr'])} | {_fmt_pct(m['cagr_0cash'])} | "
            f"{m['ann_vol']*100:.2f}% | {m['sharpe_tbill']:+.3f} | {m['sharpe_rf0']:+.3f} | "
            f"{_fmt_dd(m['max_dd'])} | {m['avg_monthly_turnover']*100:.1f}% | {m['avg_exposure']*100:.1f}% | {py} |"
        )
    return "\n".join(lines)


def _yearly_table(mdf: pd.DataFrame) -> str:
    lines = [
        "| Year | Split | A0 | A1 | A2 | EW_5 | RP_5 | B60_40 | SPY | TBILL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for y in sorted(mdf["year"].unique()):
        sub = mdf[mdf["year"] == y]
        sp = sub["split"].iloc[0]
        vals = [
            _fmt_pct(float(np.prod(1.0 + sub[f"{k}_net_1x_tbill"].to_numpy()) - 1.0))
            for k in ["A0", "A1", "A2", "EW_5", "RP_5", "B60_40"]
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
        "# Study `T005` Report: Multi-Asset Time-Series Trend / Dual Momentum (`2007–2026`)",
        "",
        f"## Final Pre-Registered Verdict: **{verdict}**",
        "",
        f"- **Stage 1 Kill Gate (DEV `2007-03..2015-12`, 106 mos):** **{s1['verdict']}** "
        f"(`A0 - EW_5` Sharpe = `{s1['sharpe_diff']:+.3f}`, MaxDD reduction = `+{s1['dd_reduction']*100:.2f}pp`)",
        f"- **Stage 2 Graduation Gate (HOLDOUT `2016-01..2026-10`, 130 mos, Single Touch):** **{s2['verdict'] if s2 else 'SKIPPED (Stage 1 FAIL — HOLDOUT SEALED)'}**",
        "",
        "### Methodological Disclosures",
        "- **Zero Survivorship Bias:** 5 liquid macro asset-class ETFs (`SPY, EFA, TLT, GLD, DBC`) + causal `^IRX` T-bill yield.",
        "- **Sharpe & Cash Convention:** Uninvested cash earns strictly causal `^IRX` T-bill monthly rate; `Sharpe_tbill` uses true excess returns (`r_p - r_f`) identically across all series.",
        "- **Costs:** `1× = 10 bps` RT (`5 bps` one-way); `2× = 20 bps` RT (`10 bps` one-way) on exact drifted-weight turnover.",
        "",
    ]

    if s2 is not None:
        lines += [
            "### Stage 2 Pre-Registered Graduation Checklist (HOLDOUT `2016–2026`, Net of Costs)",
            "",
            "| Candidate | C1: Sharpe 1x>=0.55 & 2x>=0.50 | C2: Beats EW_5 & RP_5 (DEV+HO, 1x+2x) | C3: Beats 60/40 & -5pp vs SPY DD | C4: >=70% +Yrs & Conc. | C5: MaxDD<=15% | Gate |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        fb = lambda b: "YES" if b else "NO"
        for strat in ("A1", "A2"):
            cand = s2["candidates"][strat]
            c = cand["checks"]
            lines.append(
                f"| **{strat}** | {fb(c['c1_sharpe_1x_ge_055_and_2x_ge_050'])} | "
                f"{fb(c['c2_beats_ew5_and_rp5_dev_and_ho_1x_2x'])} | "
                f"{fb(c['c3_beats_6040_and_cuts_spy_dd_5pp'])} | "
                f"{fb(c['c4_pos_years_ge_70pct_and_concentration'])} | "
                f"{fb(c['c5_max_dd_le_15pct'])} | **{'PASS' if cand['passed'] else 'FAIL'}** |"
            )
        lines += ["", "#### Concentration Robustness Details (HOLDOUT, 1× Costs)", ""]
        for strat in ("A1", "A2"):
            conc = s2["candidates"][strat]["concentration"]
            lines.append(
                f"- **`{strat}`:** Drop best year (`{conc['best_year_dropped']}`): Sharpe = `{conc['sharpe_without_best_year']:+.3f}` (need `>0.25`); "
                f"Drop top ETF (`{conc['top_etf_dropped']}`): Sharpe = `{conc['sharpe_without_top_etf']:+.3f}` (need `>0.25`)."
            )

    lines += ["", "---", ""]
    if summary["stage2_metrics"] is not None:
        for key, label in [
            ("HOLDOUT", "HOLDOUT (`2016–2026`, 130 mos)"),
            ("DEV", "DEV (`2007–2015`, 106 mos)"),
            ("FULL", "FULL 20-YEAR SPAN (`2007–2026`, 236 mos)"),
            ("HOLDOUT_2022_2026", "Informational Sub-Window (`2022–2026`, 58 mos)"),
        ]:
            lines += [
                f"## {label} — 1× Costs (10 bps RT)",
                "",
                _table(summary["stage2_metrics"][key], "net_1x"),
                "",
                f"### {label} — 2× Stress Costs (20 bps RT)",
                "",
                _table(summary["stage2_metrics"][key], "net_2x"),
                "",
            ]
        lines += ["## Calendar-Year Returns (`2007–2026`, 1× Costs, T-Bill Cash)", "", _yearly_table(mdf), ""]
    else:
        lines += ["## Stage 1 DEV Results (`2007–2015`, 106 mos, 1× Costs)", "", _table(s1["dev_metrics"], "net_1x"), ""]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_t005(stage: int = 1) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    etf_dfs, irx_df = load_macro_dataset()
    max_period = DEV_END if stage == 1 else None

    sim = simulate_multi_asset(etf_dfs, irx_df, max_period=max_period)
    mdf = sim["monthly_df"]
    dev_df = mdf[mdf["split"] == "DEV"].copy()
    s1 = evaluate_stage1_dev(dev_df)

    if stage == 1:
        print("\n=== T005 STAGE 1: DEV-ONLY MULTI-ASSET TREND (2007-03 .. 2015-12; HOLDOUT SEALED) ===")
        print(_table(s1["dev_metrics"], "net_1x"))
        print(
            f"\nT005 Stage 1 Gate: {s1['verdict']} | "
            f"A0-EW_5 Sharpe = {s1['sharpe_diff']:+.3f} (need >=+0.150) | "
            f"MaxDD cut = +{s1['dd_reduction']*100:.2f}pp (need >=+5.00pp)"
        )
        summary = {
            "stage_executed": 1,
            "overall_verdict": "PENDING_STAGE_2" if s1["verdict"] == "PASS" else "FAIL",
            "stage1_dev": s1,
            "stage2_graduation": None,
            "stage2_metrics": None,
        }
        if s1["verdict"] == "FAIL":
            mdf.to_csv(OUT_DIR / "monthly_returns_dev.csv", index=False)
            (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            write_report(summary, mdf, OUT_DIR / "REPORT.md")
        return summary

    ho_df = mdf[mdf["split"] == "HOLDOUT"].copy()
    ho_recent = mdf[mdf["period"] >= "2022-01"].copy()
    s2 = evaluate_stage2_graduation(mdf, sim["etf_contribs"])
    overall = "PASS" if (s1["verdict"] == "PASS" and s2["verdict"] == "PASS") else "FAIL"

    summary = {
        "stage_executed": 2,
        "overall_verdict": overall,
        "stage1_dev": s1,
        "stage2_graduation": s2,
        "stage2_metrics": {
            "DEV": _collect_metrics(dev_df),
            "HOLDOUT": _collect_metrics(ho_df),
            "FULL": _collect_metrics(mdf),
            "HOLDOUT_2022_2026": _collect_metrics(ho_recent),
        },
    }

    mdf.to_csv(OUT_DIR / "monthly_returns.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(summary, mdf, OUT_DIR / "REPORT.md")

    print(f"\n=== T005 FINAL VERDICT: {overall} ===")
    print(f"  Stage 1 (DEV 2007-2015)    : {s1['verdict']}")
    print(f"  Stage 2 (HOLDOUT 2016-2026): {s2['verdict']}")
    print(f"Artifacts written to         : {OUT_DIR}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-registered T005 Multi-Asset Trend harness.")
    parser.add_argument("--download", action="store_true", help="Download missing macro ETFs.")
    parser.add_argument("--stage", type=int, choices=[1, 2], default=None)
    args = parser.parse_args()
    if args.download:
        m = ensure_macro_data()
        print(m.to_string(index=False))
        if not (m["status"] == "OK").all():
            return 1
    if args.stage is not None:
        run_t005(stage=args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
