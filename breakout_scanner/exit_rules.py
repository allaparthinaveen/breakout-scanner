"""
Unified Exit Rules Engine for Paper Positions and Outcomes Tracking.
===================================================================
Defines a single, deterministic, pure-function evaluation of a position against
closed price bars. Shared by both live PortfolioStateManager and OutcomeTracker
to guarantee absolute parity across Telegram reporting, persistent positions,
and forward-test outcomes.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


def evaluate_position_bar(
    position: Dict[str, Any],
    bar: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates a single closed candle against a position record.

    Order of execution inside bar:
    1. Fill check (if awaiting fill): next-bar open + friction (5 bps)
    2. Stop Loss check (gap-aware, intrabar Low/High) -> STOP_LOSS or BREAKEVEN_STOP (terminal)
    3. Failed Breakout check (within failed_breakout_max_bars, Close < pivot for BUY) -> FAILED_BREAKOUT (terminal)
    4. Take Profit 1 check (High >= TP1 for BUY) -> TP1_SCALE (50% scale, stop to breakeven, non-terminal)
    5. Take Profit 2 check (High >= TP2 for BUY, if TP1 hit) -> TP2_EXIT (terminal)
    6. Time Exit check (bars_held >= max_hold_bars) -> TIME_EXIT (terminal)
    7. Mark-to-market updates for remaining open exposure

    Guarantees:
    - Reject second terminal state (frozen once CLOSED)
    - Returns list of event dicts emitted during this bar
    """
    events: List[Dict[str, Any]] = []

    # 0. Terminal Guard: Reject mutations on already closed positions
    if position.get("status") == "CLOSED" or position.get("exit_reason") is not None:
        return events

    direction = int(position.get("direction", 1))
    c_mgmt = cfg.get("management", {}) if cfg else {}
    c_exec = cfg.get("execution", {}) if cfg else {}

    bar_ts = bar.get("bar_ts") or bar.get("timestamp") or bar.get("Date") or ""
    bar_ts_str = str(bar_ts)

    open_px = float(bar["Open"])
    high_px = float(bar["High"])
    low_px = float(bar["Low"])
    close_px = float(bar["Close"])
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Fill Check (Next-Bar Open + Friction)
    if position.get("status") == "AWAITING_FILL" or position.get("modeled_fill") is None:
        friction_bps = float(c_exec.get("friction_bps", 5.0))
        friction_mult = 1.0 + (friction_bps / 10000.0) * direction
        fill_px = open_px * friction_mult

        position["modeled_fill"] = fill_px
        position["entry_price"] = fill_px
        position["fill_bar_ts"] = bar_ts_str
        position["fill_basis"] = "NEXT_BAR_OPEN"
        position["status"] = "OPEN"
        position["bars_held"] = 1

        initial_stop = float(position.get("stop_loss") or position.get("stop") or 0.0)
        position["initial_stop"] = initial_stop
        position["initial_risk"] = abs(fill_px - initial_stop) or 1.0

        fill_ev = {
            "event_type": "FILL",
            "price": fill_px,
            "shares": int(position.get("shares", 0)),
            "bar_ts": bar_ts_str,
            "timestamp": now_iso,
            "reason": "Filled at Next-Bar Open + 5 bps friction",
        }
        position.setdefault("events", []).append(fill_ev)
        events.append(fill_ev)
    else:
        position["bars_held"] = int(position.get("bars_held", 0)) + 1

    bars_held = int(position["bars_held"])
    entry = float(position.get("modeled_fill") or position.get("entry_price") or open_px)
    active_stop = float(position.get("stop_loss") or position.get("stop") or entry)
    initial_risk = float(position.get("initial_risk") or abs(entry - active_stop) or 1.0)
    pivot = float(position.get("pivot") or position.get("resistance") or position.get("breakout_level") or entry)
    shares = int(position.get("shares", 1))

    failed_max_bars = int(c_mgmt.get("failed_breakout_max_bars", 3))
    use_failed_exit = bool(c_mgmt.get("use_failed_breakout_exit", True))
    max_hold_bars = int(c_mgmt.get("max_hold_bars", 70))
    tp1 = float(position.get("take_profit_1") or position.get("tp1")) if (position.get("take_profit_1") or position.get("tp1")) else None
    tp2 = float(position.get("take_profit_2") or position.get("tp2")) if (position.get("take_profit_2") or position.get("tp2")) else None

    # 2. Stop Loss Check (Gap-Aware & Intrabar)
    hit_stop = (low_px <= active_stop) if direction == 1 else (high_px >= active_stop)
    if hit_stop:
        if direction == 1:
            exit_px = min(open_px, active_stop)
        else:
            exit_px = max(open_px, active_stop)

        is_post_tp1 = bool(position.get("tp1_hit", False) or position.get("status") == "TP1_HIT")
        if is_post_tp1:
            ev_type = "BREAKEVEN_STOP"
            realized_r = 0.50
            reason = "Breakeven Stop Hit after TP1"
            pnl = (exit_px - entry) * shares * direction
            pnl_pct = ((exit_px - entry) / entry * 100.0 * direction) if entry > 0 else 0.0
        else:
            ev_type = "STOP_LOSS"
            realized_r = round(((exit_px - entry) / initial_risk * direction), 2)
            reason = "Stop Loss Triggered"
            pnl = (exit_px - entry) * shares * direction
            pnl_pct = ((exit_px - entry) / entry * 100.0 * direction) if entry > 0 else 0.0

        total_realized_pnl = round(position.get("banked_pnl", 0.0) + pnl, 2)
        position["status"] = "CLOSED"
        position["exit_reason"] = ev_type
        position["exit_price"] = exit_px
        position["exit_bar_ts"] = bar_ts_str
        position["closed_at"] = now_iso
        position["realized_r"] = realized_r
        position["realized_pnl"] = total_realized_pnl
        position["pnl_pct"] = round(pnl_pct, 2)
        position["last_processed_bar_ts"] = bar_ts_str

        ev = {
            "event_type": ev_type,
            "exit_price": exit_px,
            "realized_r": realized_r,
            "realized_pnl": total_realized_pnl,
            "pnl_pct": round(pnl_pct, 2),
            "shares": shares,
            "bar_ts": bar_ts_str,
            "timestamp": now_iso,
            "reason": reason,
        }
        position.setdefault("events", []).append(ev)
        events.append(ev)
        return events

    # 3. Failed Breakout Check (within N bars post-fill, close < pivot)
    if use_failed_exit and bars_held <= failed_max_bars and not position.get("tp1_hit", False):
        hit_failed = (close_px < pivot) if direction == 1 else (close_px > pivot)
        if hit_failed:
            exit_px = close_px
            realized_r = round(((exit_px - entry) / initial_risk * direction), 2)
            pnl = (exit_px - entry) * shares * direction
            pnl_pct = ((exit_px - entry) / entry * 100.0 * direction) if entry > 0 else 0.0
            total_realized_pnl = round(position.get("banked_pnl", 0.0) + pnl, 2)

            position["status"] = "CLOSED"
            position["exit_reason"] = "FAILED_BREAKOUT"
            position["exit_price"] = exit_px
            position["exit_bar_ts"] = bar_ts_str
            position["closed_at"] = now_iso
            position["realized_r"] = realized_r
            position["realized_pnl"] = total_realized_pnl
            position["pnl_pct"] = round(pnl_pct, 2)
            position["last_processed_bar_ts"] = bar_ts_str

            ev = {
                "event_type": "FAILED_BREAKOUT",
                "exit_price": exit_px,
                "realized_r": realized_r,
                "realized_pnl": total_realized_pnl,
                "pnl_pct": round(pnl_pct, 2),
                "shares": shares,
                "bar_ts": bar_ts_str,
                "timestamp": now_iso,
                "reason": f"FAILED_BREAKOUT: Closed back inside pivot ({close_px:.2f} vs {pivot:.2f}) on bar {bars_held}",
            }
            position.setdefault("events", []).append(ev)
            events.append(ev)
            return events

    # 4. Take Profit 1 Check (Scale 50%, stop to breakeven)
    if not position.get("tp1_hit", False) and tp1 is not None:
        hit_tp1 = (high_px >= tp1) if direction == 1 else (low_px <= tp1)
        if hit_tp1:
            position["tp1_hit"] = True
            position["status"] = "TP1_HIT"
            half_shares = shares // 2 if shares > 1 else shares
            pnl = (tp1 - entry) * half_shares * direction
            pnl_pct = ((tp1 - entry) / entry * 100.0 * direction) if entry > 0 else 0.0

            be_offset_pct = float(c_mgmt.get("breakeven_offset_pct", 0.10)) / 100.0
            new_stop = entry * (1.0 + be_offset_pct * direction)
            position["stop_loss"] = new_stop
            position["stop"] = new_stop
            position["shares"] = max(1, shares - half_shares) if shares > 1 else shares
            position["banked_pnl"] = round(position.get("banked_pnl", 0.0) + pnl, 2)

            ev = {
                "event_type": "TP1_SCALE",
                "exit_price": tp1,
                "realized_r": 1.0,
                "realized_pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "shares": half_shares,
                "bar_ts": bar_ts_str,
                "timestamp": now_iso,
                "reason": "TP1 Hit: Scaled 50% & Trailed Stop to Breakeven",
            }
            position.setdefault("events", []).append(ev)
            events.append(ev)

    # 5. Take Profit 2 Check (Full target exit)
    if position.get("tp1_hit", False) and tp2 is not None:
        hit_tp2 = (high_px >= tp2) if direction == 1 else (low_px <= tp2)
        if hit_tp2:
            exit_px = tp2
            rem_shares = position.get("shares", 1)
            pnl = (exit_px - entry) * rem_shares * direction
            total_realized_pnl = round(position.get("banked_pnl", 0.0) + pnl, 2)
            pnl_pct = ((exit_px - entry) / entry * 100.0 * direction) if entry > 0 else 0.0
            realized_r = 1.50

            position["status"] = "CLOSED"
            position["exit_reason"] = "TP2_EXIT"
            position["exit_price"] = exit_px
            position["exit_bar_ts"] = bar_ts_str
            position["closed_at"] = now_iso
            position["realized_r"] = realized_r
            position["realized_pnl"] = total_realized_pnl
            position["pnl_pct"] = round(pnl_pct, 2)
            position["last_processed_bar_ts"] = bar_ts_str

            ev = {
                "event_type": "TP2_EXIT",
                "exit_price": exit_px,
                "realized_r": realized_r,
                "realized_pnl": total_realized_pnl,
                "pnl_pct": round(pnl_pct, 2),
                "shares": rem_shares,
                "bar_ts": bar_ts_str,
                "timestamp": now_iso,
                "reason": "Take Profit 2 Hit: Full Target Reached",
            }
            position.setdefault("events", []).append(ev)
            events.append(ev)
            return events

    # 6. Time Exit Check (Max hold duration)
    if bars_held >= max_hold_bars:
        exit_px = close_px
        rem_shares = position.get("shares", 1)
        pnl = (exit_px - entry) * rem_shares * direction
        total_realized_pnl = round(position.get("banked_pnl", 0.0) + pnl, 2)
        pnl_pct = ((exit_px - entry) / entry * 100.0 * direction) if entry > 0 else 0.0
        rem_r = ((exit_px - entry) / initial_risk * direction)
        realized_r = round(0.50 * 1.0 + 0.50 * rem_r, 2) if position.get("tp1_hit") else round(rem_r, 2)

        time_ev_type = c_mgmt.get("time_exit_name", "TIME_STOP_10D")
        position["status"] = "CLOSED"
        position["exit_reason"] = time_ev_type
        position["exit_price"] = exit_px
        position["exit_bar_ts"] = bar_ts_str
        position["closed_at"] = now_iso
        position["realized_r"] = realized_r
        position["realized_pnl"] = total_realized_pnl
        position["pnl_pct"] = round(pnl_pct, 2)
        position["last_processed_bar_ts"] = bar_ts_str

        ev = {
            "event_type": time_ev_type,
            "exit_price": exit_px,
            "realized_r": realized_r,
            "realized_pnl": total_realized_pnl,
            "pnl_pct": round(pnl_pct, 2),
            "shares": rem_shares,
            "bar_ts": bar_ts_str,
            "timestamp": now_iso,
            "reason": f"TIME_EXIT: Maximum hold period ({max_hold_bars} bars) reached",
        }
        position.setdefault("events", []).append(ev)
        events.append(ev)
        return events

    # 7. Mark-to-market updates for still-open position
    rem_shares = position.get("shares", 1)
    unrealized_pnl = round((close_px - entry) * rem_shares * direction, 2)
    position["current_price"] = close_px
    position["unrealized_pnl"] = unrealized_pnl
    position["pnl_pct"] = round(((close_px - entry) / entry * 100.0 * direction), 2) if entry > 0 else 0.0
    position["last_processed_bar_ts"] = bar_ts_str

    return events
