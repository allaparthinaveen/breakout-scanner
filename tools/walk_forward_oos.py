"""
Phase 5 — Walk-Forward Out-of-Sample Evaluator
===============================================
Runs rolling temporal train/test splits on offline golden fixtures to compare
baseline scanner performance versus Phase 2/3 improved scoring.

Metrics computed per window and globally:
- TP1 Hit Rate (% of trades reaching +1.0R)
- Win Rate (% of trades closed with net profit)
- Expectancy E(R) in dollars
- Average R (risk-normalized return)
- Profit Factor
- Max Drawdown %

Usage:
    python3 tools/walk_forward_oos.py
    python3 tools/walk_forward_oos.py --symbols AAPL NVDA MSFT --windows 5
"""

import sys
import os
import json
import argparse
from copy import deepcopy
from typing import List, Dict, Optional

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester

# ─────────────────────────────────────────────
# Fixture discovery
# ─────────────────────────────────────────────

FIXTURE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "golden_dataset"
)


def load_fixture(symbol: str, timeframe: str = "1h") -> Optional[pd.DataFrame]:
    """Load an offline fixture CSV for a given symbol and timeframe."""
    fname = os.path.join(FIXTURE_DIR, f"{symbol}_{timeframe}.csv")
    if not os.path.exists(fname):
        return None
    df = pd.read_csv(fname, index_col=0, parse_dates=True)
    df.columns = [c.capitalize() for c in df.columns]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    return df


def list_available_symbols(timeframe: str = "1h") -> List[str]:
    """Return symbol names for which fixtures exist."""
    if not os.path.isdir(FIXTURE_DIR):
        return []
    syms = []
    suffix = f"_{timeframe}.csv"
    for fname in os.listdir(FIXTURE_DIR):
        if fname.endswith(suffix):
            syms.append(fname[: -len(suffix)])
    return sorted(syms)


# ─────────────────────────────────────────────
# Config variants for comparison
# ─────────────────────────────────────────────

def make_baseline_cfg() -> dict:
    """Baseline config: all Phase 2/3/4 modifiers OFF."""
    cfg = load_config()
    cfg.setdefault("signal_quality", {})["enabled"] = False
    cfg["signal_quality"]["close_location_weight"] = 0.0
    cfg["signal_quality"]["vdu_slope_weight"] = 0.0
    cfg["signal_quality"]["trigger_tightness_weight"] = 0.0
    cfg["signal_quality"]["failed_breakout_penalty_weight"] = 0.0
    cfg.setdefault("confluence_context", {})["enabled"] = False
    return cfg


def make_improved_cfg() -> dict:
    """Improved config: Phase 2 signal quality modifiers activated at calibrated weights."""
    cfg = load_config()
    cfg.setdefault("signal_quality", {})["enabled"] = True
    cfg["signal_quality"]["close_location_weight"] = 1.0
    cfg["signal_quality"]["vdu_slope_weight"] = 1.0
    cfg["signal_quality"]["trigger_tightness_weight"] = 1.0
    cfg["signal_quality"]["failed_breakout_penalty_weight"] = 1.0
    cfg.setdefault("confluence_context", {})["enabled"] = False  # Keep HTF off for fair comparison
    return cfg


# ─────────────────────────────────────────────
# Rolling window splitter
# ─────────────────────────────────────────────

def rolling_windows(df: pd.DataFrame, train_bars: int, test_bars: int, step_bars: int) -> List[dict]:
    """Generate non-overlapping OOS test windows."""
    windows = []
    n = len(df)
    start = 0
    wid = 1
    while start + train_bars + test_bars <= n:
        te = start + train_bars
        windows.append({
            "window_id": wid,
            "train": df.iloc[start:te],
            "test": df.iloc[te: te + test_bars],
        })
        start += step_bars
        wid += 1
    return windows


# ─────────────────────────────────────────────
# Per-symbol evaluation
# ─────────────────────────────────────────────

