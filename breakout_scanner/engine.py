import pandas as pd
import numpy as np
from .models import Setup, State

def get_sma(series, window):
    return series.rolling(window=window).mean()

class BreakoutEngine:
    def __init__(self, cfg, symbol): 
        self.cfg = cfg
        self.symbol = symbol
        self.setup = Setup(symbol)

    def run(self, raw):
        df = raw.copy()
        c_trend = self.cfg['trend']
        c_base = self.cfg['base']
        c_vol = self.cfg['volume']
        c_break = self.cfg['breakout']
        c_risk = self.cfg['risk']
        c_tgt = self.cfg['target']
        c_mgmt = self.cfg['management']
        
        # ATR
        df['PrevClose'] = df['Close'].shift(1)
        df['TR'] = np.maximum(df['High'] - df['Low'], 
                   np.maximum(abs(df['High'] - df['PrevClose']), 
                              abs(df['Low'] - df['PrevClose'])))
        df['ATR'] = df['TR'].ewm(alpha=1/c_risk['atr_length'], adjust=False).mean()
        
        # Structure Targets
        df['StructTgtHigh'] = df['High'].shift(1).rolling(c_tgt['target_lookback']).max()
        df['StructTgtLow'] = df['Low'].shift(1).rolling(c_tgt['target_lookback']).min()
        
        # Simple Day Counter based on index changes if DatetimeIndex
        df['Day'] = df.index.floor('D') if isinstance(df.index, pd.DatetimeIndex) else df.index // 24
        
        # Precompute indicators (shift where necessary)
        df['SMA'] = get_sma(df['Close'], c_trend['sma_length'])
        df['CandleRange'] = np.maximum(df['High'] - df['Low'], 1e-12)
        df['BullCloseLoc'] = (df['Close'] - df['Low']) / df['CandleRange']
        df['BearCloseLoc'] = (df['High'] - df['Close']) / df['CandleRange']
        df['BodyPct'] = abs(df['Close'] - df['Open']) / df['CandleRange'] * 100
        
        df['BaseHigh'] = df['High'].rolling(c_base['base_lookback']).max()
        df['BaseLow'] = df['Low'].rolling(c_base['base_lookback']).min()
        df['BaseMid'] = (df['BaseHigh'] + df['BaseLow']) / 2
        df['BaseRangePct'] = np.where(df['BaseMid'] > 0, (df['BaseHigh'] - df['BaseLow']) / df['BaseMid'] * 100, 999.0)
        
        df['TightHigh'] = df['High'].rolling(c_base['tight_lookback']).max()
        df['TightLow'] = df['Low'].rolling(c_base['tight_lookback']).min()
        df['TightMid'] = (df['TightHigh'] + df['TightLow']) / 2
        df['TightRangePct'] = np.where(df['TightMid'] > 0, (df['TightHigh'] - df['TightLow']) / df['TightMid'] * 100, 999.0)
        
        # Previous Structure (shift by 1)
        df['PrevBaseHigh'] = df['High'].shift(1).rolling(c_base['base_lookback']).max()
        df['PrevBaseLow'] = df['Low'].shift(1).rolling(c_base['base_lookback']).min()
        
        # Tightening
        earlier_lookback = max(c_base['base_lookback'] - c_base['tight_lookback'], 2)
        df['EarlierHigh'] = df['High'].shift(c_base['tight_lookback']).rolling(earlier_lookback).max()
        df['EarlierLow'] = df['Low'].shift(c_base['tight_lookback']).rolling(earlier_lookback).min()
        df['EarlierMid'] = (df['EarlierHigh'] + df['EarlierLow']) / 2
        df['EarlierRangePct'] = np.where(df['EarlierMid'] > 0, (df['EarlierHigh'] - df['EarlierLow']) / df['EarlierMid'] * 100, 999.0)
        df['RangeTightening'] = df['TightRangePct'] < df['EarlierRangePct']
        df['TighteningOK'] = ~c_base['require_tightening'] | df['RangeTightening']
        
        # Volume
        df['AvgVolume'] = get_sma(df['Volume'], c_vol['volume_length'])
        df['BaseAvgVol'] = get_sma(df['Volume'], c_base['base_lookback'])
        df['TightAvgVol'] = get_sma(df['Volume'], c_base['tight_lookback'])
        df['VolContracting'] = df['TightAvgVol'] <= df['BaseAvgVol'] * c_vol['contraction_ratio']
        df['VolContractionOK'] = ~c_vol['use_volume_contraction'] | df['VolContracting']
        
        df['VolExpansion'] = df['Volume'] >= df['AvgVolume'] * c_vol['breakout_multiplier']
        df['BreakoutVolOK'] = ~c_vol['use_breakout_volume'] | df['VolExpansion']
        
        # Qualification
        df['BaseSmallEnough'] = df['BaseRangePct'] <= c_base['max_base_range_pct']
        df['TightSmallEnough'] = df['TightRangePct'] <= c_base['max_tight_range_pct']
        df['VCPQualified'] = df['BaseSmallEnough'] & df['TightSmallEnough'] & df['TighteningOK'] & df['VolContractionOK']
        
        # Trend
        df['BullTrend'] = df['Close'] > df['SMA']
        df['BearTrend'] = df['Close'] < df['SMA']
        df['LongTrendOK'] = ~c_trend['use_trend_filter'] | df['BullTrend']
        df['ShortTrendOK'] = ~c_trend['use_trend_filter'] | df['BearTrend']
        
        # Momentum
        df['BullMom'] = (df['Close'] > df['Open']) & (df['BullCloseLoc'] >= c_break['close_location']) & (~c_break['require_momentum_candle'] | (df['BodyPct'] >= c_break['min_body_pct']))
        df['BearMom'] = (df['Close'] < df['Open']) & (df['BearCloseLoc'] >= c_break['close_location']) & (~c_break['require_momentum_candle'] | (df['BodyPct'] >= c_break['min_body_pct']))
        
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
            
            elif state == State.CONTRACTION:
                setup_bars = i - setup_start + 1
                
                if setup_bars > c_base['max_setup_bars']:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                else:
                    bull_break = row['Close'] >= self.setup.resistance * (1.0 + c_break['buffer_pct']/100.0)
                    bear_break = row['Close'] <= self.setup.support * (1.0 - c_break['buffer_pct']/100.0)
                    
                    long_signal = (setup_bars >= c_base['min_setup_bars'] and bull_break and 
                                   row['BullMom'] and row['LongTrendOK'] and row['BreakoutVolOK'])
                                   
                    short_signal = (setup_bars >= c_base['min_setup_bars'] and bear_break and 
                                    row['BearMom'] and row['ShortTrendOK'] and row['BreakoutVolOK'])
                                    
                    if long_signal:
                        state = State.BUY
                        self.setup.state = State.BUY
                        self.setup.direction = 1
                        self.setup.entry = row['Close']
                        
                        structure_stop = self.setup.support * (1.0 - c_risk['structure_buffer_pct']/100.0)
                        atr_stop = self.setup.entry - row['ATR'] * c_risk['atr_multiplier']
                        raw_stop = max(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                        
                        max_risk_stop = self.setup.entry * (1.0 - c_risk['max_stop_pct']/100.0)
                        min_risk_stop = self.setup.entry * (1.0 - c_risk['min_stop_pct']/100.0)
                        final_stop = min(max(raw_stop, max_risk_stop), min_risk_stop)
                        
                        self.setup.stop = final_stop
                        self.setup.original_stop = final_stop
                        
                        risk = abs(self.setup.entry - self.setup.stop)
                        self.setup.tp1 = self.setup.entry + risk * c_tgt['tp1_r']
                        self.setup.tp2 = self.setup.entry + risk * c_tgt['tp2_r']
                        
                        structure_target = row['StructTgtHigh']
                        self.setup.tp3 = structure_target if structure_target > self.setup.tp2 else self.setup.entry + risk * 3.0
                        
                        self.setup.breakout_index = i
                        self.setup.entry_day = row['Day']
                        self.setup.tp1_hit = False
                        self.setup.tp2_hit = False
                        
                    elif short_signal:
                        state = State.SELL
                        self.setup.state = State.SELL
                        self.setup.direction = -1
                        self.setup.entry = row['Close']
                        
                        structure_stop = self.setup.resistance * (1.0 + c_risk['structure_buffer_pct']/100.0)
                        atr_stop = self.setup.entry + row['ATR'] * c_risk['atr_multiplier']
                        raw_stop = min(structure_stop, atr_stop) if c_risk['use_structure_stop'] else atr_stop
                        
                        max_risk_stop = self.setup.entry * (1.0 + c_risk['max_stop_pct']/100.0)
                        min_risk_stop = self.setup.entry * (1.0 + c_risk['min_stop_pct']/100.0)
                        final_stop = max(min(raw_stop, max_risk_stop), min_risk_stop)
                        
                        self.setup.stop = final_stop
                        self.setup.original_stop = final_stop
                        
                        risk = abs(self.setup.entry - self.setup.stop)
                        self.setup.tp1 = self.setup.entry - risk * c_tgt['tp1_r']
                        self.setup.tp2 = self.setup.entry - risk * c_tgt['tp2_r']
                        
                        structure_target = row['StructTgtLow']
                        self.setup.tp3 = structure_target if structure_target < self.setup.tp2 else self.setup.entry - risk * 3.0
                        
                        self.setup.breakout_index = i
                        self.setup.entry_day = row['Day']
                        self.setup.tp1_hit = False
                        self.setup.tp2_hit = False

            elif state in (State.BUY, State.SELL):
                active_dir = self.setup.direction
                
                # Update held days
                if self.setup.entry_day is not None and row['Day'] != self.setup.entry_day:
                    delta = row['Day'] - self.setup.entry_day
                    days_diff = delta.days if hasattr(delta, 'days') else 1
                    self.setup.held_days = days_diff
                
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
                hit_time = c_mgmt['use_time_exit'] and self.setup.held_days >= c_mgmt['max_hold_days']
                
                if hit_stop or hit_time:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                    
        # Calculate trailing trend for display
        self.setup.trend = 1 if df['Close'].iloc[-1] > df['SMA'].iloc[-1] else -1
        self.setup.current_price = df['Close'].iloc[-1]
        
        # Calculate how many bars since the breakout
        if self.setup.state in (State.BUY, State.SELL) and self.setup.breakout_index is not None:
            self.setup.bars_since_breakout = len(df) - 1 - self.setup.breakout_index
        else:
            self.setup.bars_since_breakout = 0
            
        return self.setup
