import pandas as pd
import numpy as np
from typing import List, Dict, Optional
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
        risk_pct = float(c_risk.get('risk_per_trade_pct', 1.0)) / 100.0
        slippage_pct = float(c_risk.get('slippage_pct', 0.05)) / 100.0
        commission_pct = float(c_risk.get('commission_pct', 0.05)) / 100.0
        
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
                
                if p_dir == 1:  # Long / BUY
                    entry_fill = cur_open * (1.0 + slippage_pct)
                    risk_per_share = entry_fill - p_stop
                    
                    # If market opens below or at the stop, breakout failed on open -> invalidate
                    if cur_open <= p_stop or risk_per_share <= 0:
                        pending_signal = None
                    else:
                        risk_cash = capital * risk_pct
                        shares = risk_cash / risk_per_share
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
                            'shares_remaining': shares - shares_tp1,
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
                        }
                        pending_signal = None
                        
                elif p_dir == -1:  # Short / SELL
                    entry_fill = cur_open * (1.0 - slippage_pct)
                    risk_per_share = p_stop - entry_fill
                    
                    if cur_open >= p_stop or risk_per_share <= 0:
                        pending_signal = None
                    else:
                        risk_cash = capital * risk_pct
                        shares = risk_cash / risk_per_share
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
                            'shares_remaining': shares - shares_tp1,
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
                        }
                        pending_signal = None

            # =========================================================================
            # STEP 2: MANAGE ACTIVE TRADE ON CURRENT BAR
            # =========================================================================
            if active_trade is not None:
                t_dir = active_trade['direction']
                bars_held = i - active_trade['entry_bar']
                active_trade['held_bars'] = bars_held
                
                if t_dir == 1:  # LONG MANAGEMENT
                    # 1. Open gap-through-stop check (subsequent bars only)
                    if i > active_trade['entry_bar'] and cur_open <= active_trade['stop']:
                        exit_fill = cur_open * (1.0 - slippage_pct)
                        rem_shares = active_trade['shares_remaining']
                        pnl_rem = (exit_fill - active_trade['entry_price']) * rem_shares
                        comm_exit = rem_shares * exit_fill * commission_pct
                        active_trade['commissions'] += comm_exit
                        capital += (pnl_rem - comm_exit)
                        
                        active_trade['exit_time'] = bar_time
                        active_trade['exit_price'] = exit_fill
                        active_trade['exit_reason'] = 'GAP_STOP' if not active_trade['tp1_hit'] else 'BREAKEVEN_STOP'
                        active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                        active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                        active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                        active_trade['capital_after'] = capital
                        trades.append(active_trade)
                        active_trade = None
                        state = State.IDLE
                    
                    # 2. Intra-bar checks if still active
                    if active_trade is not None:
                        # Conservative fill order: assume Stop Loss hit first if both hit in the same candle
                        if cur_low <= active_trade['stop']:
                            exit_fill = min(cur_open, active_trade['stop']) * (1.0 - slippage_pct)
                            rem_shares = active_trade['shares_remaining']
                            pnl_rem = (exit_fill - active_trade['entry_price']) * rem_shares
                            comm_exit = rem_shares * exit_fill * commission_pct
                            active_trade['commissions'] += comm_exit
                            capital += (pnl_rem - comm_exit)
                            
                            active_trade['exit_time'] = bar_time
                            active_trade['exit_price'] = exit_fill
                            active_trade['exit_reason'] = 'BREAKEVEN_STOP' if active_trade['tp1_hit'] else 'STOP_LOSS'
                            active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                            active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                            active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                            active_trade['capital_after'] = capital
                            trades.append(active_trade)
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
                                
                                active_trade['commissions'] += comm_tp1
                                active_trade['tp1_exit_time'] = bar_time
                                active_trade['tp1_exit_price'] = tp1_fill
                                active_trade['tp1_pnl'] = pnl_tp1
                                
                                # Trail Stop to Breakeven
                                if c_mgmt.get('use_breakeven', True):
                                    be_offset = c_mgmt.get('breakeven_offset_pct', 0.10) / 100.0
                                    active_trade['stop'] = active_trade['entry_price'] * (1.0 + be_offset)
                            
                            # Check TP2 full exit on remaining shares
                            if active_trade['tp1_hit'] and cur_high >= active_trade['tp2']:
                                tp2_fill = max(cur_open, active_trade['tp2']) * (1.0 - slippage_pct)
                                rem_shares = active_trade['shares_remaining']
                                pnl_rem = (tp2_fill - active_trade['entry_price']) * rem_shares
                                comm_tp2 = rem_shares * tp2_fill * commission_pct
                                active_trade['commissions'] += comm_tp2
                                capital += (pnl_rem - comm_tp2)
                                
                                active_trade['exit_time'] = bar_time
                                active_trade['exit_price'] = tp2_fill
                                active_trade['exit_reason'] = 'TP2_HIT'
                                active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                                active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                                active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                                active_trade['capital_after'] = capital
                                trades.append(active_trade)
                                active_trade = None
                                state = State.IDLE
                                
                            # Check Time Exit
                            elif c_mgmt.get('use_time_exit', True) and bars_held >= c_mgmt.get('max_hold_days', 10):
                                exit_fill = cur_close * (1.0 - slippage_pct)
                                rem_shares = active_trade['shares_remaining']
                                pnl_rem = (exit_fill - active_trade['entry_price']) * rem_shares
                                comm_exit = rem_shares * exit_fill * commission_pct
                                active_trade['commissions'] += comm_exit
                                capital += (pnl_rem - comm_exit)
                                
                                active_trade['exit_time'] = bar_time
                                active_trade['exit_price'] = exit_fill
                                active_trade['exit_reason'] = 'TIME_EXIT'
                                active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                                active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                                active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                                active_trade['capital_after'] = capital
                                trades.append(active_trade)
                                active_trade = None
                                state = State.IDLE

                elif t_dir == -1:  # SHORT MANAGEMENT
                    # 1. Open gap-through-stop check (subsequent bars only)
                    if i > active_trade['entry_bar'] and cur_open >= active_trade['stop']:
                        exit_fill = cur_open * (1.0 + slippage_pct)
                        rem_shares = active_trade['shares_remaining']
                        pnl_rem = (active_trade['entry_price'] - exit_fill) * rem_shares
                        comm_exit = rem_shares * exit_fill * commission_pct
                        active_trade['commissions'] += comm_exit
                        capital += (pnl_rem - comm_exit)
                        
                        active_trade['exit_time'] = bar_time
                        active_trade['exit_price'] = exit_fill
                        active_trade['exit_reason'] = 'GAP_STOP' if not active_trade['tp1_hit'] else 'BREAKEVEN_STOP'
                        active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                        active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                        active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                        active_trade['capital_after'] = capital
                        trades.append(active_trade)
                        active_trade = None
                        state = State.IDLE
                    
                    # 2. Intra-bar checks if still active
                    if active_trade is not None:
                        if cur_high >= active_trade['stop']:
                            exit_fill = max(cur_open, active_trade['stop']) * (1.0 + slippage_pct)
                            rem_shares = active_trade['shares_remaining']
                            pnl_rem = (active_trade['entry_price'] - exit_fill) * rem_shares
                            comm_exit = rem_shares * exit_fill * commission_pct
                            active_trade['commissions'] += comm_exit
                            capital += (pnl_rem - comm_exit)
                            
                            active_trade['exit_time'] = bar_time
                            active_trade['exit_price'] = exit_fill
                            active_trade['exit_reason'] = 'BREAKEVEN_STOP' if active_trade['tp1_hit'] else 'STOP_LOSS'
                            active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                            active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                            active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                            active_trade['capital_after'] = capital
                            trades.append(active_trade)
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
                                
                                active_trade['commissions'] += comm_tp1
                                active_trade['tp1_exit_time'] = bar_time
                                active_trade['tp1_exit_price'] = tp1_fill
                                active_trade['tp1_pnl'] = pnl_tp1
                                
                                if c_mgmt.get('use_breakeven', True):
                                    be_offset = c_mgmt.get('breakeven_offset_pct', 0.10) / 100.0
                                    active_trade['stop'] = active_trade['entry_price'] * (1.0 - be_offset)
                                    
                            # Check TP2 full exit on remaining shares
                            if active_trade['tp1_hit'] and cur_low <= active_trade['tp2']:
                                tp2_fill = min(cur_open, active_trade['tp2']) * (1.0 + slippage_pct)
                                rem_shares = active_trade['shares_remaining']
                                pnl_rem = (active_trade['entry_price'] - tp2_fill) * rem_shares
                                comm_tp2 = rem_shares * tp2_fill * commission_pct
                                active_trade['commissions'] += comm_tp2
                                capital += (pnl_rem - comm_tp2)
                                
                                active_trade['exit_time'] = bar_time
                                active_trade['exit_price'] = tp2_fill
                                active_trade['exit_reason'] = 'TP2_HIT'
                                active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                                active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                                active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                                active_trade['capital_after'] = capital
                                trades.append(active_trade)
                                active_trade = None
                                state = State.IDLE
                                
                            # Check Time Exit
                            elif c_mgmt.get('use_time_exit', True) and bars_held >= c_mgmt.get('max_hold_days', 10):
                                exit_fill = cur_close * (1.0 + slippage_pct)
                                rem_shares = active_trade['shares_remaining']
                                pnl_rem = (active_trade['entry_price'] - exit_fill) * rem_shares
                                comm_exit = rem_shares * exit_fill * commission_pct
                                active_trade['commissions'] += comm_exit
                                capital += (pnl_rem - comm_exit)
                                
                                active_trade['exit_time'] = bar_time
                                active_trade['exit_price'] = exit_fill
                                active_trade['exit_reason'] = 'TIME_EXIT'
                                active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
                                active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
                                active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
                                active_trade['capital_after'] = capital
                                trades.append(active_trade)
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
                        
                        long_signal = (setup_bars >= c_base['min_setup_bars'] and bull_break and 
                                       row['BullMom'] and row['LongTrendOK'] and row['BreakoutVolOK'])
                                       
                        short_signal = (setup_bars >= c_base['min_setup_bars'] and bear_break and 
                                        row['BearMom'] and row['ShortTrendOK'] and row['BreakoutVolOK'])
                                        
                        if long_signal:
                            structure_stop = setup_support * (1.0 - c_risk['structure_buffer_pct'] / 100.0)
                            atr_stop = cur_close - row['ATR'] * c_risk['atr_multiplier']
                            raw_stop = max(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                            max_risk_stop = cur_close * (1.0 - c_risk['max_stop_pct'] / 100.0)
                            min_risk_stop = cur_close * (1.0 - c_risk['min_stop_pct'] / 100.0)
                            final_stop = min(max(raw_stop, max_risk_stop), min_risk_stop)
                            
                            pending_signal = {
                                'direction': 1,
                                'signal_bar': i,
                                'signal_time': bar_time,
                                'signal_close': cur_close,
                                'stop': final_stop,
                                'resistance': setup_resistance,
                                'support': setup_support,
                            }
                            state = State.IDLE
                            
                        elif short_signal:
                            structure_stop = setup_resistance * (1.0 + c_risk['structure_buffer_pct'] / 100.0)
                            atr_stop = cur_close + row['ATR'] * c_risk['atr_multiplier']
                            raw_stop = min(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                            max_risk_stop = cur_close * (1.0 + c_risk['max_stop_pct'] / 100.0)
                            min_risk_stop = cur_close * (1.0 + c_risk['min_stop_pct'] / 100.0)
                            final_stop = max(min(raw_stop, max_risk_stop), min_risk_stop)
                            
                            pending_signal = {
                                'direction': -1,
                                'signal_bar': i,
                                'signal_time': bar_time,
                                'signal_close': cur_close,
                                'stop': final_stop,
                                'resistance': setup_resistance,
                                'support': setup_support,
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
            rem_shares = active_trade['shares_remaining']
            
            if active_trade['direction'] == 1:
                exit_fill = last_close * (1.0 - slippage_pct)
                pnl_rem = (exit_fill - active_trade['entry_price']) * rem_shares
            else:
                exit_fill = last_close * (1.0 + slippage_pct)
                pnl_rem = (active_trade['entry_price'] - exit_fill) * rem_shares
                
            comm_exit = rem_shares * exit_fill * commission_pct
            active_trade['commissions'] += comm_exit
            capital += (pnl_rem - comm_exit)
            
            active_trade['exit_time'] = final_time
            active_trade['exit_price'] = exit_fill
            active_trade['exit_reason'] = 'END_OF_DATA'
            active_trade['gross_pnl'] = active_trade['tp1_pnl'] + pnl_rem
            active_trade['net_pnl'] = active_trade['gross_pnl'] - active_trade['commissions']
            active_trade['realized_r'] = active_trade['net_pnl'] / active_trade['risk_cash'] if active_trade['risk_cash'] > 0 else 0.0
            active_trade['capital_after'] = capital
            trades.append(active_trade)
            active_trade = None

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
