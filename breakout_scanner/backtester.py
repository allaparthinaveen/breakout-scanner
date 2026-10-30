import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Any
from .models import State
from .engine import compute_indicators

class Backtester:
    """
    Unified Institutional Chronological Backtester.
    
    Guarantees:
    1. Zero Lookahead Bias: Signal confirmed at Close[i]; execution at Open[i+1].
    2. Next-Bar Open Execution with realistic slippage modeling.
    3. Live Engine Parity:
       - TP1 (1.0R): Scale out 50% shares, trail remaining stop to breakeven (+offset).
       - TP2 (2.0R): Full exit on remaining 50% shares.
       - Dynamic Stop Loss with gap-through-stop fill at Open.
       - Time-based exit when held >= max_hold_days.
    4. Friction Accounting: Deducts slippage and commissions on every entry and exit leg.
    5. End-of-Test Settlement: Marks to market any position open at test end (no hidden losses).
    6. O(N) Single-Pass Efficiency: Computes all indicators once, bar-by-bar state machine.
    """
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.results: List[Dict] = []
        self.equity_curve: List[float] = []
        
    def run(self, symbol: str, df: pd.DataFrame) -> Dict:
        """
        Run chronological bar-by-bar backtest on a single symbol.
        df must contain: Open, High, Low, Close, Volume.
        """
        if df is None or df.empty:
            return {'trades': 0}
            
        c_base = self.cfg['base']
        c_break = self.cfg['breakout']
        c_risk = self.cfg['risk']
        c_tgt = self.cfg['target']
        c_mgmt = self.cfg['management']
        
        min_bars_required = c_base['base_lookback'] + c_base['tight_lookback'] + 5
        if len(df) < min_bars_required:
            return {'trades': 0}
            
        # Precompute indicators across full history once (O(N) efficiency)
        df = compute_indicators(df, self.cfg)
        
        initial_capital = float(c_risk.get('account_size', 100000.0))
        account_risk_fraction = float(c_risk.get('risk_per_trade_pct', 1.0)) / 100.0
        slippage_pct = float(c_risk.get('slippage_pct', 0.05)) / 100.0
        commission_pct = float(c_risk.get('commission_pct', 0.05)) / 100.0
        max_exposure_pct = float(c_risk.get('max_exposure_pct', 25.0)) / 100.0
        
        capital = initial_capital
        active_trade: Optional[Dict] = None
        pending_signal: Optional[Dict] = None
        trades: List[Dict] = []
        self.equity_curve = []
        
        state = State.IDLE
        setup_start = -1
        setup_resistance = None
        setup_support = None
        
        start_index = c_base['base_lookback'] + c_base['tight_lookback']

        is_intraday_data = False
        if len(df) > 1 and isinstance(df.index, pd.DatetimeIndex):
            median_delta = (df.index[1:] - df.index[:-1]).median()
            is_intraday_data = median_delta.total_seconds() < 43200
        
        def _close_active_trade(trade: Dict, exit_fill: float, exit_reason: str, exit_time: Any, current_capital: float) -> float:
            rem_shares = trade['shares_remaining']
            if trade['direction'] == 1:
                pnl_rem = (exit_fill - trade['entry_price']) * rem_shares
            else:
                pnl_rem = (trade['entry_price'] - exit_fill) * rem_shares
                
            comm_exit = rem_shares * exit_fill * commission_pct
            trade['commissions'] += comm_exit
            current_capital += (pnl_rem - comm_exit)
            
            trade['exit_time'] = exit_time
            trade['exit_price'] = exit_fill
            trade['exit_reason'] = exit_reason
            trade['gross_pnl'] = trade['tp1_pnl'] + pnl_rem
            trade['net_pnl'] = trade['gross_pnl'] - trade['commissions']
            trade['realized_r'] = trade['net_pnl'] / trade['risk_cash'] if trade['risk_cash'] > 0 else 0.0
            trade['capital_after'] = current_capital
            
            # Excursion metrics (MFE / MAE in R)
            risk_per_share = abs(trade['entry_price'] - trade['original_stop'])
            if risk_per_share > 0:
                if trade['direction'] == 1:
                    mfe_r = (trade['peak_price'] - trade['entry_price']) / risk_per_share
                    mae_r = (trade['entry_price'] - trade['trough_price']) / risk_per_share
                else:
                    mfe_r = (trade['entry_price'] - trade['peak_price']) / risk_per_share
                    mae_r = (trade['trough_price'] - trade['entry_price']) / risk_per_share
            else:
                mfe_r = 0.0
                mae_r = 0.0
            trade['mfe_r'] = round(float(max(0.0, mfe_r)), 2)
            trade['mae_r'] = round(float(max(0.0, mae_r)), 2)
            
            trades.append(trade)
            return current_capital

        for i in range(start_index, len(df)):
            row = df.iloc[i]
            cur_open = float(row['Open'])
            cur_high = float(row['High'])
            cur_low = float(row['Low'])
            cur_close = float(row['Close'])
            bar_time = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            
            # =========================================================================
            # STEP 1: EXECUTE PENDING SIGNAL AT BAR OPEN (Next-Bar Open Execution)
            # =========================================================================
            if pending_signal is not None and active_trade is None:
                p_dir = pending_signal['direction']
                p_stop = pending_signal['stop']
                
                # Next-Bar Open Confirmation Check
                confirm_ok = True
                if c_break.get('use_open_confirmation', False):
                    max_adverse_gap = c_break.get('max_open_gap_pct', 0.50)
                    sig_close = pending_signal['signal_close']
                    p_res = pending_signal.get('resistance')
                    p_sup = pending_signal.get('support')
                    if p_dir == 1:
                        gap_down_pct = ((sig_close - cur_open) / sig_close * 100.0) if sig_close else 0.0
                        holds_pivot = (cur_open >= p_res * 0.999) if p_res else True
                        if gap_down_pct > max_adverse_gap or not holds_pivot:
                            confirm_ok = False
                    elif p_dir == -1:
                        gap_up_pct = ((cur_open - sig_close) / sig_close * 100.0) if sig_close else 0.0
                        holds_pivot = (cur_open <= p_sup * 1.001) if p_sup else True
                        if gap_up_pct > max_adverse_gap or not holds_pivot:
                            confirm_ok = False

                # Phase 2 Anti-Chase Extension Filter: Reject if opening beyond pivot tolerance
                if confirm_ok and c_break.get('use_extension_filter', False):
                    p_atr = pending_signal.get('atr')
                    max_chase = float(c_break.get('max_chase_pct', 2.0))
                    if p_dir == 1 and pending_signal.get('resistance'):
                        p_res = pending_signal['resistance']
                        dist_pct = (cur_open - p_res) / p_res * 100.0
                        atr_limit = (0.75 * (p_atr / p_res * 100.0)) if (p_atr and p_atr > 0) else max_chase
                        max_allowed = min(max_chase, max(1.0, atr_limit))
                        if dist_pct > max_allowed:
                            confirm_ok = False
                    elif p_dir == -1 and pending_signal.get('support'):
                        p_sup = pending_signal['support']
                        dist_pct = (p_sup - cur_open) / p_sup * 100.0
                        atr_limit = (0.75 * (p_atr / p_sup * 100.0)) if (p_atr and p_atr > 0) else max_chase
                        max_allowed = min(max_chase, max(1.0, atr_limit))
                        if dist_pct > max_allowed:
                            confirm_ok = False

                if capital <= 0 or not confirm_ok:
                    pending_signal = None
                elif p_dir == 1:  # Long / BUY
                    entry_fill = cur_open * (1.0 + slippage_pct)
                    risk_per_share = entry_fill - p_stop
                    
                    if cur_open <= p_stop or risk_per_share <= 0:
                        pending_signal = None
                    else:
                        risk_cash = capital * account_risk_fraction
                        shares = risk_cash / risk_per_share
                        
                        max_notional = capital * max_exposure_pct
                        if shares * entry_fill > max_notional:
                            shares = max_notional / entry_fill
                            risk_cash = shares * risk_per_share
                            
                        comm_entry = shares * entry_fill * commission_pct
                        capital -= comm_entry
                        
                        tp1 = entry_fill + risk_per_share * c_tgt['tp1_r']
                        tp2 = entry_fill + risk_per_share * c_tgt['tp2_r']
                        shares_tp1 = shares * 0.5
                        
                        active_trade = {
                            'symbol': symbol,
                            'direction': 1,
                            'signal_time': pending_signal['signal_time'],
                            'signal_close': pending_signal['signal_close'],
                            'entry_bar': i,
                            'entry_time': bar_time,
                            'entry_price': entry_fill,
                            'original_stop': p_stop,
                            'stop': p_stop,
                            'tp1': tp1,
                            'tp2': tp2,
                            'shares': shares,
                            'shares_tp1': shares_tp1,
                            'shares_remaining': shares,
                            'risk_cash': risk_cash,
                            'tp1_hit': False,
                            'tp1_exit_time': None,
                            'tp1_exit_price': None,
                            'tp1_pnl': 0.0,
                            'commissions': comm_entry,
                            'exit_time': None,
                            'exit_price': None,
                            'exit_reason': None,
                            'gross_pnl': 0.0,
                            'net_pnl': 0.0,
                            'realized_r': 0.0,
                            'held_bars': 0,
                            'capital_after': capital,
                            'resistance': pending_signal.get('resistance'),
                            'support': pending_signal.get('support'),
                            'stop_floor_binding': pending_signal.get('stop_floor_binding', False),
                            'friction_r': pending_signal.get('friction_r', 0.0),
                            'peak_price': entry_fill,
                            'trough_price': entry_fill,
                            'fwd_ret_1b': None,
                            'fwd_ret_2b': None,
                            'fwd_ret_4b': None,
                            'fwd_ret_8b': None,
                            'mfe_r': 0.0,
                            'mae_r': 0.0,
                        }
                        pending_signal = None
                        
                elif p_dir == -1:  # Short / SELL
                    entry_fill = cur_open * (1.0 - slippage_pct)
                    risk_per_share = p_stop - entry_fill
                    
                    if cur_open >= p_stop or risk_per_share <= 0:
                        pending_signal = None
                    else:
                        risk_cash = capital * account_risk_fraction
                        shares = risk_cash / risk_per_share
                        
                        max_notional = capital * max_exposure_pct
                        if shares * entry_fill > max_notional:
                            shares = max_notional / entry_fill
                            risk_cash = shares * risk_per_share
                            
                        comm_entry = shares * entry_fill * commission_pct
                        capital -= comm_entry
                        
                        tp1 = entry_fill - risk_per_share * c_tgt['tp1_r']
                        tp2 = entry_fill - risk_per_share * c_tgt['tp2_r']
                        shares_tp1 = shares * 0.5
                        
                        active_trade = {
                            'symbol': symbol,
                            'direction': -1,
                            'signal_time': pending_signal['signal_time'],
                            'signal_close': pending_signal['signal_close'],
                            'entry_bar': i,
                            'entry_time': bar_time,
                            'entry_price': entry_fill,
                            'original_stop': p_stop,
                            'stop': p_stop,
                            'tp1': tp1,
                            'tp2': tp2,
                            'shares': shares,
                            'shares_tp1': shares_tp1,
                            'shares_remaining': shares,
                            'risk_cash': risk_cash,
                            'tp1_hit': False,
                            'tp1_exit_time': None,
                            'tp1_exit_price': None,
                            'tp1_pnl': 0.0,
                            'commissions': comm_entry,
                            'exit_time': None,
                            'exit_price': None,
                            'exit_reason': None,
                            'gross_pnl': 0.0,
                            'net_pnl': 0.0,
                            'realized_r': 0.0,
                            'held_bars': 0,
                            'capital_after': capital,
                            'resistance': pending_signal.get('resistance'),
                            'support': pending_signal.get('support'),
                            'stop_floor_binding': pending_signal.get('stop_floor_binding', False),
                            'friction_r': pending_signal.get('friction_r', 0.0),
                            'peak_price': entry_fill,
                            'trough_price': entry_fill,
                            'fwd_ret_1b': None,
                            'fwd_ret_2b': None,
                            'fwd_ret_4b': None,
                            'fwd_ret_8b': None,
                            'mfe_r': 0.0,
                            'mae_r': 0.0,
                        }
                        pending_signal = None

            # =========================================================================
            # STEP 2: MANAGE ACTIVE TRADE ON CURRENT BAR
            # =========================================================================
            if active_trade is not None:
                t_dir = active_trade['direction']
                bars_held = i - active_trade['entry_bar']
                active_trade['held_bars'] = bars_held
                
                # Excursion tracking (MFE / MAE)
                if t_dir == 1:
                    active_trade['peak_price'] = max(active_trade['peak_price'], cur_high)
                    active_trade['trough_price'] = min(active_trade['trough_price'], cur_low)
                else:
                    active_trade['peak_price'] = min(active_trade['peak_price'], cur_low)
                    active_trade['trough_price'] = max(active_trade['trough_price'], cur_high)
                    
                # Forward return tracking (+1, +2, +4, +8 bars)
                if bars_held == 1 and active_trade['fwd_ret_1b'] is None:
                    active_trade['fwd_ret_1b'] = round(float((cur_close - active_trade['entry_price']) / active_trade['entry_price'] * 100.0 * t_dir), 2)
                elif bars_held == 2 and active_trade['fwd_ret_2b'] is None:
                    active_trade['fwd_ret_2b'] = round(float((cur_close - active_trade['entry_price']) / active_trade['entry_price'] * 100.0 * t_dir), 2)
                elif bars_held == 4 and active_trade['fwd_ret_4b'] is None:
                    active_trade['fwd_ret_4b'] = round(float((cur_close - active_trade['entry_price']) / active_trade['entry_price'] * 100.0 * t_dir), 2)
                elif bars_held == 8 and active_trade['fwd_ret_8b'] is None:
                    active_trade['fwd_ret_8b'] = round(float((cur_close - active_trade['entry_price']) / active_trade['entry_price'] * 100.0 * t_dir), 2)
                
                # Time-Exit Calendar/Timeframe Synchronization:
                if not is_intraday_data:
                    held_days = float(bars_held)
                else:
                    try:
                        cur_dt = pd.to_datetime(bar_time)
                        entry_dt = pd.to_datetime(active_trade['entry_time'])
                        is_crypto = str(self.cfg.get('market', '')).lower() == 'crypto'
                        if is_crypto:
                            held_days = (cur_dt - entry_dt).total_seconds() / 86400.0
                        else:
                            cur_date = cur_dt.date()
                            entry_date = entry_dt.date()
                            if cur_date == entry_date:
                                held_days = (cur_dt - entry_dt).total_seconds() / 86400.0
                            else:
                                total_sec = (cur_dt - entry_dt).total_seconds()
                                dates_range = pd.date_range(entry_date, cur_date, freq='D')
                                weekend_days = sum(1 for d in dates_range if d.weekday() >= 5)
                                adj_sec = max(0.0, total_sec - (weekend_days * 86400.0))
                                held_days = adj_sec / 86400.0
                    except Exception:
                        bars_per_day = 7 if str(self.cfg.get('market', '')).lower() in ('nse', 'us', '') else 24
                        held_days = bars_held / bars_per_day

                active_trade['held_days'] = round(float(held_days), 2)
                
                if t_dir == 1:  # LONG MANAGEMENT
                    # 1. Open gap-through-stop check (subsequent bars only)
                    if i > active_trade['entry_bar'] and cur_open <= active_trade['stop']:
                        exit_fill = cur_open * (1.0 - slippage_pct)
                        reason = 'GAP_STOP' if not active_trade['tp1_hit'] else 'BREAKEVEN_STOP'
                        capital = _close_active_trade(active_trade, exit_fill, reason, bar_time, capital)
                        active_trade = None
                        state = State.IDLE
                    
                    # 2. Intra-bar checks if still active
                    if active_trade is not None:
                        # Conservative fill order: assume Stop Loss hit first if both hit in the same candle
                        if cur_low <= active_trade['stop']:
                            exit_fill = min(cur_open, active_trade['stop']) * (1.0 - slippage_pct)
                            reason = 'BREAKEVEN_STOP' if active_trade['tp1_hit'] else 'STOP_LOSS'
                            capital = _close_active_trade(active_trade, exit_fill, reason, bar_time, capital)
                            active_trade = None
                            state = State.IDLE
                        else:
                            # Check Failed Breakout Exit: price closes back inside base within N bars
                            hit_failed = False
                            if c_mgmt.get('use_failed_breakout_exit', False):
                                max_failed_bars = c_mgmt.get('failed_breakout_max_bars', 3)
                                if bars_held <= max_failed_bars and active_trade.get('resistance') and cur_close < active_trade['resistance']:
                                    hit_failed = True

                            if hit_failed:
                                exit_fill = cur_close * (1.0 - slippage_pct)
                                capital = _close_active_trade(active_trade, exit_fill, 'FAILED_BREAKOUT', bar_time, capital)
                                active_trade = None
                                state = State.IDLE
                            else:
                                # Check TP1 scale out (50%)
                                if not active_trade['tp1_hit'] and cur_high >= active_trade['tp1']:
                                    active_trade['tp1_hit'] = True
                                    tp1_fill = max(cur_open, active_trade['tp1']) * (1.0 - slippage_pct)
                                    sh_tp1 = active_trade['shares_tp1']
                                    comm_tp1 = sh_tp1 * tp1_fill * commission_pct
                                    pnl_tp1 = (tp1_fill - active_trade['entry_price']) * sh_tp1
                                    capital += (pnl_tp1 - comm_tp1)
                                    active_trade['shares_remaining'] -= sh_tp1
                                    
                                    active_trade['commissions'] += comm_tp1
                                    active_trade['tp1_exit_time'] = bar_time
                                    active_trade['tp1_exit_price'] = tp1_fill
                                    active_trade['tp1_pnl'] = pnl_tp1
                                    
                                    # Trail Stop to Breakeven
                                    if c_mgmt.get('use_breakeven', True):
                                        be_offset = c_mgmt.get('breakeven_offset_pct', 0.10) / 100.0
                                        active_trade['stop'] = active_trade['entry_price'] * (1.0 + be_offset)
                                
                                # Check TP2 full exit on remaining shares
                                if active_trade is not None and active_trade['tp1_hit'] and cur_high >= active_trade['tp2']:
                                    tp2_fill = max(cur_open, active_trade['tp2']) * (1.0 - slippage_pct)
                                    capital = _close_active_trade(active_trade, tp2_fill, 'TP2_HIT', bar_time, capital)
                                    active_trade = None
                                    state = State.IDLE
                                    
                                # Check Time Exit if still active (Calendar/weekend resilient)
                                if active_trade is not None:
                                    is_time_exit = False
                                    if c_mgmt.get('use_time_exit', True):
                                        if c_mgmt.get('max_hold_bars') is not None:
                                            if bars_held >= c_mgmt.get('max_hold_bars'):
                                                is_time_exit = True
                                        elif held_days >= c_mgmt.get('max_hold_days', 10):
                                            is_time_exit = True
                                    
                                    if is_time_exit:
                                        exit_fill = cur_close * (1.0 - slippage_pct)
                                        capital = _close_active_trade(active_trade, exit_fill, 'TIME_EXIT', bar_time, capital)
                                        active_trade = None
                                        state = State.IDLE

                elif t_dir == -1:  # SHORT MANAGEMENT
                    # 1. Open gap-through-stop check (subsequent bars only)
                    if i > active_trade['entry_bar'] and cur_open >= active_trade['stop']:
                        exit_fill = cur_open * (1.0 + slippage_pct)
                        reason = 'GAP_STOP' if not active_trade['tp1_hit'] else 'BREAKEVEN_STOP'
                        capital = _close_active_trade(active_trade, exit_fill, reason, bar_time, capital)
                        active_trade = None
                        state = State.IDLE
                    
                    # 2. Intra-bar checks if still active
                    if active_trade is not None:
                        if cur_high >= active_trade['stop']:
                            exit_fill = max(cur_open, active_trade['stop']) * (1.0 + slippage_pct)
                            reason = 'BREAKEVEN_STOP' if active_trade['tp1_hit'] else 'STOP_LOSS'
                            capital = _close_active_trade(active_trade, exit_fill, reason, bar_time, capital)
                            active_trade = None
                            state = State.IDLE
                        else:
                            # Check Failed Breakout Exit
                            hit_failed = False
                            if c_mgmt.get('use_failed_breakout_exit', False):
                                max_failed_bars = c_mgmt.get('failed_breakout_max_bars', 3)
                                if bars_held <= max_failed_bars and active_trade.get('support') and cur_close > active_trade['support']:
                                    hit_failed = True

                            if hit_failed:
                                exit_fill = cur_close * (1.0 + slippage_pct)
                                capital = _close_active_trade(active_trade, exit_fill, 'FAILED_BREAKOUT', bar_time, capital)
                                active_trade = None
                                state = State.IDLE
                            else:
                                # Check TP1 scale out (50%)
                                if not active_trade['tp1_hit'] and cur_low <= active_trade['tp1']:
                                    active_trade['tp1_hit'] = True
                                    tp1_fill = min(cur_open, active_trade['tp1']) * (1.0 + slippage_pct)
                                    sh_tp1 = active_trade['shares_tp1']
                                    comm_tp1 = sh_tp1 * tp1_fill * commission_pct
                                    pnl_tp1 = (active_trade['entry_price'] - tp1_fill) * sh_tp1
                                    capital += (pnl_tp1 - comm_tp1)
                                    active_trade['shares_remaining'] -= sh_tp1
                                    
                                    active_trade['commissions'] += comm_tp1
                                    active_trade['tp1_exit_time'] = bar_time
                                    active_trade['tp1_exit_price'] = tp1_fill
                                    active_trade['tp1_pnl'] = pnl_tp1
                                    
                                    if c_mgmt.get('use_breakeven', True):
                                        be_offset = c_mgmt.get('breakeven_offset_pct', 0.10) / 100.0
                                        active_trade['stop'] = active_trade['entry_price'] * (1.0 - be_offset)
                                        
                                # Check TP2 full exit on remaining shares
                                if active_trade is not None and active_trade['tp1_hit'] and cur_low <= active_trade['tp2']:
                                    tp2_fill = min(cur_open, active_trade['tp2']) * (1.0 + slippage_pct)
                                    capital = _close_active_trade(active_trade, tp2_fill, 'TP2_HIT', bar_time, capital)
                                    active_trade = None
                                    state = State.IDLE
                                    
                                # Check Time Exit if still active (Calendar/weekend resilient)
                                if active_trade is not None:
                                    is_time_exit = False
                                    if c_mgmt.get('use_time_exit', True):
                                        if c_mgmt.get('max_hold_bars') is not None:
                                            if bars_held >= c_mgmt.get('max_hold_bars'):
                                                is_time_exit = True
                                        elif held_days >= c_mgmt.get('max_hold_days', 10):
                                            is_time_exit = True
                                    
                                    if is_time_exit:
                                        exit_fill = cur_close * (1.0 + slippage_pct)
                                        capital = _close_active_trade(active_trade, exit_fill, 'TIME_EXIT', bar_time, capital)
                                        active_trade = None
                                        state = State.IDLE

            # =========================================================================
            # STEP 3: VCP SCANNING ON BAR CLOSE (Only when not holding or pending)
            # =========================================================================
            if active_trade is None and pending_signal is None:
                if state == State.IDLE:
                    if row['VCPQualified']:
                        state = State.CONTRACTION
                        setup_start = i
                        setup_resistance = row['PrevBaseHigh']
                        setup_support = row['PrevBaseLow']
                        
                elif state == State.CONTRACTION:
                    setup_bars = i - setup_start + 1
                    if setup_bars > c_base['max_setup_bars']:
                        state = State.IDLE
                    else:
                        bull_break = cur_close >= setup_resistance * (1.0 + c_break['buffer_pct'] / 100.0)
                        bear_break = cur_close <= setup_support * (1.0 - c_break['buffer_pct'] / 100.0)
                        
                        # Phase 2 CLV (Close Location Value) Filter
                        c_quality = self.cfg.get('signal_quality', {}) if self.cfg else {}
                        require_clv = c_quality.get('require_clv_confirmation', False)
                        min_clv = float(c_quality.get('min_clv_threshold', 0.65))
                        clv_long_ok = True
                        clv_short_ok = True
                        bar_rng = cur_high - cur_low
                        if require_clv and bar_rng > 1e-9:
                            clv_val = (cur_close - cur_low) / bar_rng
                            clv_long_ok = (clv_val >= min_clv)
                            clv_short_ok = (clv_val <= (1.0 - min_clv))
                        elif require_clv:
                            clv_long_ok = False
                            clv_short_ok = False

                        long_signal = (setup_bars >= c_base['min_setup_bars'] and bull_break and 
                                       row['BullMom'] and row['LongTrendOK'] and row['BreakoutVolOK'] and clv_long_ok)
                                       
                        short_signal = (c_break.get('allow_shorts', True) and
                                        setup_bars >= c_base['min_setup_bars'] and bear_break and 
                                        row['BearMom'] and row['ShortTrendOK'] and row['BreakoutVolOK'] and clv_short_ok)
                                        
                        if long_signal:
                            structure_stop = setup_support * (1.0 - c_risk['structure_buffer_pct'] / 100.0)
                            atr_stop = cur_close - row['ATR'] * c_risk['atr_multiplier']
                            raw_stop = max(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                            
                            min_risk_stop = cur_close * (1.0 - c_risk['min_stop_pct'] / 100.0)
                            final_stop = min(raw_stop, min_risk_stop)
                            
                            raw_stop_dist_pct = (cur_close - raw_stop) / cur_close * 100.0 if cur_close > 0 else 0.0
                            is_floor_binding = raw_stop_dist_pct < c_risk.get('min_stop_pct', 0.75)
                            
                            risk = abs(cur_close - final_stop)
                            setup_risk_pct = (risk / cur_close) * 100.0 if cur_close > 0 else 0.0
                            max_allowed_stop = c_risk.get('max_stop_pct', 5.0)
                            
                            round_trip_cost_pct = 2.0 * (c_risk.get('slippage_pct', 0.05) + c_risk.get('commission_pct', 0.05))
                            friction_r = (round_trip_cost_pct / setup_risk_pct) if setup_risk_pct > 0 else 999.0
                            cost_ok = (friction_r <= c_risk.get('max_friction_r', 0.15)) if c_risk.get('use_cost_filter', False) else True
                            
                            if setup_risk_pct <= max_allowed_stop and cost_ok:
                                pending_signal = {
                                    'direction': 1,
                                    'signal_bar': i,
                                    'signal_time': bar_time,
                                    'signal_close': cur_close,
                                    'stop': final_stop,
                                    'resistance': setup_resistance,
                                    'support': setup_support,
                                    'stop_floor_binding': is_floor_binding,
                                    'friction_r': round(float(friction_r), 3),
                                    'atr': float(row['ATR']) if ('ATR' in row and row['ATR'] is not None) else None
                                }
                            state = State.IDLE
                            
                        elif short_signal:
                            structure_stop = setup_resistance * (1.0 + c_risk['structure_buffer_pct'] / 100.0)
                            atr_stop = cur_close + row['ATR'] * c_risk['atr_multiplier']
                            raw_stop = min(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                            
                            min_risk_stop = cur_close * (1.0 + c_risk['min_stop_pct'] / 100.0)
                            final_stop = max(raw_stop, min_risk_stop)
                            
                            raw_stop_dist_pct = (raw_stop - cur_close) / cur_close * 100.0 if cur_close > 0 else 0.0
                            is_floor_binding = raw_stop_dist_pct < c_risk.get('min_stop_pct', 0.75)
                            
                            risk = abs(cur_close - final_stop)
                            setup_risk_pct = (risk / cur_close) * 100.0 if cur_close > 0 else 0.0
                            max_allowed_stop = c_risk.get('max_stop_pct', 5.0)
                            
                            round_trip_cost_pct = 2.0 * (c_risk.get('slippage_pct', 0.05) + c_risk.get('commission_pct', 0.05))
                            friction_r = (round_trip_cost_pct / setup_risk_pct) if setup_risk_pct > 0 else 999.0
                            cost_ok = (friction_r <= c_risk.get('max_friction_r', 0.15)) if c_risk.get('use_cost_filter', False) else True
                            
                            if setup_risk_pct <= max_allowed_stop and cost_ok:
                                pending_signal = {
                                    'direction': -1,
                                    'signal_bar': i,
                                    'signal_time': bar_time,
                                    'signal_close': cur_close,
                                    'stop': final_stop,
                                    'resistance': setup_resistance,
                                    'support': setup_support,
                                    'stop_floor_binding': is_floor_binding,
                                    'friction_r': round(float(friction_r), 3),
                                    'atr': float(row['ATR']) if ('ATR' in row and row['ATR'] is not None) else None
                                }
                            state = State.IDLE

            # =========================================================================
            # STEP 4: TRACK MARK-TO-MARKET PORTFOLIO EQUITY
            # =========================================================================
            unrealized = 0.0
            if active_trade is not None:
                if active_trade['direction'] == 1:
                    unrealized = (cur_close - active_trade['entry_price']) * active_trade['shares_remaining']
                else:
                    unrealized = (active_trade['entry_price'] - cur_close) * active_trade['shares_remaining']
            self.equity_curve.append(capital + unrealized)

        # =========================================================================
        # STEP 5: END-OF-TEST SETTLEMENT (Close Open Positions)
        # =========================================================================
        if active_trade is not None:
            last_close = float(df['Close'].iloc[-1])
            final_time = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else len(df) - 1
            exit_fill = last_close * (1.0 - slippage_pct) if active_trade['direction'] == 1 else last_close * (1.0 + slippage_pct)
            capital = _close_active_trade(active_trade, exit_fill, 'END_OF_DATA', final_time, capital)
            active_trade = None

        # =========================================================================
        # STEP 6: UNBIASED FORWARD RETURNS (All trades evaluated regardless of exit)
        # =========================================================================
        for trade in trades:
            e_bar = trade.get('entry_bar')
            e_price = trade.get('entry_price')
            t_dir = trade.get('direction', 1)
            if e_bar is not None and e_price is not None and e_price > 0:
                for n_bars in (1, 2, 4, 8):
                    fwd_idx = e_bar + n_bars
                    if fwd_idx < len(df):
                        fwd_close = float(df.iloc[fwd_idx]['Close'])
                        trade[f'fwd_ret_{n_bars}b'] = round(float((fwd_close - e_price) / e_price * 100.0 * t_dir), 2)
                    else:
                        trade[f'fwd_ret_{n_bars}b'] = None

        self.results.extend(trades)
        return self._compute_metrics(trades, initial_capital, capital, self.equity_curve)

    def _compute_metrics(self, trades: List[Dict], initial_capital: float, final_capital: float, equity_curve: List[float]) -> Dict:
        """Calculate comprehensive trading and risk metrics."""
        if not trades:
            return {
                'trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'expectancy': 0.0,
                'average_r': 0.0,
                'tp1_hit_rate': 0.0,
                'max_drawdown_pct': 0.0,
                'net_return_pct': 0.0,
                'total_net_pnl': 0.0,
                'total_commissions': 0.0,
                'initial_capital': round(initial_capital, 2),
                'final_capital': round(final_capital, 2)
            }
            
        wins = [t for t in trades if t['net_pnl'] > 0]
        losses = [t for t in trades if t['net_pnl'] <= 0]
        
        win_rate = len(wins) / len(trades)
        gross_profit = sum(t['net_pnl'] for t in wins)
        gross_loss = abs(sum(t['net_pnl'] for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        
        avg_win = np.mean([t['net_pnl'] for t in wins]) if wins else 0.0
        avg_loss = np.mean([abs(t['net_pnl']) for t in losses]) if losses else 0.0
        expectancy = round((win_rate * avg_win) - ((1.0 - win_rate) * avg_loss), 2)
        
        avg_r = round(float(np.mean([t['realized_r'] for t in trades])), 2)
        tp1_hits = sum(1 for t in trades if t['tp1_hit'])
        tp1_hit_rate = round(tp1_hits / len(trades), 4)
        
        # Max drawdown from equity curve
        max_dd_pct = 0.0
        if equity_curve:
            eq = np.array(equity_curve)
            peak = np.maximum.accumulate(eq)
            dd = (eq - peak) / peak * 100.0
            max_dd_pct = round(float(abs(np.min(dd))), 2)
            
        net_return_pct = round((final_capital - initial_capital) / initial_capital * 100.0, 2)
        total_commissions = round(sum(t['commissions'] for t in trades), 2)
        total_net_pnl = round(sum(t['net_pnl'] for t in trades), 2)
        
        return {
            'trades': len(trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(win_rate, 4),
            'profit_factor': profit_factor,
            'expectancy': expectancy,
            'average_r': avg_r,
            'tp1_hit_rate': tp1_hit_rate,
            'max_drawdown_pct': max_dd_pct,
            'net_return_pct': net_return_pct,
            'total_net_pnl': total_net_pnl,
            'total_commissions': total_commissions,
            'initial_capital': round(initial_capital, 2),
            'final_capital': round(final_capital, 2)
        }
