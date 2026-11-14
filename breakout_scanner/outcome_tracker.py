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
    resistance = price_info.get("resistance")
    support = price_info.get("support")

    if not stated_stop or direction == 0:
        return {
            "status": "FAILED",
            "evaluated": False,
            "reason": "INVALID_PRICE_GEOMETRY",
        }

    if df is None or df.empty:
        return {"status": "FAILED", "evaluated": False, "reason": "NO_PRICE_DATA"}

    try:
        # Find bars after the signal timestamp
        sig_ts_str = record.get("timestamp")
        if sig_ts_str:
            try:
                sig_dt = pd.to_datetime(sig_ts_str)
                if df.index.tz is None and sig_dt.tz is not None:
                    df = df.copy()
                    df.index = df.index.tz_localize("UTC")
                elif df.index.tz is not None and sig_dt.tz is None:
                    sig_dt = sig_dt.tz_localize("UTC")
                subsequent_df = df[df.index >= sig_dt]
            except Exception:
                subsequent_df = df
        else:
            subsequent_df = df

        # Need at least 2 bars: Bar 0 is signal bar close, Bar 1 is entry fill bar
        if subsequent_df.empty or len(subsequent_df) < 2:
            return {
                "status": "IN_PROGRESS",
                "evaluated": False,
                "bars_elapsed": len(subsequent_df),
                "hypothetical_r": 0.0,
                "reason": "AWAITING_NEXT_OPEN_FILL",
            }

        # Realistic Execution: Enter at Bar 1 Open + 5 bps friction
        fill_bar = subsequent_df.iloc[1]
        raw_fill = float(fill_bar["Open"])
        entry = raw_fill * (1.0005 if direction == 1 else 0.9995)
        stop = stated_stop

        initial_risk = abs(entry - stop)
        if initial_risk <= 0:
            return {
                "status": "FAILED",
                "evaluated": False,
                "reason": "ZERO_INITIAL_RISK",
            }

        # Evaluation path begins from fill bar (Bar 1 onwards) up to max_hold_bars
        eval_window = subsequent_df.iloc[1: 1 + max_hold_bars]

        mfe_r = 0.0
        mae_r = 0.0
        hit_tp1 = False
        hit_stop = False
        hit_tp2 = False
        failed_breakout = False
        exit_r = 0.0
        exit_reason = "OPEN_TRADE"
        active_stop = stop

        for bar_idx, (_, row) in enumerate(eval_window.iterrows()):
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])

            if direction == 1:
                # Favorable & Adverse Excursion
                curr_fe_r = (high - entry) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r

                curr_ae_r = (entry - low) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r

                # 1. Early Failed Breakout Exit (First 3 bars post-fill fall back below resistance)
                if bar_idx <= 2 and resistance and close < resistance:
                    failed_breakout = True
                    exit_reason = "FAILED_BREAKOUT"
                    exit_r = round((close - entry) / initial_risk, 2)
                    break

                # 2. Stop Hit Check
                if low <= active_stop:
                    hit_stop = True
                    if hit_tp1:
                        # Banked 50% at TP1 (+1.0R), stopped out at breakeven on remaining 50% (+0.0R) -> +0.50R total
                        exit_r = 0.50
                        exit_reason = "BREAKEVEN_STOP"
                    else:
                        exit_r = -1.00
                        exit_reason = "STOP_LOSS"
                    break

                # 3. TP1 Hit Check (1.0R)
                if tp1 and high >= tp1 and not hit_tp1:
                    hit_tp1 = True
                    active_stop = entry  # Trail stop to breakeven for remaining 50%

                # 4. TP2 Hit Check (2.0R)
                if tp2 and high >= tp2 and hit_tp1:
                    hit_tp2 = True
                    # 50% banked at TP1 (+1.0R) + 50% banked at TP2 (+2.0R) -> +1.50R total
                    exit_r = 1.50
                    exit_reason = "TP2_HIT"
                    break

            elif direction == -1:
                curr_fe_r = (entry - low) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r

                curr_ae_r = (high - entry) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r

                # 1. Early Failed Breakout Exit
                if bar_idx <= 2 and support and close > support:
                    failed_breakout = True
                    exit_reason = "FAILED_BREAKOUT"
                    exit_r = round((entry - close) / initial_risk, 2)
                    break

                # 2. Stop Hit Check
                if high >= active_stop:
                    hit_stop = True
                    if hit_tp1:
                        exit_r = 0.50
                        exit_reason = "BREAKEVEN_STOP"
                    else:
                        exit_r = -1.00
                        exit_reason = "STOP_LOSS"
                    break

                # 3. TP1 Hit Check
                if tp1 and low <= tp1 and not hit_tp1:
                    hit_tp1 = True
                    active_stop = entry

                # 4. TP2 Hit Check
                if tp2 and low <= tp2 and hit_tp1:
                    hit_tp2 = True
                    exit_r = 1.50
                    exit_reason = "TP2_HIT"
                    break

        # Terminal determination
        is_terminal = hit_stop or hit_tp2 or failed_breakout

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

        return {
            "status": "EVALUATED" if is_terminal else "IN_PROGRESS",
            "evaluated": is_terminal,
            "eval_timestamp": datetime.now(timezone.utc).isoformat(),
            "bars_elapsed": len(eval_window),
            "max_hold_bars": max_hold_bars,
            "intended_fill": record.get("price", {}).get("entry"),
            "actual_fill": round(entry, 4),
            "exit_reason": exit_reason,
            "max_favorable_excursion_r": round(float(mfe_r), 2),
            "max_adverse_excursion_r": round(float(mae_r), 2),
            "hypothetical_r": round(float(exit_r), 2),
            "hit_tp1": hit_tp1,
            "hit_stop": hit_stop,
            "hit_tp2": hit_tp2,
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
    max_record_age_days: int = 14,
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

    for rec in ledger_records:
        sig_id = rec.get("signal_id")
        out = evaluated_outcomes.get(sig_id)
        if out and out.get("hypothetical_r") is not None and out.get("status") in ("EVALUATED", "SUCCESS"):
            r_val = out["hypothetical_r"]
            status = rec.get("status")
            if status == "APPROVED":
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
            "approved_count": len(approved_r),
            "provisional_count": len(provisional_r),
            "suppressed_count": len(suppressed_r),
            "rejected_count": len(rejected_r),
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = update_ledger_outcomes()
    print(json.dumps(res, indent=2))
