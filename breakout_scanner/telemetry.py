"""
Telemetry and metrics logging module (Phase 0 Baseline & Safety Net).
Captures scan-level metrics, candidate counts, and reject reason histograms
strictly for observability without altering core engine logic.
"""

import os
import json
import csv
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import numpy as np

from .models import Setup, State

DEFAULT_TELEMETRY_DIR = "data/telemetry"
METRICS_JSONL_FILE = "metrics_history.jsonl"
METRICS_CSV_FILE = "metrics_daily.csv"
REJECT_LOG_FILE = "reject_events.jsonl"
CANDIDATE_LOG_FILE = "candidate_events.jsonl"


class ScanMetricsLogger:
    """
    Lightweight, non-invasive metrics logger for breakout scanner runs.
    """
    def __init__(self, output_dir: str = DEFAULT_TELEMETRY_DIR):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.jsonl_path = os.path.join(self.output_dir, METRICS_JSONL_FILE)
        self.csv_path = os.path.join(self.output_dir, METRICS_CSV_FILE)
        self.reject_path = os.path.join(self.output_dir, REJECT_LOG_FILE)
        self.candidate_path = os.path.join(self.output_dir, CANDIDATE_LOG_FILE)

    def compute_metrics(
        self,
        setups: List[Setup],
        market: str = "us",
        timeframe: str = "1h",
        config_name: str = "default"
    ) -> Dict[str, Any]:
        """
        Compute standard scan metrics and reject reasons without changing setups.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        total_scanned = len(setups)

        state_counts = {
            "IDLE": 0,
            "CONTRACTION": 0,
            "BUY": 0,
            "SELL": 0,
            "FAILED": 0
        }
        grade_counts = {"A+": 0, "A": 0, "B": 0, "C": 0, "UNRATED": 0}
        reject_reasons = {
            "IDLE_NO_STRUCTURE": 0,
            "FAILED_BREAKOUT": 0,
            "LOOSE_RANGE_GT_10PCT": 0,
            "UNCOILED_DECAY_GT_0_70": 0,
            "NO_VOLUME_DRYUP": 0,
            "LOW_RVOL_BREAKOUT": 0,
            "COUNTER_OR_NO_HTF_ALIGNMENT": 0,
            "SUBOPTIMAL_GRADE_C": 0
        }

        ready_setups = []
        breakout_setups = []
        all_candidates = []

        for s in setups:
            state_name = s.state.name if hasattr(s.state, "name") else str(s.state)
            state_counts[state_name] = state_counts.get(state_name, 0) + 1

            grade = getattr(s, "composite_grade", "") or "UNRATED"
            grade_counts[grade] = grade_counts.get(grade, 0) + 1

            if s.state == State.CONTRACTION:
                ready_setups.append(s)
                all_candidates.append(s)
            elif s.state in (State.BUY, State.SELL):
                breakout_setups.append(s)
                all_candidates.append(s)
            else:
                # Classify non-candidate reject reason
                if s.state == State.IDLE:
                    reject_reasons["IDLE_NO_STRUCTURE"] += 1
                elif s.state == State.FAILED:
                    reject_reasons["FAILED_BREAKOUT"] += 1

            # Check individual quality gates for reject telemetry
            if s.base_width_pct is not None and s.base_width_pct > 10.0:
                reject_reasons["LOOSE_RANGE_GT_10PCT"] += 1
            if s.decay_ratio is not None and s.decay_ratio > 0.70:
                reject_reasons["UNCOILED_DECAY_GT_0_70"] += 1
            if hasattr(s, "vdu_confirmed") and not s.vdu_confirmed:
                reject_reasons["NO_VOLUME_DRYUP"] += 1
            if getattr(s, "rvol", None) is not None and s.rvol < 1.2:
                reject_reasons["LOW_RVOL_BREAKOUT"] += 1
            if getattr(s, "htf_confirmed", None) is False:
                reject_reasons["COUNTER_OR_NO_HTF_ALIGNMENT"] += 1
            if getattr(s, "composite_grade", "") == "C":
                reject_reasons["SUBOPTIMAL_GRADE_C"] += 1

        candidate_scores = [
            s.composite_score for s in all_candidates
            if getattr(s, "composite_score", None) is not None and np.isfinite(s.composite_score) and s.composite_score > 0
        ]
        avg_score = round(float(np.mean(candidate_scores)), 2) if candidate_scores else 0.0

        ready_scores = [
            s.composite_score for s in ready_setups
            if getattr(s, "composite_score", None) is not None and np.isfinite(s.composite_score) and s.composite_score > 0
        ]
        avg_ready_score = round(float(np.mean(ready_scores)), 2) if ready_scores else 0.0

        breakout_scores = [
            s.composite_score for s in breakout_setups
            if getattr(s, "composite_score", None) is not None and np.isfinite(s.composite_score) and s.composite_score > 0
        ]
        avg_breakout_score = round(float(np.mean(breakout_scores)), 2) if breakout_scores else 0.0

        record = {
            "timestamp": now_utc,
            "market": market,
            "timeframe": timeframe,
            "config": config_name,
            "total_scanned": total_scanned,
            "total_candidates": len(all_candidates),
            "ready_count": len(ready_setups),
            "breakout_count": len(breakout_setups),
            "avg_candidate_score": avg_score,
            "avg_ready_score": avg_ready_score,
            "avg_breakout_score": avg_breakout_score,
            "state_distribution": state_counts,
            "grade_distribution": grade_counts,
            "reject_histogram": reject_reasons,
            "ready_symbols": [s.symbol for s in ready_setups],
            "breakout_symbols": [s.symbol for s in breakout_setups]
        }
        return record

    def record_scan(
        self,
        setups: List[Setup],
        market: str = "us",
        timeframe: str = "1h",
        config_name: str = "default"
    ) -> Dict[str, Any]:
        """
        Record scan metrics to persistent storage (JSONL and CSV).
        """
        record = self.compute_metrics(setups, market, timeframe, config_name)

        # 1. Append to JSONL
        try:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            pass

        # 2. Append/Update CSV Summary
        try:
            csv_exists = os.path.exists(self.csv_path)
            fieldnames = [
                "timestamp", "market", "timeframe", "config",
                "total_scanned", "total_candidates", "ready_count", "breakout_count",
                "avg_candidate_score", "avg_ready_score", "avg_breakout_score"
            ]
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                if not csv_exists:
                    writer.writeheader()
                writer.writerow(record)
        except Exception:
            pass

        # 3. Append detailed reject events
        self.record_reject_events(setups, market=market, timeframe=timeframe)

        # 4. Append detailed candidate events for forward outcome tracking
        self.record_candidate_events(setups, market=market, timeframe=timeframe)

        return record

    def record_reject_events(
        self,
        setups: List[Setup],
        market: str = "us",
        timeframe: str = "1h"
    ) -> List[Dict[str, Any]]:
        """
        Record structured reject events for setups failing quality gates.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        events = []
        for s in setups:
            reasons = []
            if s.state == State.IDLE:
                reasons.append("IDLE_NO_STRUCTURE")
            elif s.state == State.FAILED:
                reasons.append("FAILED_BREAKOUT")

            if s.base_width_pct is not None and s.base_width_pct > 10.0:
                reasons.append("LOOSE_RANGE_GT_10PCT")
            if s.decay_ratio is not None and s.decay_ratio > 0.70:
                reasons.append("UNCOILED_DECAY_GT_0_70")
            if hasattr(s, "vdu_confirmed") and not s.vdu_confirmed:
                reasons.append("NO_VOLUME_DRYUP")
            if getattr(s, "rvol", None) is not None and s.rvol < 1.2:
                reasons.append("LOW_RVOL_BREAKOUT")
            if getattr(s, "htf_confirmed", None) is False:
                reasons.append("COUNTER_OR_NO_HTF_ALIGNMENT")
            if getattr(s, "composite_grade", "") == "C":
                reasons.append("SUBOPTIMAL_GRADE_C")

            if reasons:
                event = {
                    "timestamp": now_utc,
                    "symbol": s.symbol,
                    "market": market,
                    "timeframe": timeframe,
                    "state": s.state.name if hasattr(s.state, "name") else str(s.state),
                    "primary_reason": reasons[0],
                    "reasons": reasons,
                    "base_width_pct": round(float(s.base_width_pct), 3) if s.base_width_pct is not None else None,
                    "decay_ratio": round(float(s.decay_ratio), 2) if s.decay_ratio is not None else None,
                    "vdu_ratio": round(float(s.vdu_ratio), 2) if s.vdu_ratio is not None else None,
                    "composite_score": getattr(s, "composite_score", 0),
                    "composite_grade": getattr(s, "composite_grade", ""),
                    "htf_confirmed": getattr(s, "htf_confirmed", False)
                }
                events.append(event)

        if events:
            try:
                with open(self.reject_path, "a", encoding="utf-8") as f:
                    for ev in events:
                        f.write(json.dumps(ev) + "\n")
            except Exception:
                pass
        return events

    def record_candidate_events(
        self,
        setups: List[Setup],
        market: str = "us",
        timeframe: str = "1h"
    ) -> List[Dict[str, Any]]:
        """
        Record candidate setups (CONTRACTION, BUY, SELL) for forward outcome tracking.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        candidates = []
        for s in setups:
            if s.state in (State.CONTRACTION, State.BUY, State.SELL):
                entry_val = s.entry or s.current_price or s.resistance
                stop_val = s.stop
                tp1_val = s.tp1
                tp2_val = s.tp2
                risk_val = None
                if entry_val is not None and stop_val is not None and abs(entry_val - stop_val) > 0:
                    risk_val = round(abs(entry_val - stop_val), 4)

                cand = {
                    "timestamp": now_utc,
                    "symbol": s.symbol,
                    "market": market,
                    "timeframe": timeframe,
                    "state": s.state.name if hasattr(s.state, "name") else str(s.state),
                    "direction": s.direction,
                    "entry": round(float(entry_val), 4) if entry_val is not None else None,
                    "stop": round(float(stop_val), 4) if stop_val is not None else None,
                    "tp1": round(float(tp1_val), 4) if tp1_val is not None else None,
                    "tp2": round(float(tp2_val), 4) if tp2_val is not None else None,
                    "risk": risk_val,
                    "base_width_pct": round(float(s.base_width_pct), 3) if s.base_width_pct is not None else None,
                    "decay_ratio": round(float(s.decay_ratio), 2) if s.decay_ratio is not None else None,
                    "tightening_confirmed": getattr(s, "tightening_confirmed", False),
                    "vdu_ratio": round(float(s.vdu_ratio), 2) if s.vdu_ratio is not None else None,
                    "vdu_confirmed": getattr(s, "vdu_confirmed", False),
                    "rvol": round(float(s.rvol), 2) if getattr(s, "rvol", None) is not None else None,
                    "htf_confirmed": getattr(s, "htf_confirmed", False),
                    "composite_score": getattr(s, "composite_score", 0),
                    "composite_grade": getattr(s, "composite_grade", ""),
                    "bias_score": getattr(s, "bias_score", 0),
                    "breakout_type": getattr(s, "breakout_type", "BREAKOUT"),
                    "retest_confirmed": getattr(s, "retest_confirmed", False)
                }
                candidates.append(cand)

        if candidates:
            try:
                with open(self.candidate_path, "a", encoding="utf-8") as f:
                    for c in candidates:
                        f.write(json.dumps(c) + "\n")
            except Exception:
                pass
        return candidates

    def get_reject_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Read back recent recorded reject events."""
        if not os.path.exists(self.reject_path):
            return []
        records = []
        try:
            with open(self.reject_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            return []
        return records[-limit:]

    def get_candidate_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Read back recent recorded candidate events."""
        if not os.path.exists(self.candidate_path):
            return []
        records = []
        try:
            with open(self.candidate_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            return []
        return records[-limit:]

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read back recent recorded scan events."""
        if not os.path.exists(self.jsonl_path):
            return []
        records = []
        try:
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            return []
        return records[-limit:]


# Global helper instance
_telemetry_logger: Optional[ScanMetricsLogger] = None

def get_telemetry_logger() -> ScanMetricsLogger:
    global _telemetry_logger
    if _telemetry_logger is None:
        _telemetry_logger = ScanMetricsLogger()
    return _telemetry_logger

def log_scan_metrics(
    setups: List[Setup],
    market: str = "us",
    timeframe: str = "1h",
    config_name: str = "default"
) -> Dict[str, Any]:
    """Convenience helper to record scan metrics."""
    return get_telemetry_logger().record_scan(setups, market, timeframe, config_name)
