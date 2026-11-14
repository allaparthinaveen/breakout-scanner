"""
Unified Forward-Test Audit Ledger & Provenance Engine.
Records every evaluated setup (Approved, Suppressed, Rejected) across both
CLI (scan_now.py) and Daemon/Scheduler (slate.py / production_daemon.py).

Features:
1. Config Freeze Tracking: Stamps a SHA256 config hash on every record.
2. Data Health Status: Records htf_data_status, sector_data_status, regime_data_status, and provisional status.
3. Shadow Features: Logs is_above_daily_sma50 for forward statistical analysis without premature hard gates.
4. What-If Schema: Reserves fields for nightly outcome tracking (what price did afterwards).
5. Slippage & Execution Logging: Logs intended vs actual fill prices.
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .models import Setup, State


def compute_config_hash(cfg: Dict[str, Any]) -> str:
    """Computes a deterministic 12-char SHA256 hash of configuration to detect mid-test parameter drift."""
    try:
        cfg_str = json.dumps(cfg, sort_keys=True, default=str)
        return hashlib.sha256(cfg_str.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "UNKNOWN_HASH"


def record_forward_test_ledger(
    setups: List[Setup],
    cfg: Dict[str, Any],
    slate: Optional[Any] = None,
    ledger_path: str = "logs/forward_test_ledger.jsonl",
) -> int:
    """
    Appends structured audit lines to JSONL ledger for every active setup.
    Called uniformly from scan_now.py and slate.py / scheduler.py.
    Returns the count of records appended.
    """
    if not setups:
        return 0

    log_dir = os.path.dirname(ledger_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    config_hash = compute_config_hash(cfg)
    timestamp = datetime.now(timezone.utc).isoformat()

    # Build lookup dictionaries from slate if provided
    approved_symbols = set()
    suppressed_map = {}
    rejected_map = {}

    if slate is not None:
        if hasattr(slate, "selected_trades"):
            approved_symbols = {alloc.symbol for alloc in slate.selected_trades}
        if hasattr(slate, "suppressed_trades"):
            for supp in slate.suppressed_trades:
                suppressed_map[supp.symbol] = {
                    "code": getattr(supp, "suppression_code", "SUPPRESSED"),
                    "reason": getattr(supp, "reason", "Suppressed by portfolio guard"),
                }
        if hasattr(slate, "rejected_trades"):
            for rej in slate.rejected_trades:
                rejected_map[rej.symbol] = {
                    "code": getattr(rej, "rejection_code", "REJECTED"),
                    "reason": getattr(rej, "reason", "Disqualified by pre-trade risk gate"),
                }

    records_written = 0
    with open(ledger_path, "a", encoding="utf-8") as f:
        for s in setups:
            # We audit active signals (BUY/SELL) and high-conviction contraction setups
            if s.state not in (State.BUY, State.SELL) and not (s.state == State.CONTRACTION and s.bias_score >= 3):
                continue

            # Determine classification status
            if s.symbol in approved_symbols:
                status = "APPROVED"
                code = "ACTIONABLE_SLATE_ENTRY"
                reason = "Approved for execution by unified slate pipeline"
            elif s.symbol in rejected_map:
                status = "REJECTED"
                code = rejected_map[s.symbol]["code"]
                reason = rejected_map[s.symbol]["reason"]
            elif s.symbol in suppressed_map:
                status = "SUPPRESSED"
                code = suppressed_map[s.symbol]["code"]
                reason = suppressed_map[s.symbol]["reason"]
            elif s.is_tp1_reached:
                status = "IN_PROGRESS"
                code = "TP1_REACHED"
                reason = "TP1 already achieved; stop trailed to breakeven"
            elif not s.is_fresh_entry:
                status = "SUPPRESSED"
                code = "LATE_CHASE"
                reason = "Price extended beyond pivot tolerance (Anti-Chase Guard)"
            elif getattr(s, "requires_confirmation", False) and not getattr(s, "provisional_data_incomplete", False):
                min_score = int(cfg.get("confirmation_policy", {}).get("min_auto_entry_score", 70))
                status = "SUPPRESSED"
                code = "AWAITING_CONFIRMATION"
                reason = f"Score {getattr(s, 'composite_score', 0)} < {min_score} requires follow-through confirmation"
            else:
                status = "APPROVED"
                code = "ACTIONABLE_CANDIDATE"
                reason = "Pristine technical breakout candidate"

            entry_price = s.entry or s.current_price
            stop_price = s.stop
            target_1 = s.tp1
            target_2 = s.tp2

            record = {
                "timestamp": timestamp,
                "config_hash": config_hash,
                "profile_name": cfg.get("profile_name", "Standard"),
                "symbol": s.symbol,
                "direction": "BUY" if s.direction == 1 else ("SELL" if s.direction == -1 else "NEUTRAL"),
                "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                "status": status,
                "code": code,
                "reason": reason,
                "quant_score": s.composite_score,
                "quant_grade": s.composite_grade,
                "bias_score": s.bias_score,
                "price": {
                    "current": s.current_price,
                    "entry": entry_price,
                    "stop": stop_price,
                    "risk_pct": s.risk_pct,
                    "tp1": target_1,
                    "tp2": target_2,
                },
                "data_health": {
                    "htf_status": getattr(s, "htf_data_status", "UNKNOWN"),
                    "sector_status": getattr(s, "sector_data_status", "UNKNOWN"),
                    "regime_status": getattr(s, "regime_data_status", "UNKNOWN"),
                    "provisional": getattr(s, "provisional_data_incomplete", False),
                },
                "shadow_features": {
                    "is_above_daily_sma50": getattr(s, "is_above_daily_sma50", None),
                },
                "execution": {
                    "intended_fill": entry_price,
                    "actual_fill": None,
                    "slippage_pct": None,
                    "exit_price": None,
                    "exit_reason": None,
                    "realized_r": None,
                },
                "what_if_outcome": {
                    "evaluated": False,
                    "eval_timestamp": None,
                    "bars_elapsed": 0,
                    "max_favorable_excursion_r": None,
                    "max_adverse_excursion_r": None,
                    "hypothetical_r": None,
                    "hit_tp1": False,
                    "hit_stop": False,
                },
            }

            f.write(json.dumps(record) + "\n")
            records_written += 1

    return records_written
