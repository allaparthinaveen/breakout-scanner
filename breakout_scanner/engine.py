import pandas as pd
import numpy as np
from .models import Setup, State

def is_finite_num(val) -> bool:
    if val is None:
        return False
    try:
        return bool(np.isfinite(val))
    except Exception:
        return False

def get_sma(series, window):
    return series.rolling(window=window).mean()

def compute_indicators(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Precompute all technical indicators, structure levels, and signals across raw bars."""
    df = raw.copy()
    c_trend = cfg['trend']
    c_base = cfg['base']
    c_vol = cfg['volume']
    c_break = cfg['breakout']
    c_risk = cfg['risk']
    c_tgt = cfg['target']
    c_mgmt = cfg['management']
    
    # ATR
    df['PrevClose'] = df['Close'].shift(1)
    df['TR'] = np.maximum(df['High'] - df['Low'], 
               np.maximum(abs(df['High'] - df['PrevClose']), 
                          abs(df['Low'] - df['PrevClose'])))
    df['ATR'] = df['TR'].ewm(alpha=1/c_risk['atr_length'], adjust=False).mean()
    
    # Structure Targets
    df['StructTgtHigh'] = df['High'].shift(1).rolling(c_tgt['target_lookback']).max()
    df['StructTgtLow'] = df['Low'].shift(1).rolling(c_tgt['target_lookback']).min()
    
    # Day Counter based on index timestamps or timeframe scaling
    if isinstance(df.index, pd.DatetimeIndex):
        df['Day'] = df.index.floor('D')
    else:
        try:
            dt_idx = pd.to_datetime(df.index)
            df['Day'] = dt_idx.floor('D')
        except Exception:
            tf = str(cfg.get('management', {}).get('timeframe', cfg.get('interval', '1d'))).lower()
            bars_per_day = 1
            if tf in ('1h', '60m'):
                bars_per_day = 7 if str(cfg.get('market', '')).lower() == 'nse' else 24
            elif tf in ('15m',):
                bars_per_day = 25
            df['Day'] = df.index // bars_per_day
    
    # Precompute indicators (shift where necessary)
    df['SMA'] = get_sma(df['Close'], c_trend['sma_length'])
    df['CandleRange'] = np.maximum(df['High'] - df['Low'], 1e-12)
    df['BullCloseLoc'] = (df['Close'] - df['Low']) / df['CandleRange']
    df['BearCloseLoc'] = (df['High'] - df['Close']) / df['CandleRange']
    df['BodyPct'] = abs(df['Close'] - df['Open']) / df['CandleRange'] * 100
    
    df['BaseHigh'] = df['High'].rolling(c_base['base_lookback']).max()
    df['BaseLow'] = df['Low'].rolling(c_base['base_lookback']).min()
    df['BaseMid'] = (df['BaseHigh'] + df['BaseLow']) / 2
    df['BaseRangePct'] = np.where((df['BaseMid'] > 0) & np.isfinite(df['BaseMid']), (df['BaseHigh'] - df['BaseLow']) / np.maximum(df['BaseMid'], 1e-12) * 100, 999.0)
    
    df['TightHigh'] = df['High'].rolling(c_base['tight_lookback']).max()
    df['TightLow'] = df['Low'].rolling(c_base['tight_lookback']).min()
    df['TightMid'] = (df['TightHigh'] + df['TightLow']) / 2
    df['TightRangePct'] = np.where((df['TightMid'] > 0) & np.isfinite(df['TightMid']), (df['TightHigh'] - df['TightLow']) / np.maximum(df['TightMid'], 1e-12) * 100, 999.0)
    
    # Previous Structure (shift by 1)
    df['PrevBaseHigh'] = df['High'].shift(1).rolling(c_base['base_lookback']).max()
    df['PrevBaseLow'] = df['Low'].shift(1).rolling(c_base['base_lookback']).min()
    
    # Tightening
    earlier_lookback = max(c_base['base_lookback'] - c_base['tight_lookback'], 2)
    df['EarlierHigh'] = df['High'].shift(c_base['tight_lookback']).rolling(earlier_lookback).max()
    df['EarlierLow'] = df['Low'].shift(c_base['tight_lookback']).rolling(earlier_lookback).min()
    df['EarlierMid'] = (df['EarlierHigh'] + df['EarlierLow']) / 2
    df['EarlierRangePct'] = np.where((df['EarlierMid'] > 0) & np.isfinite(df['EarlierMid']), (df['EarlierHigh'] - df['EarlierLow']) / np.maximum(df['EarlierMid'], 1e-12) * 100, 999.0)
    df['RangeTightening'] = df['TightRangePct'] < df['EarlierRangePct']
    df['TighteningOK'] = df['RangeTightening'] if c_base.get('require_tightening', False) else True
    
    # Volume: use shift(1) so current breakout bar does not inflate its own benchmark
    prev_vol = df['Volume'].shift(1)
    df['AvgVolume'] = get_sma(prev_vol, c_vol['volume_length'])
    df['BaseAvgVol'] = get_sma(prev_vol, c_base['base_lookback'])
    df['TightAvgVol'] = get_sma(prev_vol, c_base['tight_lookback'])
    df['VolContracting'] = df['TightAvgVol'] <= df['BaseAvgVol'] * c_vol['contraction_ratio']
    df['VolContractionOK'] = df['VolContracting'] if c_vol.get('use_volume_contraction', False) else True
    
    # Hourly Volume Profile (Same-hour-of-day median across prior sessions)
    benchmark_vol = df['AvgVolume']
    if c_vol.get('use_hourly_volume_profile', False) and hasattr(df.index, 'hour'):
        hour_series = df.index.hour
        lookback = c_vol.get('hourly_profile_days', 20)
        hourly_benchmark = pd.Series(index=df.index, dtype=float)
        for h in hour_series.unique():
            mask = (hour_series == h)
            h_vols = df.loc[mask, 'Volume'].shift(1)
            hourly_benchmark.loc[mask] = h_vols.rolling(lookback, min_periods=3).median()
        benchmark_vol = hourly_benchmark.combine_first(df['AvgVolume'])

    df['VolExpansion'] = df['Volume'] >= (benchmark_vol * c_vol['breakout_multiplier'])
    df['BreakoutVolOK'] = df['VolExpansion'] if c_vol.get('use_breakout_volume', True) else True
    
    # Qualification
    df['BaseSmallEnough'] = df['BaseRangePct'] <= c_base['max_base_range_pct']
    df['TightSmallEnough'] = df['TightRangePct'] <= c_base['max_tight_range_pct']
    df['VCPQualified'] = df['BaseSmallEnough'] & df['TightSmallEnough'] & df['TighteningOK'] & df['VolContractionOK']
    
    # Trend
    df['BullTrend'] = df['Close'] > df['SMA']
    df['BearTrend'] = df['Close'] < df['SMA']
    
    if c_trend.get('use_slope_filter', False):
        slope_bars = c_trend.get('slope_lookback_bars', 5)
        sma_slope_up = df['SMA'] > df['SMA'].shift(slope_bars)
        sma_slope_down = df['SMA'] < df['SMA'].shift(slope_bars)
        bull_trend_ok = df['BullTrend'] & sma_slope_up
        bear_trend_ok = df['BearTrend'] & sma_slope_down
    else:
        bull_trend_ok = df['BullTrend']
        bear_trend_ok = df['BearTrend']

    df['LongTrendOK'] = bull_trend_ok if c_trend.get('use_trend_filter', True) else True
    df['ShortTrendOK'] = bear_trend_ok if c_trend.get('use_trend_filter', True) else True
    
    # Momentum
    mom_candle_ok = (df['BodyPct'] >= c_break['min_body_pct']) if c_break.get('require_momentum_candle', True) else True
    df['BullMom'] = (df['Close'] > df['Open']) & (df['BullCloseLoc'] >= c_break['close_location']) & mom_candle_ok
    df['BearMom'] = (df['Close'] < df['Open']) & (df['BearCloseLoc'] >= c_break['close_location']) & mom_candle_ok

    return df

class BreakoutEngine:
    def __init__(self, cfg, symbol): 
        self.cfg = cfg
        self.symbol = symbol
        self.setup = Setup(symbol)

    def run(self, raw):
        self.setup = Setup(self.symbol)
        if raw is None or raw.empty or len(raw) < 10:
            return self.setup
        df = compute_indicators(raw, self.cfg)
        c_trend = self.cfg['trend']
        c_base = self.cfg['base']
        c_vol = self.cfg['volume']
        c_break = self.cfg['breakout']
        c_risk = self.cfg['risk']
        c_tgt = self.cfg['target']
        c_mgmt = self.cfg['management']
        
        state = State.IDLE
        setup_start = -1
        
        # Simulate state machine row by row
        for i in range(c_base['base_lookback'] + c_base['tight_lookback'], len(df)):
            row = df.iloc[i]
            
            if state == State.IDLE:
                if row['VCPQualified']:
                    state = State.CONTRACTION
                    setup_start = i
                    self.setup = Setup(self.symbol, state=State.CONTRACTION)
                    self.setup.cons_index = i
                    self.setup.resistance = row['PrevBaseHigh']
                    self.setup.support = row['PrevBaseLow']
                    if is_finite_num(self.setup.resistance) and is_finite_num(self.setup.support) and self.setup.support > 0:
                        mid = (self.setup.resistance + self.setup.support) / 2.0
                        if mid > 0:
                            self.setup.base_width_pct = (self.setup.resistance - self.setup.support) / mid * 100.0
                    base_vol = row['BaseAvgVol'] if ('BaseAvgVol' in row and is_finite_num(row['BaseAvgVol']) and row['BaseAvgVol'] > 0) else row.get('AvgVolume', 0.0)
                    if is_finite_num(base_vol) and base_vol > 0 and 'TightAvgVol' in row and is_finite_num(row['TightAvgVol']):
                        vdu = float(row['TightAvgVol'] / base_vol)
                        if is_finite_num(vdu):
                            self.setup.vdu_ratio = round(vdu, 2)
                            self.setup.vdu_confirmed = vdu <= c_vol.get('vdu_threshold', 0.75)
                    earlier_range = row['EarlierRangePct'] if ('EarlierRangePct' in row and is_finite_num(row['EarlierRangePct']) and 0 < row['EarlierRangePct'] < 500) else row.get('BaseRangePct', 0.0)
                    if is_finite_num(earlier_range) and 'TightRangePct' in row and is_finite_num(row['TightRangePct']) and 0 < earlier_range < 500 and 0 < row['TightRangePct'] < 500:
                        decay = float(row['TightRangePct'] / earlier_range)
                        if is_finite_num(decay):
                            self.setup.decay_ratio = round(decay, 2)
                            self.setup.tightening_confirmed = decay <= c_base.get('tightening_threshold', 0.70)
            
            elif state == State.CONTRACTION:
                setup_bars = i - setup_start + 1
                
                if setup_bars > c_base['max_setup_bars']:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                else:
                    # Consolidation Quality Gate:
                    # Ensure at least 75% of candle closes in contraction phase reside strictly within base range
                    min_density = c_base.get('min_box_density_pct', 75.0)
                    res = self.setup.resistance
                    sup = self.setup.support
                    buf = c_break.get('buffer_pct', 0.10) / 100.0
                    
                    if i > setup_start and is_finite_num(res) and is_finite_num(sup) and res > sup:
                        prior_closes = df['Close'].iloc[setup_start:i]
                        inside_mask = (prior_closes >= sup * (1.0 - buf)) & (prior_closes <= res * (1.0 + buf))
                        cons_density = float(inside_mask.sum() / len(prior_closes) * 100.0) if len(prior_closes) > 0 else 100.0
                        self.setup.cons_density_pct = round(cons_density, 1)
                        self.setup.cons_density_confirmed = cons_density >= min_density
                    else:
                        cons_density = 100.0
                        self.setup.cons_density_pct = 100.0
                        self.setup.cons_density_confirmed = True
                    
                    # If enough bars have formed and density fails the quality gate, reset to IDLE (erratic chop)
                    if setup_bars >= c_base['min_setup_bars'] and cons_density < min_density:
                        state = State.IDLE
                        self.setup = Setup(self.symbol)
                        continue

                    bull_break = row['Close'] >= self.setup.resistance * (1.0 + c_break['buffer_pct']/100.0)
                    bear_break = row['Close'] <= self.setup.support * (1.0 - c_break['buffer_pct']/100.0)
                    
                    long_signal = (setup_bars >= c_base['min_setup_bars'] and bull_break and 
                                   row['BullMom'] and row['LongTrendOK'] and row['BreakoutVolOK'] and
                                   self.setup.cons_density_confirmed)
                                   
                    short_signal = (c_break.get('allow_shorts', True) and
                                    setup_bars >= c_base['min_setup_bars'] and bear_break and 
                                    row['BearMom'] and row['ShortTrendOK'] and row['BreakoutVolOK'] and
                                    self.setup.cons_density_confirmed)
                                    
                    if long_signal:
                        structure_stop = self.setup.support * (1.0 - c_risk['structure_buffer_pct']/100.0)
                        atr_stop = row['Close'] - row['ATR'] * c_risk['atr_multiplier']
                        raw_stop = max(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                        
                        # Apply minimum stop floor (noise filter)
                        min_risk_stop = row['Close'] * (1.0 - c_risk['min_stop_pct']/100.0)
                        final_stop = min(raw_stop, min_risk_stop)
                        
                        # Calculate structural risk percentage
                        risk = abs(row['Close'] - final_stop)
                        risk_pct = (risk / row['Close'] * 100.0) if (is_finite_num(row['Close']) and row['Close'] > 0) else 0.0
                        max_allowed_stop = c_risk.get('max_stop_pct', 5.0)
                        
                        # Cost / Friction Gate: round-trip cost relative to stop distance
                        round_trip_cost_pct = 2.0 * (c_risk.get('slippage_pct', 0.05) + c_risk.get('commission_pct', 0.05))
                        friction_r = (round_trip_cost_pct / risk_pct) if (is_finite_num(risk_pct) and risk_pct > 0) else 999.0
                        cost_ok = (friction_r <= c_risk.get('max_friction_r', 0.15)) if c_risk.get('use_cost_filter', False) else True
                        
                        # Risk Separation Rule: If structural risk > max_stop_pct or friction too high, REJECT setup!
                        if risk_pct > max_allowed_stop or not cost_ok or not is_finite_num(risk_pct):
                            state = State.IDLE
                            self.setup = Setup(self.symbol)
                        else:
                            state = State.BUY
                            self.setup.state = State.BUY
                            self.setup.direction = 1
                            self.setup.entry = float(row['Close'])
                            self.setup.stop = float(final_stop)
                            self.setup.original_stop = float(final_stop)
                            self.setup.risk_pct = round(float(risk_pct), 2)
                            self.setup.friction_r = round(float(friction_r), 3) if is_finite_num(friction_r) else 999.0
                            self.setup.tp1 = float(self.setup.entry + risk * c_tgt['tp1_r'])
                            self.setup.tp2 = float(self.setup.entry + risk * c_tgt['tp2_r'])
                            self.setup.target = self.setup.tp2
                            self.setup.breakout_level = float(self.setup.resistance) if self.setup.resistance else self.setup.entry
                            
                            structure_target = row['StructTgtHigh']
                            self.setup.tp3 = float(structure_target) if (is_finite_num(structure_target) and structure_target > self.setup.tp2) else float(self.setup.entry + risk * 3.0)
                            
                            self.setup.breakout_index = i
                            self.setup.breakout_time = str(row.name) if hasattr(row, 'name') and row.name is not None else str(row.get('Day', i))
                            self.setup.entry_day = row['Day']
                            self.setup.tp1_hit = False
                            self.setup.tp2_hit = False
                            self.setup.confirmation_status = "PENDING_OPEN" if c_break.get('use_open_confirmation', False) else "CONFIRMED"
                            if is_finite_num(row['AvgVolume']) and row['AvgVolume'] > 0 and is_finite_num(row['Volume']):
                                rvol_val = float(row['Volume'] / row['AvgVolume'])
                                if is_finite_num(rvol_val):
                                    self.setup.rvol = round(rvol_val, 2)
                        
                    elif short_signal:
                        structure_stop = self.setup.resistance * (1.0 + c_risk['structure_buffer_pct']/100.0)
                        atr_stop = row['Close'] + row['ATR'] * c_risk['atr_multiplier']
                        raw_stop = min(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                        
                        # Apply minimum stop floor (noise filter)
                        min_risk_stop = row['Close'] * (1.0 + c_risk['min_stop_pct']/100.0)
                        final_stop = max(raw_stop, min_risk_stop)
                        
                        # Calculate structural risk percentage
                        risk = abs(row['Close'] - final_stop)
                        risk_pct = (risk / row['Close'] * 100.0) if (is_finite_num(row['Close']) and row['Close'] > 0) else 0.0
                        max_allowed_stop = c_risk.get('max_stop_pct', 5.0)
                        
                        # Cost / Friction Gate: round-trip cost relative to stop distance
                        round_trip_cost_pct = 2.0 * (c_risk.get('slippage_pct', 0.05) + c_risk.get('commission_pct', 0.05))
                        friction_r = (round_trip_cost_pct / risk_pct) if (is_finite_num(risk_pct) and risk_pct > 0) else 999.0
                        cost_ok = (friction_r <= c_risk.get('max_friction_r', 0.15)) if c_risk.get('use_cost_filter', False) else True
                        
                        # Risk Separation Rule: If structural risk > max_stop_pct or friction too high, REJECT setup!
                        if risk_pct > max_allowed_stop or not cost_ok or not is_finite_num(risk_pct):
                            state = State.IDLE
                            self.setup = Setup(self.symbol)
                        else:
                            state = State.SELL
                            self.setup.state = State.SELL
                            self.setup.direction = -1
                            self.setup.entry = float(row['Close'])
                            self.setup.stop = float(final_stop)
                            self.setup.original_stop = float(final_stop)
                            self.setup.risk_pct = round(float(risk_pct), 2)
                            self.setup.friction_r = round(float(friction_r), 3) if is_finite_num(friction_r) else 999.0
                            self.setup.tp1 = float(self.setup.entry - risk * c_tgt['tp1_r'])
                            self.setup.tp2 = float(self.setup.entry - risk * c_tgt['tp2_r'])
                            self.setup.target = self.setup.tp2
                            self.setup.breakout_level = float(self.setup.support) if self.setup.support else self.setup.entry
                            
                            structure_target = row['StructTgtLow']
                            self.setup.tp3 = float(structure_target) if (is_finite_num(structure_target) and structure_target < self.setup.tp2) else float(self.setup.entry - risk * 3.0)
                        
                            self.setup.breakout_index = i
                            self.setup.breakout_time = str(row.name) if hasattr(row, 'name') and row.name is not None else str(row.get('Day', i))
                            self.setup.entry_day = row['Day']
                            self.setup.tp1_hit = False
                            self.setup.tp2_hit = False
                            self.setup.confirmation_status = "PENDING_OPEN" if c_break.get('use_open_confirmation', False) else "CONFIRMED"
                            if is_finite_num(row['AvgVolume']) and row['AvgVolume'] > 0 and is_finite_num(row['Volume']):
                                rvol_val = float(row['Volume'] / row['AvgVolume'])
                                if is_finite_num(rvol_val):
                                    self.setup.rvol = round(rvol_val, 2)

            elif state in (State.BUY, State.SELL):
                active_dir = self.setup.direction
                
                # Update held days
                if self.setup.entry_day is not None and row['Day'] != self.setup.entry_day:
                    delta = row['Day'] - self.setup.entry_day
                    days_diff = delta.days if hasattr(delta, 'days') else int(delta)
                    self.setup.held_days = max(1, days_diff)
                
                bars_in_trade = i - (self.setup.breakout_index or i)
                
                # Check Next-Bar Open Confirmation
                hit_failed_confirmation = False
                if c_break.get('use_open_confirmation', False) and bars_in_trade == 1:
                    max_adverse_gap = c_break.get('max_open_gap_pct', 0.50)
                    sig_close = self.setup.entry
                    confirm_ok = True
                    if active_dir == 1:
                        gap_down_pct = ((sig_close - row['Open']) / sig_close * 100.0) if sig_close else 0.0
                        holds_pivot = (row['Open'] >= self.setup.resistance * 0.999) if self.setup.resistance else True
                        if gap_down_pct > max_adverse_gap or not holds_pivot:
                            confirm_ok = False
                    elif active_dir == -1:
                        gap_up_pct = ((row['Open'] - sig_close) / sig_close * 100.0) if sig_close else 0.0
                        holds_pivot = (row['Open'] <= self.setup.support * 1.001) if self.setup.support else True
                        if gap_up_pct > max_adverse_gap or not holds_pivot:
                            confirm_ok = False
                    if not confirm_ok:
                        hit_failed_confirmation = True
                    else:
                        self.setup.confirmation_status = "CONFIRMED"

                if hit_failed_confirmation:
                    self.setup.state = State.FAILED
                    self.setup.failed_breakout_confirmed = True
                    self.setup.failure_reason = "OPEN_CONFIRMATION_FAILED: Next-bar open gapped adversely or breached pivot"
                    state = State.IDLE
                    if i < len(df) - 1:
                        self.setup = Setup(self.symbol)
                    else:
                        break
                
                # Check Retest Hold (Pullback to broken pivot that successfully holds)
                if i > (self.setup.breakout_index or 0):
                    if active_dir == 1 and self.setup.resistance:
                        retest_touch = row['Low'] <= self.setup.resistance * 1.008
                        retest_hold = row['Close'] >= self.setup.resistance * 0.998
                        if retest_touch and retest_hold:
                            self.setup.retest_confirmed = True
                            self.setup.breakout_type = "RETEST_HOLD"
                            self.setup.retest_time = str(row.name) if hasattr(row, 'name') and row.name is not None else str(row.get('Day', i))
                    elif active_dir == -1 and self.setup.support:
                        retest_touch = row['High'] >= self.setup.support * 0.992
                        retest_hold = row['Close'] <= self.setup.support * 1.002
                        if retest_touch and retest_hold:
                            self.setup.retest_confirmed = True
                            self.setup.breakout_type = "RETEST_HOLD"
                            self.setup.retest_time = str(row.name) if hasattr(row, 'name') and row.name is not None else str(row.get('Day', i))
                
                # Check TP1
                hit_tp1 = (row['High'] >= self.setup.tp1) if active_dir == 1 else (row['Low'] <= self.setup.tp1)
                if hit_tp1 and not self.setup.tp1_hit:
                    self.setup.tp1_hit = True
                    if c_mgmt['use_breakeven']:
                        if active_dir == 1:
                            self.setup.stop = self.setup.entry * (1.0 + c_mgmt['breakeven_offset_pct']/100.0)
                        else:
                            self.setup.stop = self.setup.entry * (1.0 - c_mgmt['breakeven_offset_pct']/100.0)
                            
                # Check TP2
                hit_tp2 = (row['High'] >= self.setup.tp2) if active_dir == 1 else (row['Low'] <= self.setup.tp2)
                if hit_tp2 and not self.setup.tp2_hit:
                    self.setup.tp2_hit = True
                
                # Check Exits
                hit_stop = (row['Low'] <= self.setup.stop) if active_dir == 1 else (row['High'] >= self.setup.stop)
                hit_time = c_mgmt.get('use_time_exit', True) and (
                    self.setup.held_days >= c_mgmt.get('max_hold_days', 999) or
                    (c_mgmt.get('max_hold_bars') is not None and bars_in_trade >= c_mgmt['max_hold_bars'])
                )
                
                # Failed Breakout Exit Check
                hit_failed_breakout = False
                if c_mgmt.get('use_failed_breakout_exit', False) and bars_in_trade <= c_mgmt.get('failed_breakout_max_bars', 3):
                    if active_dir == 1 and self.setup.resistance and row['Close'] < self.setup.resistance:
                        hit_failed_breakout = True
                    elif active_dir == -1 and self.setup.support and row['Close'] > self.setup.support:
                        hit_failed_breakout = True

                if hit_failed_breakout:
                    self.setup.state = State.FAILED
                    self.setup.failed_breakout_confirmed = True
                    self.setup.failure_reason = "FAILED_BREAKOUT: Re-entered base within setup window"
                    state = State.IDLE
                    # If this happened on the latest bar, preserve the failed setup so caller/holder gets the exit alert!
                    # Only reset setup if there are further historical bars to process.
                    if i < len(df) - 1:
                        self.setup = Setup(self.symbol)
                    else:
                        break
                elif hit_stop or hit_time or hit_tp2:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                    if i >= len(df) - 1:
                        break
                    
        # Calculate trailing trend for display
        if not df.empty and is_finite_num(df['SMA'].iloc[-1]) and is_finite_num(df['Close'].iloc[-1]):
            self.setup.trend = 1 if df['Close'].iloc[-1] > df['SMA'].iloc[-1] else -1
        else:
            self.setup.trend = 0
        self.setup.current_price = float(df['Close'].iloc[-1]) if (not df.empty and is_finite_num(df['Close'].iloc[-1])) else None
        
        # Calculate current unrealized PnL % if in trade or failed
        if self.setup.state in (State.BUY, State.SELL, State.FAILED) and is_finite_num(self.setup.entry) and is_finite_num(self.setup.current_price) and self.setup.entry > 0:
            if self.setup.direction == 1:
                self.setup.pnl_pct = round(float((self.setup.current_price - self.setup.entry) / self.setup.entry * 100.0), 2)
            elif self.setup.direction == -1:
                self.setup.pnl_pct = round(float((self.setup.entry - self.setup.current_price) / self.setup.entry * 100.0), 2)
        
        # Calculate how many bars since the breakout
        if self.setup.state in (State.BUY, State.SELL, State.FAILED) and self.setup.breakout_index is not None:
            self.setup.bars_since_breakout = len(df) - 1 - self.setup.breakout_index
        else:
            self.setup.bars_since_breakout = 0

        # Update trailing VDU / RVOL / Coil Decay for current state display
        if self.setup.state == State.CONTRACTION and not df.empty:
            b_vol = df['BaseAvgVol'].iloc[-1] if ('BaseAvgVol' in df and is_finite_num(df['BaseAvgVol'].iloc[-1]) and df['BaseAvgVol'].iloc[-1] > 0) else df.get('AvgVolume', pd.Series()).iloc[-1]
            if is_finite_num(b_vol) and b_vol > 0 and 'TightAvgVol' in df and is_finite_num(df['TightAvgVol'].iloc[-1]):
                current_vdu = float(df['TightAvgVol'].iloc[-1] / b_vol)
                if is_finite_num(current_vdu):
                    self.setup.vdu_ratio = round(current_vdu, 2)
                    self.setup.vdu_confirmed = current_vdu <= c_vol.get('vdu_threshold', 0.75)
            earlier_range = df['EarlierRangePct'].iloc[-1] if ('EarlierRangePct' in df and is_finite_num(df['EarlierRangePct'].iloc[-1]) and 0 < df['EarlierRangePct'].iloc[-1] < 500) else df.get('BaseRangePct', pd.Series()).iloc[-1]
            if is_finite_num(earlier_range) and 'TightRangePct' in df and is_finite_num(df['TightRangePct'].iloc[-1]) and 0 < earlier_range < 500 and 0 < df['TightRangePct'].iloc[-1] < 500:
                current_decay = float(df['TightRangePct'].iloc[-1] / earlier_range)
                if is_finite_num(current_decay):
                    self.setup.decay_ratio = round(current_decay, 2)
                    self.setup.tightening_confirmed = current_decay <= c_base.get('tightening_threshold', 0.70)
        elif self.setup.state in (State.BUY, State.SELL) and not df.empty:
            if self.setup.rvol is None and 'AvgVolume' in df and is_finite_num(df['AvgVolume'].iloc[-1]) and df['AvgVolume'].iloc[-1] > 0 and 'Volume' in df and is_finite_num(df['Volume'].iloc[-1]):
                vol_ratio = float(df['Volume'].iloc[-1] / df['AvgVolume'].iloc[-1])
                if is_finite_num(vol_ratio):
                    self.setup.rvol = round(vol_ratio, 2)
            
        return self.setup
