"""
Nightly What-If Outcome Tracker & Drift Verification Engine (Milestone 3).

Evaluates the forward-test audit ledger (logs/forward_test_ledger.jsonl):
1. For every setup recorded (Approved, Suppressed, Rejected), pulls subsequent price bars.
2. Computes Max Favorable Excursion (MFE in R) and Max Adverse Excursion (MAE in R).
3. Determines hypothetical R-outcome under standard execution (50% off at TP1, move to BE).
4. Answers the core quantitative question: "Did our suppression filters save money or block winners?"
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


def evaluate_setup_outcome(
    record: Dict[str, Any],
    provider: Optional[MarketDataProvider] = None,
    eval_bars: int = 24,
) -> Dict[str, Any]:
    """
    Evaluates the hypothetical price performance of a logged signal.
    Computes MFE, MAE, TP1 hits, Stop hits, and hypothetical realized R.
    """
    provider = provider or YFinanceProvider()
    sym = record["symbol"]
    direction = 1 if record.get("direction") == "BUY" else (-1 if record.get("direction") == "SELL" else 0)
    
    price_info = record.get("price", {})
    entry = price_info.get("entry") or price_info.get("current")
    stop = price_info.get("stop")
    tp1 = price_info.get("tp1")
    tp2 = price_info.get("tp2")

    if not entry or not stop or direction == 0 or entry == stop:
        return {"evaluated": True, "hypothetical_r": 0.0, "reason": "INVALID_PRICE_GEOMETRY"}

    initial_risk = abs(entry - stop)
    if initial_risk <= 0:
        return {"evaluated": True, "hypothetical_r": 0.0, "reason": "ZERO_INITIAL_RISK"}

    try:
        # Pull recent 1H bars to measure subsequent path
        df = provider.candles(sym, period="5d", interval="1h")
        if df is None or df.empty:
            return {"evaluated": False, "reason": "NO_PRICE_DATA"}

        # Find bars after the signal timestamp
        sig_ts_str = record.get("timestamp")
        if sig_ts_str:
            try:
                sig_dt = pd.to_datetime(sig_ts_str)
                if df.index.tz is None and sig_dt.tz is not None:
                    df.index = df.index.tz_localize("UTC")
                elif df.index.tz is not None and sig_dt.tz is None:
                    sig_dt = sig_dt.tz_localize("UTC")
                subsequent_df = df[df.index >= sig_dt]
            except Exception:
                subsequent_df = df.iloc[-eval_bars:]
        else:
            subsequent_df = df.iloc[-eval_bars:]

        if subsequent_df.empty or len(subsequent_df) < 2:
            return {"evaluated": False, "reason": "INSUFFICIENT_FORWARD_BARS"}

        # Limit to the requested evaluation horizon
        eval_window = subsequent_df.iloc[:eval_bars]

        mfe_r = 0.0
        mae_r = 0.0
        hit_tp1 = False
        hit_stop = False
        exit_r = 0.0
        active_stop = stop

        for idx, row in eval_window.iterrows():
            high = row["High"]
            low = row["Low"]

            if direction == 1:
                # Favorable excursion (up)
                curr_fe_r = (high - entry) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r

                # Adverse excursion (down)
                curr_ae_r = (entry - low) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r

                # Check Stop Hit
                if low <= active_stop:
                    hit_stop = True
                    if hit_tp1:
                        # Breakeven stop exit on 2nd half (+0.10R or +0.0R)
                        exit_r = 0.5 * 1.0 + 0.5 * 0.0  # +0.5R total
                    else:
                        exit_r = -1.0
                    break

                # Check TP1 Hit
                if tp1 and high >= tp1 and not hit_tp1:
                    hit_tp1 = True
                    # Move active stop to breakeven
                    active_stop = entry * 1.001

                # Check TP2 Hit
                if tp2 and high >= tp2 and hit_tp1:
                    exit_r = 0.5 * 1.0 + 0.5 * 2.0  # +1.5R total
                    break

            elif direction == -1:
                curr_fe_r = (entry - low) / initial_risk
                if curr_fe_r > mfe_r:
                    mfe_r = curr_fe_r

                curr_ae_r = (high - entry) / initial_risk
                if curr_ae_r > mae_r:
                    mae_r = curr_ae_r

                if high >= active_stop:
                    hit_stop = True
                    if hit_tp1:
                        exit_r = 0.5 * 1.0 + 0.5 * 0.0
                    else:
                        exit_r = -1.0
                    break

                if tp1 and low <= tp1 and not hit_tp1:
                    hit_tp1 = True
                    active_stop = entry * 0.999

                if tp2 and low <= tp2 and hit_tp1:
                    exit_r = 0.5 * 1.0 + 0.5 * 2.0
                    break

        # If trade did not hit stop or TP2 by window end, mark mark-to-market R
        if not hit_stop and exit_r == 0.0:
            last_close = eval_window["Close"].iloc[-1]
            mtm_r = ((last_close - entry) / initial_risk) if direction == 1 else ((entry - last_close) / initial_risk)
            exit_r = round(float(mtm_r), 2)

        return {
            "evaluated": True,
            "eval_timestamp": datetime.now(timezone.utc).isoformat(),
            "bars_elapsed": len(eval_window),
            "max_favorable_excursion_r": round(float(mfe_r), 2),
            "max_adverse_excursion_r": round(float(mae_r), 2),
            "hypothetical_r": round(float(exit_r), 2),
            "hit_tp1": hit_tp1,
            "hit_stop": hit_stop,
        }

    except Exception as e:
        logger.error(f"Failed to evaluate outcome for {sym}: {e}")
        return {"evaluated": False, "reason": str(e)}


def update_ledger_outcomes(
    ledger_path: str = "logs/forward_test_ledger.jsonl",
    provider: Optional[MarketDataProvider] = None,
) -> Dict[str, Any]:
    """
    Reads all records in the ledger, evaluates unevaluated entries,
    and updates the file in place. Returns summary metrics.
    """
    if not os.path.exists(ledger_path):
        return {"status": "NO_LEDGER_FILE", "evaluated_count": 0}

    with open(ledger_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    evaluated_count = 0
    approved_r = []
    suppressed_r = []
    rejected_r = []

    for rec in records:
        outcome = rec.get("what_if_outcome", {})
        if not outcome.get("evaluated", False):
            updated_outcome = evaluate_setup_outcome(rec, provider=provider)
            rec["what_if_outcome"] = updated_outcome
            if updated_outcome.get("evaluated", False):
                evaluated_count += 1

        r_val = rec.get("what_if_outcome", {}).get("hypothetical_r")
        if r_val is not None:
            status = rec.get("status")
            if status == "APPROVED":
                approved_r.append(r_val)
            elif status == "SUPPRESSED":
                suppressed_r.append(r_val)
            elif status == "REJECTED":
                rejected_r.append(r_val)

    # Write back updated records atomically
    tmp_path = ledger_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    os.replace(tmp_path, ledger_path)

    return {
        "status": "SUCCESS",
        "total_records": len(records),
        "newly_evaluated": evaluated_count,
        "metrics": {
            "approved_avg_r": round(float(np.mean(approved_r)), 3) if approved_r else 0.0,
            "suppressed_avg_r": round(float(np.mean(suppressed_r)), 3) if suppressed_r else 0.0,
            "rejected_avg_r": round(float(np.mean(rejected_r)), 3) if rejected_r else 0.0,
            "approved_count": len(approved_r),
            "suppressed_count": len(suppressed_r),
            "rejected_count": len(rejected_r),
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = update_ledger_outcomes()
    print(json.dumps(res, indent=2))
