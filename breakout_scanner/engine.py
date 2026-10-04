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
            
            elif state == State.CONTRACTION:
                setup_bars = i - setup_start + 1
                
                self.setup.resistance = row['PrevBaseHigh']
                self.setup.support = row['PrevBaseLow']
                
                if setup_bars > c_base['max_setup_bars']:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                else:
                    bull_break = row['Close'] > row['PrevBaseHigh'] * (1.0 + c_break['buffer_pct']/100.0)
                    bear_break = row['Close'] < row['PrevBaseLow'] * (1.0 - c_break['buffer_pct']/100.0)
                    
                    long_signal = (setup_bars >= c_base['min_setup_bars'] and bull_break and 
                                   row['BullMom'] and row['LongTrendOK'] and row['BreakoutVolOK'])
                                   
                    short_signal = (setup_bars >= c_base['min_setup_bars'] and bear_break and 
                                    row['BearMom'] and row['ShortTrendOK'] and row['BreakoutVolOK'])
                                    
                    if long_signal:
                        state = State.BUY
                        self.setup.state = State.BUY
                        self.setup.direction = 1
                        self.setup.entry = row['Close']
                        self.setup.stop = row['PrevBaseLow'] * (1.0 - c_risk['stop_loss_buffer_pct']/100.0)
                        risk = abs(self.setup.entry - self.setup.stop)
                        self.setup.target = self.setup.entry + risk * c_risk['take_profit_r']
                        self.setup.breakout_index = i
                        
                    elif short_signal:
                        state = State.SELL
                        self.setup.state = State.SELL
                        self.setup.direction = -1
                        self.setup.entry = row['Close']
                        self.setup.stop = row['PrevBaseHigh'] * (1.0 + c_risk['stop_loss_buffer_pct']/100.0)
                        risk = abs(self.setup.entry - self.setup.stop)
                        self.setup.target = self.setup.entry - risk * c_risk['take_profit_r']
                        self.setup.breakout_index = i

            elif state in (State.BUY, State.SELL):
                # Trade active, check for TP/SL
                active_dir = self.setup.direction
                hit_target = (row['High'] >= self.setup.target) if active_dir == 1 else (row['Low'] <= self.setup.target)
                hit_stop = (row['Low'] <= self.setup.stop) if active_dir == 1 else (row['High'] >= self.setup.stop)
                
                if hit_target or hit_stop:
                    state = State.IDLE
                    self.setup = Setup(self.symbol)
                    
        # Calculate trailing trend for display
        self.setup.trend = 1 if df['Close'].iloc[-1] > df['SMA'].iloc[-1] else -1
        
        return self.setup
