"""
Unified Execution Simulation Kernel.
Provides standardized trade lifecycle simulation for both historical backtesting
and forward post-scan outcome tracking, guaranteeing zero divergence in
entry fills, partial scale-outs, trailing stops, frictions, and MFE/MAE calculations.
"""

from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np


def simulate_forward_trade(
    candidate: Dict[str, Any],
    future_candles: pd.DataFrame,
    cfg: Optional[Dict[str, Any]] = None,
    use_partial_exits: bool = True
) -> Dict[str, Any]:
    """
    Simulates the forward execution path of a trade candidate starting from
    the next-bar open (bar 0 of future_candles), enforcing institutional
    execution rules:
    - Next-bar open entry with slippage
    - Strict rejection of missing/synthetic stops (INVALID_SETUP)
    - Pessimistic same-bar collision resolution (stop takes precedence over target)
    - Overnight gap past stop filled at Open (slippage penalty)
    - 50% partial scale-out at TP1 with stop trailed to breakeven
    - Remaining 50% exited at TP2, breakeven stop, or time exit
    - Continuous MFE and MAE tracking in initial structural risk units (R)
    - Complete friction deductions (slippage and commission)
    """
    if cfg is None:
        cfg = {}

    c_risk = cfg.get('risk', {})
    c_mgmt = cfg.get('management', {})
    c_tgt = cfg.get('targets', {})

    # Slippage and commissions in config are in percent (e.g. 0.05 = 0.05% = 0.0005)
    raw_slip = float(c_risk.get('slippage_pct', 0.05))
    slippage_pct = raw_slip / 100.0 if raw_slip > 0.005 else raw_slip

    raw_comm = float(c_risk.get('commission_pct', 0.05))
    commission_pct = raw_comm / 100.0 if raw_comm > 0.005 else raw_comm

    account_equity = float(c_risk.get('account_size', c_risk.get('account_equity', 100000.0)))
    account_risk_fraction = float(c_risk.get('risk_per_trade_pct', 1.0)) / 100.0

    c_tgt = cfg.get('target', cfg.get('targets', {}))
    tp1_r = float(c_tgt.get('tp1_r', 1.0))
    tp2_r = float(c_tgt.get('tp2_r', 2.0))
    be_offset_pct = float(c_mgmt.get('breakeven_offset_pct', 0.10)) / 100.0
    use_breakeven = c_mgmt.get('use_breakeven', True)
    use_failed_exit = c_mgmt.get('use_failed_breakout_exit', False)
    failed_max_bars = int(c_mgmt.get('failed_breakout_max_bars', 3))
    max_hold_bars = c_mgmt.get('max_hold_bars', None)

    sym = candidate.get('symbol', 'UNKNOWN')
    direction = candidate.get('direction', 1)
    if direction not in (1, -1):
        direction = 1 if candidate.get('state') == 'BUY' else (-1 if candidate.get('state') == 'SELL' else 1)

    raw_entry = candidate.get('entry')
    raw_stop = candidate.get('stop')
    resistance = candidate.get('resistance')
    support = candidate.get('support')

    # Rule 1.3: Remove synthetic stop/target fallbacks. Reject invalid setup explicitly.
    if future_candles is None or future_candles.empty or raw_stop is None or raw_entry is None or raw_stop <= 0 or raw_entry <= 0:
        return {
            **candidate,
            "outcome": "INVALID_SETUP",
            "exit_reason": "MISSING_STRUCTURAL_STOP" if (raw_stop is None or raw_stop <= 0) else "INSUFFICIENT_DATA",
            "entry_price": raw_entry,
            "exit_price": raw_entry,
            "mfe_r": 0.0,
            "mae_r": 0.0,
            "realized_r": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "commissions": 0.0,
            "bars_tracked": 0,
            "t1_hit": False,
            "t1_bar": None,
            "t2_hit": False,
            "t2_bar": None,
            "stopped_out": False,
            "stop_bar": None
        }

    first_bar = future_candles.iloc[0]
    first_open = float(first_bar['Open'])

    # Phase 2/3: Tiered Follow-Through Confirmation Policy
    requires_conf = bool(candidate.get('requires_confirmation', False))
    breakout_high = candidate.get('breakout_high') or candidate.get('entry')
    breakout_low = candidate.get('breakout_low') or candidate.get('entry')

    if requires_conf and len(future_candles) > 0:
        first_high = float(first_bar['High'])
        first_low = float(first_bar['Low'])
        if direction == 1 and breakout_high is not None and first_high <= (breakout_high * 1.0001):
            return {
                **candidate,
                "outcome": "UNCONFIRMED",
                "exit_reason": "FAILED_FOLLOW_THROUGH",
                "entry_price": None,
                "exit_price": None,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "realized_r": 0.0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "commissions": 0.0,
                "bars_tracked": 1,
                "t1_hit": False,
                "t1_bar": None,
                "t2_hit": False,
                "t2_bar": None,
                "stopped_out": False,
                "stop_bar": None
            }
        elif direction == -1 and breakout_low is not None and first_low >= (breakout_low * 0.9999):
            return {
                **candidate,
                "outcome": "UNCONFIRMED",
                "exit_reason": "FAILED_FOLLOW_THROUGH",
                "entry_price": None,
                "exit_price": None,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "realized_r": 0.0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "commissions": 0.0,
                "bars_tracked": 1,
                "t1_hit": False,
                "t1_bar": None,
                "t2_hit": False,
                "t2_bar": None,
                "stopped_out": False,
                "stop_bar": None
            }

    # Entry execution at Next-Bar Open
    if direction == 1:
        entry_fill = first_open * (1.0 + slippage_pct)
        risk_per_share = entry_fill - raw_stop
    else:
        entry_fill = first_open * (1.0 - slippage_pct)
        risk_per_share = raw_stop - entry_fill

    # Immediate rejection if bar opens through stop or risk is non-positive
    if risk_per_share <= 0 or (direction == 1 and first_open <= raw_stop) or (direction == -1 and first_open >= raw_stop):
        exit_fill = first_open * (1.0 - slippage_pct * direction)
        realized_r = round(float(((exit_fill - entry_fill) / max(0.0001, abs(raw_entry - raw_stop))) * direction), 2)
        return {
            **candidate,
            "outcome": "STOPPED_OUT",
            "exit_reason": "GAP_STOP_AT_OPEN",
            "entry_price": round(entry_fill, 4),
            "exit_price": round(exit_fill, 4),
            "mfe_r": 0.0,
            "mae_r": round(float(abs(realized_r)), 2),
            "realized_r": realized_r,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "commissions": 0.0,
            "bars_tracked": len(future_candles),
            "t1_hit": False,
            "t1_bar": None,
            "t2_hit": False,
            "t2_bar": None,
            "stopped_out": True,
            "stop_bar": 1
        }

    # Position Sizing
    risk_cash = account_equity * account_risk_fraction
    shares_total = risk_cash / risk_per_share
    shares_tp1 = shares_total * 0.5 if use_partial_exits else shares_total
    shares_remaining = shares_total

    comm_entry = shares_total * entry_fill * commission_pct
    total_commissions = comm_entry

    current_stop = float(raw_stop)
    tp1_price = float(candidate['tp1']) if candidate.get('tp1') is not None else (entry_fill + direction * risk_per_share * tp1_r)
    tp2_price = float(candidate['tp2']) if candidate.get('tp2') is not None else (entry_fill + direction * risk_per_share * tp2_r)

    tp1_hit = False
    tp1_bar = None
    tp1_fill = 0.0
    tp1_pnl = 0.0

    tp2_hit = False
    tp2_bar = None

    stopped_out = False
    stop_bar = None

    exit_price = 0.0
    exit_reason = None
    exit_time = None

    peak_price = entry_fill
    trough_price = entry_fill

    bars_tracked = len(future_candles)
    bars_held = 0

    for idx, (bar_idx, row) in enumerate(future_candles.iterrows(), 1):
        cur_open = float(row['Open'])
        cur_high = float(row['High'])
        cur_low = float(row['Low'])
        cur_close = float(row['Close'])
        bar_time = bar_idx
        bars_held = idx

        # -------------------------------------------------------------
        # Excursion Tracking (MFE / MAE)
        # -------------------------------------------------------------
        if direction == 1:
            peak_price = max(peak_price, cur_high)
            trough_price = min(trough_price, cur_low)
        else:
            peak_price = min(peak_price, cur_low)
            trough_price = max(trough_price, cur_high)

        # -------------------------------------------------------------
        # 1. Overnight Gap Through Stop (subsequent bars only)
        # -------------------------------------------------------------
        if idx > 1:
            gapped_stop = (cur_open <= current_stop) if direction == 1 else (cur_open >= current_stop)
            if gapped_stop:
                exit_fill = cur_open * (1.0 - slippage_pct * direction)
                exit_price = exit_fill
                exit_reason = 'BREAKEVEN_STOP' if tp1_hit else 'GAP_STOP'
                exit_time = bar_time
                stopped_out = True
                stop_bar = idx
                break

        # -------------------------------------------------------------
        # 2. Intra-bar Pessimistic Stop (takes priority over target)
        # -------------------------------------------------------------
        hit_stop = (cur_low <= current_stop) if direction == 1 else (cur_high >= current_stop)
        if hit_stop:
            if direction == 1:
                exit_fill = min(cur_open, current_stop) * (1.0 - slippage_pct)
            else:
                exit_fill = max(cur_open, current_stop) * (1.0 + slippage_pct)
            exit_price = exit_fill
            exit_reason = 'BREAKEVEN_STOP' if tp1_hit else 'STOP_LOSS'
            exit_time = bar_time
            stopped_out = True
            stop_bar = idx
            break

        # -------------------------------------------------------------
        # 3. Failed Breakout Exit (closes back inside base within N bars)
        # -------------------------------------------------------------
        if use_failed_exit and idx <= failed_max_bars:
            hit_failed = False
            if direction == 1 and resistance is not None and cur_close < resistance:
                hit_failed = True
            elif direction == -1 and support is not None and cur_close > support:
                hit_failed = True

            if hit_failed:
                exit_fill = cur_close * (1.0 - slippage_pct * direction)
                exit_price = exit_fill
                exit_reason = 'FAILED_BREAKOUT'
                exit_time = bar_time
                break

        # -------------------------------------------------------------
        # 4. Take Profit 1 Scale-Out (50%)
        # -------------------------------------------------------------
        hit_tp1 = (cur_high >= tp1_price) if direction == 1 else (cur_low <= tp1_price)
        if not tp1_hit and hit_tp1:
            tp1_hit = True
            tp1_bar = idx
            if direction == 1:
                tp1_fill = max(cur_open, tp1_price) * (1.0 - slippage_pct)
            else:
                tp1_fill = min(cur_open, tp1_price) * (1.0 + slippage_pct)

            comm_tp1 = shares_tp1 * tp1_fill * commission_pct
            total_commissions += comm_tp1
            tp1_pnl = (tp1_fill - entry_fill) * shares_tp1 * direction
            shares_remaining -= shares_tp1

            # Trail stop to Breakeven
            if use_breakeven:
                current_stop = entry_fill * (1.0 + be_offset_pct * direction)
                # P1-4 Conservative Same-Candle Invariant:
                # If this same bar collapsed and closed back below breakeven stop, exit remainder immediately
                hit_same_bar_be = (cur_close <= current_stop) if direction == 1 else (cur_close >= current_stop)
                if hit_same_bar_be and use_partial_exits:
                    exit_fill = current_stop * (1.0 - slippage_pct * direction)
                    exit_price = exit_fill
                    exit_reason = 'BREAKEVEN_STOP'
                    exit_time = bar_time
                    stopped_out = True
                    stop_bar = idx
                    break

            if not use_partial_exits:
                # 100% position was closed at TP1
                exit_price = tp1_fill
                exit_reason = 'TP1_HIT'
                exit_time = bar_time
                break

        # -------------------------------------------------------------
        # 5. Take Profit 2 Full Exit on Remaining Shares
        # -------------------------------------------------------------
        if tp1_hit and shares_remaining > 0:
            hit_tp2 = (cur_high >= tp2_price) if direction == 1 else (cur_low <= tp2_price)
            if hit_tp2:
                tp2_hit = True
                tp2_bar = idx
                if direction == 1:
                    exit_fill = max(cur_open, tp2_price) * (1.0 - slippage_pct)
                else:
                    exit_fill = min(cur_open, tp2_price) * (1.0 + slippage_pct)
                exit_price = exit_fill
                exit_reason = 'TP2_HIT'
                exit_time = bar_time
                break

        # -------------------------------------------------------------
        # 6. Max Hold Bars / Time Exit
        # -------------------------------------------------------------
        if max_hold_bars is not None and idx >= max_hold_bars:
            exit_fill = cur_close * (1.0 - slippage_pct * direction)
            exit_price = exit_fill
            exit_reason = 'TIME_EXIT'
            exit_time = bar_time
            break

    # -----------------------------------------------------------------
    # Final Trade Accounting
    # -----------------------------------------------------------------
    # If trade was still open when forward window ended: mark to market at final bar close
    if exit_reason is None:
        final_close = float(future_candles.iloc[-1]['Close'])
        exit_fill = final_close * (1.0 - slippage_pct * direction)
        exit_price = exit_fill
        exit_reason = 'FORWARD_WINDOW_END'
        exit_time = future_candles.index[-1]

    if shares_remaining > 0:
        comm_exit = shares_remaining * exit_price * commission_pct
        total_commissions += comm_exit
        pnl_rem = (exit_price - entry_fill) * shares_remaining * direction
    else:
        pnl_rem = 0.0

    gross_pnl = tp1_pnl + pnl_rem
    net_pnl = gross_pnl - total_commissions
    realized_r = net_pnl / risk_cash if risk_cash > 0 else 0.0

    # Calculate MFE and MAE in normalized R-multiples
    if direction == 1:
        mfe_r = (peak_price - entry_fill) / risk_per_share
        mae_r = (entry_fill - trough_price) / risk_per_share
    else:
        mfe_r = (entry_fill - peak_price) / risk_per_share
        mae_r = (trough_price - entry_fill) / risk_per_share

    mfe_r = max(0.0, float(mfe_r))
    mae_r = max(0.0, float(mae_r))

    # Standardized outcome labeling
    if stopped_out and (tp1_bar is None or stop_bar <= tp1_bar):
        outcome = "STOPPED_OUT"
    elif tp2_hit:
        outcome = "TP2_REACHED"
    elif tp1_hit and exit_reason == "BREAKEVEN_STOP":
        outcome = "TP1_THEN_BREAKEVEN"
    elif tp1_hit:
        outcome = "TP1_REACHED"
    else:
        outcome = "EXPIRED_POSITIVE" if net_pnl > 0 else "EXPIRED_NEGATIVE"

    return {
        "symbol": sym,
        "scan_timestamp": candidate.get("timestamp"),
        "direction": direction,
        "entry_price": round(entry_fill, 4),
        "original_stop": round(raw_stop, 4),
        "final_stop": round(current_stop, 4),
        "tp1": round(tp1_price, 4),
        "tp2": round(tp2_price, 4),
        "exit_time": str(exit_time),
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "outcome": outcome,
        "t1_hit": tp1_hit,
        "t1_bar": tp1_bar,
        "t2_hit": tp2_hit,
        "t2_bar": tp2_bar,
        "stopped_out": stopped_out,
        "stop_bar": stop_bar,
        "mfe_r": round(float(mfe_r), 2),
        "mae_r": round(float(mae_r), 2),
        "gross_pnl": round(float(gross_pnl), 2),
        "net_pnl": round(float(net_pnl), 2),
        "commissions": round(float(total_commissions), 2),
        "realized_r": round(float(realized_r), 2),
        "bars_tracked": bars_tracked,
        "held_bars": bars_held,
        "composite_score": candidate.get("composite_score", 0),
        "composite_grade": candidate.get("composite_grade", ""),
        "base_width_pct": candidate.get("base_width_pct"),
        "rvol": candidate.get("rvol"),
        "htf_confirmed": candidate.get("htf_confirmed", False),
        "bias_score": candidate.get("bias_score", 0)
    }
