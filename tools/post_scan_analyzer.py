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

DEFAULT_OUTCOMES_FILE = "data/telemetry/candidate_outcomes.jsonl"


class PostScanAnalyzer:
    """
    Evaluates the forward performance (5-10 bars) of emitted breakout setups.
    """
    def __init__(
        self,
        forward_bars: int = 10,
        output_file: str = DEFAULT_OUTCOMES_FILE
    ):
        self.forward_bars = forward_bars
        self.output_file = output_file
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)

    def evaluate_candidate_forward(
        self,
        candidate: Dict[str, Any],
        future_candles: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Evaluate an individual candidate forward over the provided future candles.
        """
        entry = candidate.get("entry")
        stop = candidate.get("stop")
        tp1 = candidate.get("tp1")
        tp2 = candidate.get("tp2")
        direction = candidate.get("direction", 1)
        if direction not in (1, -1):
            direction = 1 if (entry is not None and stop is not None and entry > stop) else -1

        risk = candidate.get("risk")
        if risk is None or risk <= 0:
            if entry is not None and stop is not None:
                risk = abs(entry - stop)
            else:
                risk = 1.0

        if future_candles.empty or entry is None or stop is None:
            return {
                **candidate,
                "outcome": "INSUFFICIENT_DATA",
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "realized_r": 0.0,
                "bars_tracked": 0,
                "t1_hit": False,
                "stopped_out": False
            }

        bars_to_check = future_candles.iloc[:self.forward_bars]
        bars_tracked = len(bars_to_check)

        t1_hit = False
        t1_bar = None
        t2_hit = False
        stopped_out = False
        stop_bar = None

        mfe_r = 0.0
        mae_r = 0.0

        for idx, (_, row) in enumerate(bars_to_check.iterrows(), 1):
            high = row["High"]
            low = row["Low"]
            close = row["Close"]

            if direction == 1:
                # Long calculations
                favorable = (high - entry) / risk
                adverse = (entry - low) / risk
                mfe_r = max(mfe_r, float(favorable))
                mae_r = max(mae_r, float(adverse))

                if tp1 is not None and high >= tp1 and not t1_hit:
                    t1_hit = True
                    t1_bar = idx

                if tp2 is not None and high >= tp2 and not t2_hit:
                    t2_hit = True

                if low <= stop and not stopped_out:
                    stopped_out = True
                    stop_bar = idx
                    # If stopped out before or on the bar without TP1 hit first, stop exits trade
                    if not t1_hit:
                        break
            else:
                # Short calculations
                favorable = (entry - low) / risk
                adverse = (high - entry) / risk
                mfe_r = max(mfe_r, float(favorable))
                mae_r = max(mae_r, float(adverse))

                if tp1 is not None and low <= tp1 and not t1_hit:
                    t1_hit = True
                    t1_bar = idx

                if tp2 is not None and low <= tp2 and not t2_hit:
                    t2_hit = True

                if high >= stop and not stopped_out:
                    stopped_out = True
                    stop_bar = idx
                    if not t1_hit:
                        break

        # Determine definitive outcome
        if t1_hit and (not stopped_out or (stop_bar is not None and t1_bar is not None and t1_bar <= stop_bar)):
            outcome = "TP1_REACHED"
            realized_r = 1.0
        elif stopped_out:
            outcome = "STOPPED_OUT"
            realized_r = -1.0
        else:
            final_close = bars_to_check["Close"].iloc[-1]
            end_pnl_r = ((final_close - entry) / risk) if direction == 1 else ((entry - final_close) / risk)
            outcome = "EXPIRED_POSITIVE" if end_pnl_r > 0 else "EXPIRED_NEGATIVE"
            realized_r = round(float(end_pnl_r), 2)

        result = {
            "symbol": candidate.get("symbol"),
            "scan_timestamp": candidate.get("timestamp"),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "timeframe": candidate.get("timeframe", "1h"),
            "state": candidate.get("state"),
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "risk": round(float(risk), 4),
            "composite_score": candidate.get("composite_score", 0),
            "composite_grade": candidate.get("composite_grade", ""),
            "bias_score": candidate.get("bias_score", 0),
            "base_width_pct": candidate.get("base_width_pct"),
            "decay_ratio": candidate.get("decay_ratio"),
            "tightening_confirmed": candidate.get("tightening_confirmed", False),
            "vdu_ratio": candidate.get("vdu_ratio"),
            "vdu_confirmed": candidate.get("vdu_confirmed", False),
            "rvol": candidate.get("rvol"),
            "htf_confirmed": candidate.get("htf_confirmed", False),
            "outcome": outcome,
            "t1_hit": t1_hit,
            "t1_bar": t1_bar,
            "stopped_out": stopped_out,
            "stop_bar": stop_bar,
            "mfe_r": round(float(mfe_r), 2),
            "mae_r": round(float(mae_r), 2),
            "realized_r": round(float(realized_r), 2),
            "bars_tracked": bars_tracked
        }
        return result

    def analyze_historical_dataset(
        self,
        symbols_data: Dict[str, pd.DataFrame],
        cfg: Optional[Dict[str, Any]] = None,
        stride_bars: int = 10,
        warmup_bars: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Walk through historical symbols, trigger BreakoutEngine at interval strides,
        and track forward 5-10 bars for every discovered candidate.
        """
        if cfg is None:
            cfg = load_config("config/default.yaml")

        all_outcomes = []

        for sym, df in symbols_data.items():
            if len(df) < (warmup_bars + self.forward_bars):
                continue

            for t in range(warmup_bars, len(df) - self.forward_bars, stride_bars):
                hist_slice = df.iloc[:t]
                future_slice = df.iloc[t: t + self.forward_bars]

                engine = BreakoutEngine(cfg, sym)
                setup = engine.run(hist_slice)

                # Track setups that are in active coiling base or breakout
                if setup.state in (State.CONTRACTION, State.BUY, State.SELL):
                    entry_val = setup.entry or setup.resistance or setup.current_price
                    stop_val = setup.stop or (entry_val * 0.98 if entry_val else None)
                    tp1_val = setup.tp1 or (entry_val + abs(entry_val - stop_val) if entry_val and stop_val else None)

                    cand_dict = {
                        "symbol": sym,
                        "timestamp": str(hist_slice.index[-1]),
                        "timeframe": "1h",
                        "state": setup.state.name,
                        "direction": setup.direction if setup.direction != 0 else 1,
                        "entry": entry_val,
                        "stop": stop_val,
                        "tp1": tp1_val,
                        "tp2": setup.tp2,
                        "risk": abs(entry_val - stop_val) if (entry_val and stop_val) else 1.0,
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
    max_symbols: int = 10,
    forward_bars: int = 10,
    stride_bars: int = 25,
    max_bars_per_symbol: int = 1000
) -> List[Dict[str, Any]]:
    """Helper to run post-scan analyzer across cached pickle files."""
    analyzer = PostScanAnalyzer(forward_bars=forward_bars)
    symbols_data = {}

    pkl_files = [f for f in sorted(os.listdir(cache_dir)) if f.endswith(".pkl") and not f.startswith("^")][:max_symbols]
    for pkl in pkl_files:
        sym = pkl.split("_")[0]
        path = os.path.join(cache_dir, pkl)
        try:
            df = pd.read_pickle(path)
            if not df.empty:
                symbols_data[sym] = df.iloc[-max_bars_per_symbol:]
        except Exception:
            pass

    print(f"Loaded {len(symbols_data)} symbols from {cache_dir}. Analyzing forward outcomes...")
    return analyzer.analyze_historical_dataset(symbols_data, stride_bars=stride_bars)