def evaluate_symbol(
    symbol: str,
    df: pd.DataFrame,
    cfg: dict,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> Dict:
    """Run rolling OOS backtest for a single symbol and return aggregated metrics."""
    windows = rolling_windows(df, train_bars, test_bars, step_bars)
    if not windows:
        return {"symbol": symbol, "windows": 0, "trades": 0}

    all_trades: List[Dict] = []
    initial_cap = float(cfg["risk"].get("account_size", 100000.0))
    capital = initial_cap
    window_summaries = []

    for w in windows:
        test_cfg = deepcopy(cfg)
        test_cfg["risk"]["account_size"] = capital

        bt = Backtester(test_cfg)
        metrics = bt.run(symbol, w["test"].copy())

        if bt.results:
            all_trades.extend(bt.results)
            last_trade = bt.results[-1]
            capital = float(last_trade.get("capital_after", capital))

        window_summaries.append({
            "window_id": w["window_id"],
            "train_bars": len(w["train"]),
            "test_bars": len(w["test"]),
            "trades": metrics.get("trades", 0),
            "win_rate": metrics.get("win_rate", 0.0),
            "tp1_hit_rate": metrics.get("tp1_hit_rate", 0.0),
            "expectancy": metrics.get("expectancy", 0.0),
            "average_r": metrics.get("average_r", 0.0),
            "profit_factor": metrics.get("profit_factor", 0.0),
            "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
        })

    # Aggregate global OOS metrics across all windows
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t.get("net_pnl", 0) > 0]
    losses = [t for t in all_trades if t.get("net_pnl", 0) <= 0]
    win_rate = len(wins) / total_trades if total_trades else 0.0
    tp1_hits = sum(1 for t in all_trades if t.get("tp1_hit", False))
    tp1_rate = tp1_hits / total_trades if total_trades else 0.0
    avg_win = float(np.mean([t["net_pnl"] for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([abs(t["net_pnl"]) for t in losses])) if losses else 0.0
    expectancy = round((win_rate * avg_win) - ((1 - win_rate) * avg_loss), 2)
    avg_r = round(float(np.mean([t.get("realized_r", 0.0) for t in all_trades])), 3) if all_trades else 0.0
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    net_return = round((capital - initial_cap) / initial_cap * 100.0, 2)

    return {
        "symbol": symbol,
        "windows": len(windows),
        "trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "tp1_hit_rate": round(tp1_rate, 4),
        "expectancy": expectancy,
        "average_r": avg_r,
        "profit_factor": profit_factor,
        "net_return_pct": net_return,
        "initial_capital": round(initial_cap, 2),
        "final_capital": round(capital, 2),
        "window_summaries": window_summaries,
    }


# ─────────────────────────────────────────────
# Report renderer
# ─────────────────────────────────────────────

def format_comparison_report(
    baseline_results: List[Dict],
    improved_results: List[Dict],
) -> str:
    """Render side-by-side baseline vs. improved comparison report."""
    lines = [
        "",
        "=" * 72,
        " PHASE 5 — WALK-FORWARD OUT-OF-SAMPLE EVALUATION REPORT",
        "=" * 72,
        "",
    ]

    # Build lookup by symbol
    improved_map = {r["symbol"]: r for r in improved_results}

    total_base_trades = total_imp_trades = 0
    total_base_wins = total_imp_wins = 0
    total_base_tp1 = total_imp_tp1 = 0
    exp_gains = []
    r_gains = []

    for br in baseline_results:
        sym = br["symbol"]
        ir = improved_map.get(sym)
        if not ir:
            continue

        total_base_trades += br["trades"]
        total_imp_trades += ir["trades"]
        total_base_wins += br["wins"]
        total_imp_wins += ir["wins"]
        total_base_tp1 += int(br.get("tp1_hit_rate", 0) * br["trades"])
        total_imp_tp1 += int(ir.get("tp1_hit_rate", 0) * ir["trades"])

        if br["trades"] > 0 and ir["trades"] > 0:
            exp_delta = ir["expectancy"] - br["expectancy"]
            r_delta = ir["average_r"] - br["average_r"]
            exp_gains.append(exp_delta)
            r_gains.append(r_delta)
            delta_sign = "▲" if exp_delta >= 0 else "▼"
            lines.append(
                f"  {sym:<8} | Trades: {br['trades']:>3} → {ir['trades']:>3} | "
                f"WR: {br['win_rate']*100:.1f}% → {ir['win_rate']*100:.1f}% | "
                f"TP1: {br['tp1_hit_rate']*100:.1f}% → {ir['tp1_hit_rate']*100:.1f}% | "
                f"E(R): ${br['expectancy']:>+7.1f} → ${ir['expectancy']:>+7.1f} {delta_sign} | "
                f"Avg R: {br['average_r']:>+.3f} → {ir['average_r']:>+.3f}"
            )
        else:
            lines.append(f"  {sym:<8} | Insufficient trades for comparison")

    lines.append("")
    lines.append("─" * 72)
    lines.append(" AGGREGATE OOS METRICS")
    lines.append("─" * 72)

    # Aggregate win rates
    base_wr = total_base_wins / total_base_trades if total_base_trades else 0.0
    imp_wr = total_imp_wins / total_imp_trades if total_imp_trades else 0.0
    base_tp1r = total_base_tp1 / total_base_trades if total_base_trades else 0.0
    imp_tp1r = total_imp_tp1 / total_imp_trades if total_imp_trades else 0.0
    avg_exp_gain = float(np.mean(exp_gains)) if exp_gains else 0.0
    avg_r_gain = float(np.mean(r_gains)) if r_gains else 0.0

    lines.append(f"  Total Trades:     Baseline {total_base_trades:>4}  →  Improved {total_imp_trades:>4}")
    lines.append(f"  Win Rate:         Baseline {base_wr*100:.1f}%     →  Improved {imp_wr*100:.1f}%  (Δ {(imp_wr-base_wr)*100:+.1f}pp)")
    lines.append(f"  TP1 Hit Rate:     Baseline {base_tp1r*100:.1f}%     →  Improved {imp_tp1r*100:.1f}%  (Δ {(imp_tp1r-base_tp1r)*100:+.1f}pp)")
    lines.append(f"  Avg Expectancy:   Baseline N/A         →  Improved N/A  (Δ ${avg_exp_gain:+.1f}/trade)")
    lines.append(f"  Avg R per Trade:  Δ {avg_r_gain:+.3f}R")

    lines.append("")
    lines.append("─" * 72)

    # Verdict
    improved_count = sum(1 for g in exp_gains if g > 0)
    total_compared = len(exp_gains)
    verdict_pct = improved_count / total_compared * 100 if total_compared else 0
    if verdict_pct >= 60 and avg_exp_gain > 0:
        lines.append("  ✅ VERDICT: Phase 2+ signal quality modifiers IMPROVE expectancy")
        lines.append(f"     {improved_count}/{total_compared} symbols benefit from improved scoring ({verdict_pct:.0f}%).")
    elif verdict_pct >= 40:
        lines.append("  ⚠️  VERDICT: Mixed results — modifiers need further calibration before activation.")
        lines.append(f"     {improved_count}/{total_compared} symbols benefit ({verdict_pct:.0f}%).")
    else:
        lines.append("  ❌ VERDICT: Improved scoring does not yet demonstrate consistent edge.")
        lines.append("     Keep weights at 0.0 until more data or calibration is available.")

    lines.append("=" * 72)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 5 Walk-Forward OOS Evaluation")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Symbols to evaluate (defaults to all fixtures found)")
    parser.add_argument("--timeframe", default="1h",
                        help="Fixture timeframe to load (default: 1h)")
    parser.add_argument("--train-bars", type=int, default=300,
                        help="Training window size in bars (default: 300)")
    parser.add_argument("--test-bars", type=int, default=100,
                        help="OOS test window size in bars (default: 100)")
    parser.add_argument("--step-bars", type=int, default=80,
                        help="Step between windows in bars (default: 80)")
    parser.add_argument("--output", default=None,
                        help="Optional path to save JSON results")
    args = parser.parse_args()

    symbols = args.symbols or list_available_symbols(args.timeframe)
    if not symbols:
        print(f"No fixtures found in {FIXTURE_DIR}. Run Phase 0 first.")
        sys.exit(1)

    print(f"\n🔬 Walk-Forward OOS Evaluation — {len(symbols)} symbols | "
          f"train={args.train_bars} / test={args.test_bars} / step={args.step_bars} bars")

    baseline_cfg = make_baseline_cfg()
    improved_cfg = make_improved_cfg()

    baseline_results = []
    improved_results = []

    for sym in symbols:
        df = load_fixture(sym, args.timeframe)
        if df is None or len(df) < args.train_bars + args.test_bars:
            print(f"  ⚠️  {sym}: fixture too short or missing, skipping")
            continue
        print(f"  Running {sym} ({len(df)} bars)...", end=" ", flush=True)
        br = evaluate_symbol(sym, df, baseline_cfg, args.train_bars, args.test_bars, args.step_bars)
        ir = evaluate_symbol(sym, df, improved_cfg, args.train_bars, args.test_bars, args.step_bars)
        baseline_results.append(br)
        improved_results.append(ir)
        print(f"baseline={br['trades']} trades / improved={ir['trades']} trades ✓")

    # Print comparison report
    print(format_comparison_report(baseline_results, improved_results))

    # Optionally save JSON
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        out = {
            "baseline": baseline_results,
            "improved": improved_results,
            "config": {
                "train_bars": args.train_bars,
                "test_bars": args.test_bars,
                "step_bars": args.step_bars,
                "timeframe": args.timeframe,
                "symbols": symbols,
            }
        }
        with open(args.output, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n💾 Results saved → {args.output}")


if __name__ == "__main__":
    main()
