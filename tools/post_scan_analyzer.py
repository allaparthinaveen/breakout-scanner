"""
Phase 1: Post-Scan Forward Outcome Tracker (tools/post_scan_analyzer.py)

Tracks candidates emitted by the breakout scanner over the subsequent 5–10 bars to measure:
- Realized follow-through rate (TP1 hit vs Stopped Out)
- False-breakout rate (pierced resistance and collapsed)
- Maximum Favorable Excursion (MFE in R)
- Maximum Adverse Excursion (MAE in R)
- Realized R-multiple per candidate
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Ensure project root is in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import Setup, State
from breakout_scanner.simulation import simulate_forward_trade

DEFAULT_OUTCOMES_FILE = "data/telemetry/candidate_outcomes.jsonl"


class PostScanAnalyzer:
    """
    Evaluates the forward performance (5-10 bars) of emitted breakout setups
    using the unified simulation kernel.
    """
    def __init__(
        self,
        forward_bars: int = 10,
        output_file: str = DEFAULT_OUTCOMES_FILE,
        cfg: Optional[Dict[str, Any]] = None,
        use_partial_exits: bool = True
    ):
        self.forward_bars = forward_bars
        self.output_file = output_file
        self.cfg = cfg or load_config("config/default.yaml")
        self.use_partial_exits = use_partial_exits
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)

    def evaluate_candidate_forward(
        self,
        candidate: Dict[str, Any],
        future_candles: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Evaluate an individual candidate forward over the provided future candles
        using the unified institutional execution simulator.
        """
        candles_slice = future_candles.iloc[:self.forward_bars] if not future_candles.empty else future_candles
        return simulate_forward_trade(
            candidate=candidate,
            future_candles=candles_slice,
            cfg=self.cfg,
            use_partial_exits=self.use_partial_exits
        )

    def analyze_historical_dataset(
        self,
        symbols_data: Dict[str, pd.DataFrame],
        cfg: Optional[Dict[str, Any]] = None,
        stride_bars: int = 1,
        warmup_bars: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Walk through historical symbols bar-by-bar, trigger BreakoutEngine,
        and track forward 5-10 bars for every discovered candidate.
        Enforces:
        - Bar-by-bar chronological replay without missing triggers (stride_bars=1 default)
        - In-position symbol locking to eliminate duplicate overlapping executions
        - Strict rejection of missing/synthetic stops (no fake 2% stops)
        """
        if cfg is None:
            cfg = self.cfg or load_config("config/default.yaml")

        all_outcomes = []

        for sym, df in symbols_data.items():
            if len(df) < (warmup_bars + self.forward_bars):
                continue

            in_position_until = -1

            for t in range(warmup_bars, len(df) - self.forward_bars, stride_bars):
                # Suppress overlapping duplicate breakout triggers while active in trade
                if t < in_position_until:
                    continue

                hist_slice = df.iloc[:t]
                future_slice = df.iloc[t: t + self.forward_bars]

                engine = BreakoutEngine(cfg, sym)
                setup = engine.run(hist_slice)

                # Evaluate only actual triggered breakouts (BUY / SELL) to measure genuine trade expectancy
                if setup.state in (State.BUY, State.SELL):
                    # Rule 1.3: Reject candidates without structural stop or entry (NO synthetic fallbacks)
                    if setup.stop is None or setup.entry is None or setup.stop <= 0 or setup.entry <= 0:
                        continue

                    # Enrich confluence context (HTF 1D alignment, regime, sector alpha)
                    try:
                        from breakout_scanner.confluence import enrich_confluence_and_context
                        enrich_confluence_and_context(setup, hist_slice, cfg=cfg)
                    except Exception:
                        pass

                    from breakout_scanner.scorer import calculate_composite_score
                    quality_cfg = cfg.get('signal_quality', {}) if cfg else None
                    context_cfg = cfg.get('confluence_context', {}) if cfg else None
                    c_score, c_grade = calculate_composite_score(setup, quality_cfg=quality_cfg, context_cfg=context_cfg)
                    setup.composite_score = c_score
                    setup.composite_grade = c_grade

                    cand_dict = {
                        "symbol": sym,
                        "timestamp": str(hist_slice.index[-1]),
                        "timeframe": "1h",
                        "state": setup.state.name,
                        "direction": setup.direction if setup.direction != 0 else 1,
                        "entry": setup.entry,
                        "stop": setup.stop,
                        "tp1": setup.tp1,
                        "tp2": setup.tp2,
                        "risk": abs(setup.entry - setup.stop),
                        "resistance": setup.resistance,
                        "support": setup.support,
                        "base_width_pct": setup.base_width_pct,
                        "decay_ratio": setup.decay_ratio,
                        "tightening_confirmed": setup.tightening_confirmed,
                        "vdu_ratio": setup.vdu_ratio,
                        "vdu_confirmed": setup.vdu_confirmed,
                        "rvol": setup.rvol,
                        "htf_confirmed": setup.htf_confirmed,
                        "composite_score": setup.composite_score,
                        "composite_grade": setup.composite_grade,
                        "bias_score": setup.bias_score
                    }

                    outcome = self.evaluate_candidate_forward(cand_dict, future_slice)
                    all_outcomes.append(outcome)

                    # Lock symbol while trade is held to avoid duplicate event counting
                    held = outcome.get("held_bars", 1)
                    in_position_until = t + max(1, held)

        self._save_outcomes(all_outcomes)
        return all_outcomes

    def _save_outcomes(self, outcomes: List[Dict[str, Any]]):
        """Append outcome results to the persistent outcomes JSONL."""
        if not outcomes:
            return
        try:
            with open(self.output_file, "a", encoding="utf-8") as f:
                for out in outcomes:
                    f.write(json.dumps(out) + "\n")
        except Exception:
            pass


def run_historical_post_scan(
    cache_dir: str = "backtest/cache",
    symbols: Optional[List[str]] = None,
    max_symbols: int = 15,
    forward_bars: int = 10,
    stride_bars: int = 1,
    max_bars_per_symbol: int = 1000
) -> List[Dict[str, Any]]:
    """Helper to run post-scan analyzer across cached pickle files."""
    analyzer = PostScanAnalyzer(forward_bars=forward_bars)
    symbols_data = {}

    if not os.path.isdir(cache_dir):
        print(f"Cache directory {cache_dir} not found.")
        return []

    available = [f for f in sorted(os.listdir(cache_dir)) if f.endswith(".pkl") and not f.startswith("^")]

    if symbols is None:
        preferred = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "CRM", "CSCO", "JPM", "NFLX", "AMD", "QCOM", "INTC", "CAT", "DIS"]
        chosen_files = []
        for pref in preferred:
            matches = [f for f in available if f.startswith(f"{pref}_")]
            if matches:
                chosen_files.append(matches[0])
            if len(chosen_files) >= max_symbols:
                break
        if not chosen_files:
            chosen_files = available[:max_symbols]
    else:
        chosen_files = []
        for s in symbols:
            matches = [f for f in available if f.startswith(f"{s}_")]
            if matches:
                chosen_files.append(matches[0])

    for pkl in chosen_files:
        sym = pkl.split("_")[0]
        path = os.path.join(cache_dir, pkl)
        try:
            df = pd.read_pickle(path)
            if not df.empty:
                symbols_data[sym] = df.iloc[-max_bars_per_symbol:]
        except Exception:
            pass

    print(f"Loaded {len(symbols_data)} symbols ({', '.join(symbols_data.keys())}) from {cache_dir}. Analyzing forward outcomes...")
    return analyzer.analyze_historical_dataset(symbols_data, stride_bars=stride_bars)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run forward outcome tracking on historical breakouts")
    parser.add_argument("--cache-dir", type=str, default="backtest/cache")
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--output", type=str, default=DEFAULT_OUTCOMES_FILE)
    args = parser.parse_args()

    if os.path.exists(args.output):
        try:
            os.remove(args.output)
        except Exception:
            pass

    outcomes = run_historical_post_scan(cache_dir=args.cache_dir, max_symbols=args.max_symbols, stride_bars=args.stride)
    print(f"Successfully evaluated and saved {len(outcomes)} breakout outcomes to {args.output}")
