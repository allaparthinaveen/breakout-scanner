"""
Unified Forward-Test Audit Ledger & Provenance Engine (Schema v1.1).
Records every evaluated setup (Approved, Provisional, Suppressed, Rejected) across both
CLI (scan_now.py) and Daemon/Scheduler (slate.py / production_daemon.py).

Features:
1. Deduplication Guard: Enforces single-entry logging per unique breakout signal_id.
2. Signal-Only Gate: Filters out passive contraction bases and aged in-progress positions.
3. Provisional Quarantine: Labels provisional setups as PROVISIONAL with a technical quality floor (>=50).
4. Config Freeze Tracking: Stamps a SHA256 config hash on every record.
5. Provenance & Metadata: Stamps schema_version ("v1.1"), scan_id, git_sha, and deterministic signal_id.
6. Execution Fidelity: Logs intended fill, actual fill, slippage, and allocated shares/dollar risk.
"""

import os
import json
import uuid
import hashlib
import subprocess
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .models import Setup, State

_CACHED_GIT_SHA: Optional[str] = None


def get_git_sha() -> str:
    """Retrieves current git commit short SHA or fallback."""
    global _CACHED_GIT_SHA
    if _CACHED_GIT_SHA is not None:
        return _CACHED_GIT_SHA
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8").strip()
        _CACHED_GIT_SHA = sha or "UNKNOWN"
    except Exception:
        _CACHED_GIT_SHA = os.environ.get("GIT_SHA", "UNKNOWN")
    return _CACHED_GIT_SHA


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
    Appends structured audit lines to JSONL ledger for new candidate trade signals.
    Called uniformly from scan_now.py and slate.py / scheduler.py.
    Returns the count of records appended.
    """
    if not setups:
        return 0

    log_dir = os.path.dirname(ledger_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 1. Load existing signal_ids to enforce strict deduplication
    existing_signals = set()
    if os.path.exists(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as lf:
            for line in lf:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    sig = obj.get("signal_id")
                    if sig:
                        existing_signals.add(sig)
                except Exception:
                    pass

    config_hash = compute_config_hash(cfg)
    timestamp = datetime.now(timezone.utc).isoformat()
    scan_id = getattr(slate, "slate_id", None) or f"scan_{uuid.uuid4().hex[:10]}"
    git_sha = get_git_sha()

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
            # Trade ledger strictly records candidate trade breakouts (BUY/SELL).
            # Passive VCP bases (CONTRACTION) are intentionally omitted.
            if s.state not in (State.BUY, State.SELL):
                continue

            entry_price = s.entry or s.current_price
            stop_price = s.stop
            # Must possess valid price geometry to prevent 0.0R junk rows
            if not entry_price or not stop_price or entry_price == stop_price:
                continue

            # Only record fresh breakouts or candidates actively evaluated by slate
            is_evaluated_by_slate = (
                s.symbol in approved_symbols
                or s.symbol in rejected_map
                or s.symbol in suppressed_map
            )
            if not s.is_fresh_entry and not is_evaluated_by_slate:
                # In-progress setups that broke out in prior closed bars are not new candidates
                continue

            dir_str = "BUY" if s.direction == 1 else ("SELL" if s.direction == -1 else "NEUTRAL")
            sig_time = getattr(s, "breakout_time", None) or timestamp
            signal_id = f"{s.symbol}_{sig_time}_{dir_str}".replace(" ", "_").replace(":", "-")

            # Deduplication: Skip if this specific breakout signal was already logged
            if signal_id in existing_signals:
                continue

            # Classify signal status with dedicated PROVISIONAL quarantine
            is_provisional = getattr(s, "provisional_data_incomplete", False)
            quant_score = getattr(s, "composite_score", 0) or 0

            if s.symbol in rejected_map:
                status = "REJECTED"
                code = rejected_map[s.symbol]["code"]
                reason = rejected_map[s.symbol]["reason"]
            elif s.symbol in suppressed_map:
                status = "SUPPRESSED"
                code = suppressed_map[s.symbol]["code"]
                reason = suppressed_map[s.symbol]["reason"]
            elif is_provisional:
                # Technical-only quality gate: throttled setups with low technicals are suppressed
                if quant_score < 50:
                    status = "SUPPRESSED"
                    code = "PROVISIONAL_LOW_TECHNICAL_SCORE"
                    reason = f"Provisional technical score {quant_score} below minimum quality threshold (50)"
                else:
                    status = "PROVISIONAL"
                    code = "ACTIONABLE_PROVISIONAL_ENTRY" if s.symbol in approved_symbols else "PROVISIONAL_CANDIDATE"
                    reason = "Provisional technical breakout with incomplete external market data"
            elif s.symbol in approved_symbols:
                status = "APPROVED"
                code = "ACTIONABLE_SLATE_ENTRY"
                reason = "Approved for execution by unified slate pipeline"
            elif getattr(s, "requires_confirmation", False):
                min_score = int(cfg.get("confirmation_policy", {}).get("min_auto_entry_score", 70))
                status = "SUPPRESSED"
                code = "AWAITING_CONFIRMATION"
                reason = f"Score {quant_score} < {min_score} requires follow-through confirmation"
            elif not s.is_fresh_entry:
                status = "SUPPRESSED"
                code = "LATE_CHASE"
                reason = "Price extended beyond pivot tolerance (Anti-Chase Guard)"
            else:
                status = "APPROVED"
                code = "ACTIONABLE_CANDIDATE"
                reason = "Pristine technical breakout candidate"

            target_1 = s.tp1
            target_2 = s.tp2

            # Populate execution details
            alloc = None
            if slate is not None and hasattr(slate, "selected_trades"):
                alloc = next((a for a in slate.selected_trades if a.symbol == s.symbol), None)

            if status in ("APPROVED", "PROVISIONAL") and alloc is not None:
                intended_fill = float(alloc.entry_price) if getattr(alloc, "entry_price", None) else float(entry_price)
                actual_fill = float(s.current_price) if s.current_price is not None else intended_fill
                slippage_pct = round(((actual_fill - intended_fill) / intended_fill) * 100.0, 3) if intended_fill > 0 else 0.0
                shares = int(getattr(alloc, "shares", 0))
                dollar_risk = float(getattr(alloc, "dollar_risk", 0.0))
            else:
                intended_fill = float(entry_price)
                actual_fill = None
                slippage_pct = None
                shares = 0
                dollar_risk = 0.0

            execution_dict = {
                "intended_fill": intended_fill,
                "actual_fill": actual_fill,
                "slippage_pct": slippage_pct,
                "shares": shares,
                "dollar_risk": dollar_risk,
                "exit_price": None,
                "exit_reason": None,
                "realized_r": None,
            }

            record = {
                "schema_version": "v1.1",
                "scan_id": scan_id,
                "signal_id": signal_id,
                "git_sha": git_sha,
                "timestamp": timestamp,
                "config_hash": config_hash,
                "profile_name": cfg.get("profile_name", "Standard"),
                "symbol": s.symbol,
                "direction": dir_str,
                "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                "status": status,
                "code": code,
                "reason": reason,
                "quant_score": quant_score,
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
                    "earnings_status": getattr(s, "earnings_data_status", "UNKNOWN"),
                    "provisional": is_provisional,
                },
                "shadow_features": {
                    "is_above_daily_sma50": getattr(s, "is_above_daily_sma50", None),
                },
                "execution": execution_dict,
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
            existing_signals.add(signal_id)
            records_written += 1

    return records_written
