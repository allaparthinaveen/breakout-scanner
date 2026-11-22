"""
Nightly What-If Outcome Tracker & Drift Verification Engine (Milestone 3).

Evaluates the forward-test audit ledger (logs/forward_test_ledger.jsonl):
1. Strictly APPEND-ONLY architecture: Reads forward_test_ledger.jsonl without modifying it.
2. Writes outcomes to dedicated logs/forward_test_outcomes.jsonl.
3. Realistic Next-Open Execution: Fills at next candle Open + 5 bps friction (no close-price cheating).
4. Continuous In-Progress Tracking: Evaluates trades across their full 10-day (70-bar) hold horizon;
   never locks outcomes prematurely after 2-3 bars.
5. True R-Maths: Accurately banks 50% at TP1 (+1.0R) and tracks the remaining half at breakeven / MTM.
6. Failed Breakout & Time Stops: Incorporates early 3-bar failure exits and 10-day expiration exits.
7. Provisional Quarantine: Isolates PROVISIONAL setups into their own metric bucket (provisional_avg_r)
   so they never pollute approved_avg_r.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from .providers.yfinance_provider import YFinanceProvider
from .providers.base import MarketDataProvider

logger = logging.getLogger("outcome_tracker")

DEFAULT_MAX_HOLD_BARS = 70  # 10 trading days of 1H candles (approx 7 hrs/day * 10 = 70)
FRESH_SCAN_TOLERANCE_MINUTES = 10


def evaluate_setup_outcome_from_df(
    record: Dict[str, Any],
    df: Optional[pd.DataFrame],
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> Dict[str, Any]:
    """
    Evaluates the hypothetical price path of a logged signal using realistic execution.
    - Entry: Next-bar Open + 5 bps friction
    - Stop / TP1 / TP2 / Time Stop / Failed Breakout exits
    - Accurately banks 0.50R at TP1 and tracks second half trailing stop / MTM
    - Status: IN_PROGRESS until a terminal exit occurs or 70 bars elapse
    """
    sym = record.get("symbol", "")
    direction = 1 if record.get("direction") == "BUY" else (-1 if record.get("direction") == "SELL" else 0)

    price_info = record.get("price", {})
    stated_stop = price_info.get("stop")
    tp1 = price_info.get("tp1")
    tp2 = price_info.get("tp2")
    resistance = price_info.get("resistance") or price_info.get("breakout_level")
    support = price_info.get("support") or price_info.get("breakout_level")

    if not stated_stop or direction == 0:
        return {
            "status": "FAILED",
            "evaluated": False,
            "reason": "INVALID_PRICE_GEOMETRY",
        }

    if df is None or df.empty:
        return {"status": "FAILED", "evaluated": False, "reason": "NO_PRICE_DATA"}

    try:
        # Infer bar length from median diff of df.index (fallback 1h)
        diffs = pd.Series(df.index).diff().dropna()
        bar_length = diffs.median() if not diffs.empty else pd.Timedelta(hours=1)
        if not isinstance(bar_length, pd.Timedelta) or pd.isna(bar_length):
            bar_length = pd.Timedelta(hours=1)

        breakout_str = record.get("breakout_time")
        sig_ts_str = record.get("timestamp")

        b_dt = None
        if breakout_str:
            try:
                b_dt = pd.to_datetime(breakout_str)
                if df.index.tz is not None:
                    if b_dt.tz is None:
                        b_dt = b_dt.tz_localize(df.index.tz)
                    else:
                        b_dt = b_dt.tz_convert(df.index.tz)
                else:
                    if b_dt.tz is not None:
                        b_dt = b_dt.tz_convert(None)
            except Exception:
                b_dt = None

        s_dt = None
        if sig_ts_str:
            try:
                s_dt = pd.to_datetime(sig_ts_str)
                if df.index.tz is not None:
                    if s_dt.tz is None:
                        s_dt = s_dt.tz_localize(df.index.tz)
                    else:
                        s_dt = s_dt.tz_convert(df.index.tz)
                else:
                    if s_dt.tz is not None:
                        s_dt = s_dt.tz_convert(None)
            except Exception:
                s_dt = None

        fill_idx = None
        fill_basis = "FALLBACK"

        if b_dt is not None:
            breakout_bar_end = b_dt + bar_length
            tolerance = pd.Timedelta(minutes=FRESH_SCAN_TOLERANCE_MINUTES)
            is_fresh = True
            if s_dt is not None and s_dt > (breakout_bar_end + tolerance):
                is_fresh = False

            if is_fresh:
                # Fresh: fill = first bar AFTER the breakout bar
                if b_dt in df.index:
                    sig_loc = df.index.get_loc(b_dt)
                    if isinstance(sig_loc, (int, np.integer)):
                        fill_idx = int(sig_loc) + 1
                        fill_basis = "BREAKOUT_NEXT_OPEN"
                else:
                    matching = np.where(df.index <= b_dt)[0]
                    if len(matching) > 0:
                        fill_idx = int(matching[-1]) + 1
                        fill_basis = "BREAKOUT_NEXT_OPEN"
            else:
                # Late: fill = first bar whose START is strictly greater than the scan timestamp
                if s_dt is not None:
                    matching_late = np.where(df.index > s_dt)[0]
                    if len(matching_late) > 0:
                        fill_idx = int(matching_late[0])
                        fill_basis = "SCAN_NEXT_OPEN"
                    else:
                        fill_idx = len(df)
                        fill_basis = "SCAN_NEXT_OPEN"

        if fill_idx is None:
            if s_dt is not None:
                matching_late = np.where(df.index > s_dt)[0]
                if len(matching_late) > 0:
                    fill_idx = int(matching_late[0])
                    fill_basis = "SCAN_NEXT_OPEN"
                else:
                    fill_idx = len(df)
                    fill_basis = "SCAN_NEXT_OPEN"
            else:
                fill_idx = 1
                fill_basis = "FALLBACK"

        if len(df) <= fill_idx:
            return {
                "status": "IN_PROGRESS",
                "evaluated": False,
                "bars_elapsed": 0,
                "hypothetical_r": 0.0,
                "reason": "AWAITING_NEXT_OPEN_FILL",
                "fill_basis": fill_basis,
            }

        # Realistic Execution: Enter at fill bar Open + 5 bps friction
        fill_bar = df.iloc[fill_idx]
        raw_fill = float(fill_bar["Open"])
        entry = raw_fill * (1.0005 if direction == 1 else 0.9995)
        stop = stated_stop

        initial_risk = abs(entry - stop)
        if initial_risk <= 0:
            return {
                "status": "FAILED",
                "evaluated": False,
                "reason": "ZERO_INITIAL_RISK",
                "fill_basis": fill_basis,
            }

        # Evaluation path begins from fill bar up to max_hold_bars
        eval_window = df.iloc[fill_idx: fill_idx + max_hold_bars]

        from .exit_rules import evaluate_position_bar

        pivot_val = (
            record.get("pivot")
            or price_info.get("pivot")
            or (resistance if direction == 1 else support)
            or price_info.get("entry")
            or entry
        )

        pos = {
            "symbol": sym,
            "direction": direction,
            "status": "OPEN",
            "entry_price": entry,
            "modeled_fill": entry,
            "fill_bar_ts": str(fill_bar.name if hasattr(fill_bar, 'name') else fill_idx),
            "fill_basis": fill_basis,
            "stop_loss": stop,
            "initial_stop": stop,
            "initial_risk": initial_risk,
            "pivot": float(pivot_val),
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "shares": 100,
            "bars_held": 0,
            "events": [],
        }

        is_legacy_record = bool(record.get("legacy", False))
        cfg_eval = {
            "management": {
                "failed_breakout_max_bars": 3,
                "use_failed_breakout_exit": (not is_legacy_record),
                "max_hold_bars": max_hold_bars,
                "breakeven_offset_pct": 0.0,
            }
        }

        mfe_r = 0.0
        mae_r = 0.0

        for bar_idx, (_, row) in enumerate(eval_window.iterrows()):
            open_px = float(row["Open"])
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])

            if direction == 1:
                curr_fe_r = (high - entry) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r
                curr_ae_r = (entry - low) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r
            else:
                curr_fe_r = (entry - low) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r
                curr_ae_r = (high - entry) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r

            bar_dict = {
                "Open": open_px,
                "High": high,
                "Low": low,
                "Close": close,
                "bar_ts": str(row.name if hasattr(row, 'name') else bar_idx),
            }
            evaluate_position_bar(pos, bar_dict, cfg=cfg_eval)
            if pos.get("status") == "CLOSED":
                break

        is_terminal = (pos.get("status") == "CLOSED")
        exit_reason = pos.get("exit_reason", "OPEN_TRADE")
        exit_r = float(pos.get("realized_r", 0.0) or 0.0)
        hit_tp1 = bool(pos.get("tp1_hit", False))
        hit_stop = (exit_reason in ("STOP_LOSS", "BREAKEVEN_STOP"))
        hit_tp2 = (exit_reason == "TP2_EXIT")
        failed_breakout = (exit_reason == "FAILED_BREAKOUT")

        # If not stopped or TP2, evaluate whether max hold horizon (10 days) reached
        last_close = float(eval_window["Close"].iloc[-1])
        rem_r = ((last_close - entry) / initial_risk) if direction == 1 else ((entry - last_close) / initial_risk)

        if not is_terminal:
            if len(eval_window) >= max_hold_bars:
                # 10-Day Time Stop Expired: Terminal Exit
                is_terminal = True
                exit_reason = "TIME_STOP_10D"
                if hit_tp1:
                    exit_r = round(0.50 * 1.0 + 0.50 * rem_r, 2)
                else:
                    exit_r = round(rem_r, 2)
            else:
                # Trade Still In-Progress: Calculate running Mark-to-Market R without finalizing
                is_terminal = False
                exit_reason = "OPEN_TRADE"
                if hit_tp1:
                    exit_r = round(0.50 * 1.0 + 0.50 * rem_r, 2)
                else:
                    exit_r = round(rem_r, 2)

        # Base (b): Hypothetical pullback-to-pivot entry
        pivot_val = record.get("pivot") or price_info.get("pivot") or (resistance if direction == 1 else support)
        pullback_filled = False
        pb_entry = float(pivot_val) if (pivot_val is not None and float(pivot_val) > 0) else None
        pb_initial_risk = abs(pb_entry - stop) if (pb_entry and stop and pb_entry != stop) else None
        pb_mfe_r = 0.0
        pb_mae_r = 0.0
        pb_hit_tp1 = False
        pb_hit_stop = False
        pb_hit_tp2 = False
        pb_exit_r = 0.0
        pb_exit_reason = "DID_NOT_PULLBACK"
        pb_active_stop = stop
        pb_is_terminal = False

        if pb_entry and pb_initial_risk and pb_initial_risk > 0:
            for pb_idx, (_, row) in enumerate(eval_window.iterrows()):
                open_px = float(row["Open"])
                high = float(row["High"])
                low = float(row["Low"])
                close = float(row["Close"])

                if not pullback_filled:
                    if direction == 1:
                        # Before fill: check if stop is hit first or price touches pivot
                        if low <= stop and stop >= pb_entry:
                            pb_exit_reason = "STOP_BREACHED_BEFORE_PULLBACK"
                            break
                        if low <= pb_entry:
                            pullback_filled = True
                            pb_active_stop = stop
                    elif direction == -1:
                        if high >= stop and stop <= pb_entry:
                            pb_exit_reason = "STOP_BREACHED_BEFORE_PULLBACK"
                            break
                        if high >= pb_entry:
                            pullback_filled = True
                            pb_active_stop = stop

                if pullback_filled:
                    if direction == 1:
                        curr_fe = (high - pb_entry) / pb_initial_risk
                        if curr_fe > pb_mfe_r:
                            pb_mfe_r = curr_fe
                        curr_ae = (pb_entry - low) / pb_initial_risk
                        if curr_ae > pb_mae_r:
                            pb_mae_r = curr_ae

                        # Stop hit
                        if low <= pb_active_stop:
                            pb_hit_stop = True
                            pb_is_terminal = True
                            if pb_hit_tp1:
                                pb_exit_r = 0.50
                                pb_exit_reason = "BREAKEVEN_STOP"
                            else:
                                pb_exit_reason = "STOP_LOSS"
                                pb_exit_r = round((open_px - pb_entry) / pb_initial_risk, 2) if open_px <= pb_active_stop else -1.00
                            break

                        # TP1
                        if tp1 and high >= tp1 and not pb_hit_tp1:
                            pb_hit_tp1 = True
                            pb_active_stop = pb_entry

                        # TP2
                        if tp2 and high >= tp2 and pb_hit_tp1:
                            pb_hit_tp2 = True
                            pb_is_terminal = True
                            pb_exit_r = 1.50
                            pb_exit_reason = "TP2_HIT"
                            break
                    elif direction == -1:
                        curr_fe = (pb_entry - low) / pb_initial_risk
                        if curr_fe > pb_mfe_r:
                            pb_mfe_r = curr_fe
                        curr_ae = (high - pb_entry) / pb_initial_risk
                        if curr_ae > pb_mae_r:
                            pb_mae_r = curr_ae

                        # Stop hit
                        if high >= pb_active_stop:
                            pb_hit_stop = True
                            pb_is_terminal = True
                            if pb_hit_tp1:
                                pb_exit_r = 0.50
                                pb_exit_reason = "BREAKEVEN_STOP"
                            else:
                                pb_exit_reason = "STOP_LOSS"
                                pb_exit_r = round((pb_entry - open_px) / pb_initial_risk, 2) if open_px >= pb_active_stop else -1.00
                            break

                        # TP1
                        if tp1 and low <= tp1 and not pb_hit_tp1:
                            pb_hit_tp1 = True
                            pb_active_stop = pb_entry

                        # TP2
                        if tp2 and low <= tp2 and pb_hit_tp1:
                            pb_hit_tp2 = True
                            pb_is_terminal = True
                            pb_exit_r = 1.50
                            pb_exit_reason = "TP2_HIT"
                            break

            if pullback_filled and not pb_is_terminal:
                rem_pb = ((last_close - pb_entry) / pb_initial_risk) if direction == 1 else ((pb_entry - last_close) / pb_initial_risk)
                if len(eval_window) >= max_hold_bars:
                    pb_exit_reason = "TIME_STOP_10D"
                    pb_exit_r = round(0.50 * 1.0 + 0.50 * rem_pb, 2) if pb_hit_tp1 else round(rem_pb, 2)
                else:
                    pb_exit_reason = "OPEN_TRADE"
                    pb_exit_r = round(0.50 * 1.0 + 0.50 * rem_pb, 2) if pb_hit_tp1 else round(rem_pb, 2)

        return {
            "status": "EVALUATED" if is_terminal else "IN_PROGRESS",
            "evaluated": is_terminal,
            "legacy": is_legacy_record,
            "eval_timestamp": datetime.now(timezone.utc).isoformat(),
            "bars_elapsed": len(eval_window),
            "max_hold_bars": max_hold_bars,
            "intended_fill": record.get("price", {}).get("entry"),
            "actual_fill": round(entry, 8) if entry < 0.01 else round(entry, 4),
            "exit_reason": exit_reason,
            "fill_basis": fill_basis,
            "max_favorable_excursion_r": round(float(mfe_r), 2),
            "max_adverse_excursion_r": round(float(mae_r), 2),
            "hypothetical_r": round(float(exit_r), 2),
            "hit_tp1": hit_tp1,
            "hit_stop": hit_stop,
            "hit_tp2": hit_tp2,
            "pullback_filled": pullback_filled,
            "pullback_to_pivot_fill": {
                "filled": pullback_filled,
                "entry_price": pb_entry if pullback_filled else None,
                "initial_risk": pb_initial_risk if pullback_filled else None,
                "hypothetical_r": round(float(pb_exit_r), 2) if pullback_filled else None,
                "exit_reason": pb_exit_reason,
                "hit_tp1": pb_hit_tp1 if pullback_filled else False,
                "hit_stop": pb_hit_stop if pullback_filled else False,
                "hit_tp2": pb_hit_tp2 if pullback_filled else False,
                "max_favorable_excursion_r": round(float(pb_mfe_r), 2) if pullback_filled else None,
                "max_adverse_excursion_r": round(float(pb_mae_r), 2) if pullback_filled else None,
            },
            "signal_close_fill": {
                "entry_price": round(entry, 8) if entry < 0.01 else round(entry, 4),
                "initial_risk": initial_risk,
                "hypothetical_r": round(float(exit_r), 2),
                "exit_reason": exit_reason,
                "hit_tp1": hit_tp1,
                "hit_stop": hit_stop,
                "hit_tp2": hit_tp2,
                "max_favorable_excursion_r": round(float(mfe_r), 2),
                "max_adverse_excursion_r": round(float(mae_r), 2),
            },
        }

    except Exception as e:
        logger.error(f"Failed to evaluate outcome for {sym}: {e}", exc_info=True)
        return {"status": "FAILED", "evaluated": False, "reason": str(e)}


def evaluate_setup_outcome(
    record: Dict[str, Any],
    provider: Optional[MarketDataProvider] = None,
    eval_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> Dict[str, Any]:
    """
    Direct evaluation wrapper for a single record. Fetches candles and delegates to evaluate_setup_outcome_from_df.
    """
    provider = provider or YFinanceProvider()
    sym = record.get("symbol", "")
    try:
        df = provider.candles(sym, period="60d", interval="1h")
    except Exception as e:
        logger.error(f"Error fetching candles for {sym}: {e}")
        df = None
    return evaluate_setup_outcome_from_df(record, df, max_hold_bars=eval_bars)


def update_ledger_outcomes(
    ledger_path: str = "logs/forward_test_ledger.jsonl",
    outcomes_path: str = "logs/forward_test_outcomes.jsonl",
    provider: Optional[MarketDataProvider] = None,
    max_record_age_days: int = 25,
    eval_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> Dict[str, Any]:
    """
    Reads forward_test_ledger.jsonl (STRICTLY APPEND-ONLY, never modified).
    Evaluates new and IN_PROGRESS signals against forward candles.
    Writes latest outcome states to logs/forward_test_outcomes.jsonl.
    Computes isolated metrics for Approved, Provisional, Suppressed, and Rejected buckets.
    """
    if not os.path.exists(ledger_path):
        return {"status": "NO_LEDGER_FILE", "evaluated_count": 0}

    provider = provider or YFinanceProvider()

    outcomes_dir = os.path.dirname(outcomes_path)
    if outcomes_dir and not os.path.exists(outcomes_dir):
        os.makedirs(outcomes_dir, exist_ok=True)

    # 1. Load previously recorded outcomes (latest state keyed by signal_id)
    evaluated_outcomes: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(outcomes_path):
        with open(outcomes_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    sig_id = obj.get("signal_id")
                    if sig_id:
                        evaluated_outcomes[sig_id] = obj
                except Exception:
                    pass

    # 2. Read forward-test ledger (append-only)
    ledger_records = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ledger_records.append(json.loads(line))
            except Exception:
                pass

    now_utc = datetime.now(timezone.utc)
    pending_records_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    newly_evaluated_count = 0

    for rec in ledger_records:
        sig_id = rec.get("signal_id")
        if not sig_id:
            sig_id = f"{rec.get('symbol')}_{rec.get('timestamp')}_{rec.get('direction')}".replace(" ", "_").replace(":", "-")
            rec["signal_id"] = sig_id

        existing_outcome = evaluated_outcomes.get(sig_id)

        # If already reached terminal evaluated state (stopped, TP2, failed, or 10D expired), skip!
        if existing_outcome and existing_outcome.get("evaluated") is True:
            continue

        # Check age: expire if older than max_record_age_days without terminal exit
        ts_str = rec.get("timestamp")
        is_expired = False
        if ts_str:
            try:
                rec_dt = pd.to_datetime(ts_str)
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.tz_localize("UTC")
                age_days = (now_utc - rec_dt).total_seconds() / 86400.0
                if age_days > max_record_age_days:
                    is_expired = True
            except Exception:
                pass

        if is_expired:
            expired_outcome = {
                "signal_id": sig_id,
                "symbol": rec.get("symbol"),
                "eval_timestamp": now_utc.isoformat(),
                "status": "EXPIRED",
                "evaluated": True,
                "reason": "WINDOW_EXPIRED",
                "bars_elapsed": 0,
                "max_favorable_excursion_r": None,
                "max_adverse_excursion_r": None,
                "hypothetical_r": 0.0,
                "hit_tp1": False,
                "hit_stop": False,
                "hit_tp2": False,
            }
            evaluated_outcomes[sig_id] = expired_outcome
            newly_evaluated_count += 1
            continue

        sym = rec.get("symbol")
        if sym:
            pending_records_by_symbol.setdefault(sym, []).append(rec)

    # 3. Batch fetch candles per unique symbol (1 network call per symbol)
    for sym, rec_list in pending_records_by_symbol.items():
        try:
            df = provider.candles(sym, period="60d", interval="1h")
        except Exception as e:
            logger.error(f"Error fetching candles for {sym}: {e}")
            df = None

        for rec in rec_list:
            outcome = evaluate_setup_outcome_from_df(rec, df, max_hold_bars=eval_bars)
            sig_id = rec["signal_id"]
            outcome["signal_id"] = sig_id
            outcome["symbol"] = sym

            evaluated_outcomes[sig_id] = outcome
            if outcome.get("evaluated", False):
                newly_evaluated_count += 1

    # 4. Atomically persist updated outcomes state
    tmp_outcomes_path = outcomes_path + ".tmp"
    with open(tmp_outcomes_path, "w", encoding="utf-8") as out_f:
        for out in evaluated_outcomes.values():
            out_f.write(json.dumps(out) + "\n")
    os.replace(tmp_outcomes_path, outcomes_path)

    # 5. Compute isolated metric buckets
    approved_r = []
    provisional_r = []
    suppressed_r = []
    rejected_r = []
    late_chase_shadow_r = []
    late_chase_shadow_pullback_r = []

    legacy_excluded_count = 0
    for rec in ledger_records:
        sig_id = rec.get("signal_id")
        out = evaluated_outcomes.get(sig_id)
        if out and out.get("hypothetical_r") is not None and out.get("status") in ("EVALUATED", "SUCCESS"):
            if rec.get("legacy") is True or out.get("legacy") is True:
                legacy_excluded_count += 1
                continue
            r_val = out["hypothetical_r"]
            bucket = rec.get("bucket")
            status = rec.get("status")
            if bucket == "LATE_CHASE_SHADOW":
                late_chase_shadow_r.append(r_val)
                pb_r = out.get("pullback_to_pivot_fill", {}).get("hypothetical_r")
                if pb_r is not None:
                    late_chase_shadow_pullback_r.append(pb_r)
            elif status == "APPROVED":
                approved_r.append(r_val)
            elif status == "PROVISIONAL":
                provisional_r.append(r_val)
            elif status == "SUPPRESSED":
                suppressed_r.append(r_val)
            elif status == "REJECTED":
                rejected_r.append(r_val)

    return {
        "status": "SUCCESS",
        "total_records": len(ledger_records),
        "newly_evaluated": newly_evaluated_count,
        "metrics": {
            "approved_avg_r": round(float(np.mean(approved_r)), 3) if approved_r else 0.0,
            "provisional_avg_r": round(float(np.mean(provisional_r)), 3) if provisional_r else 0.0,
            "suppressed_avg_r": round(float(np.mean(suppressed_r)), 3) if suppressed_r else 0.0,
            "rejected_avg_r": round(float(np.mean(rejected_r)), 3) if rejected_r else 0.0,
            "late_chase_shadow_avg_r": round(float(np.mean(late_chase_shadow_r)), 3) if late_chase_shadow_r else 0.0,
            "late_chase_shadow_pullback_avg_r": round(float(np.mean(late_chase_shadow_pullback_r)), 3) if late_chase_shadow_pullback_r else 0.0,
            "approved_count": len(approved_r),
            "provisional_count": len(provisional_r),
            "suppressed_count": len(suppressed_r),
            "rejected_count": len(rejected_r),
            "late_chase_shadow_count": len(late_chase_shadow_r),
            "legacy_excluded_count": legacy_excluded_count,
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = update_ledger_outcomes()
    print(json.dumps(res, indent=2))
