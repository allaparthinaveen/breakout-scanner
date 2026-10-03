import pandas as pd
import numpy as np
from .models import Setup, State
from .indicators import add_momentum_indicators

class BreakoutEngine:
    def __init__(self, cfg, symbol): 
        self.cfg = cfg
        self.symbol = symbol
        self.setup = Setup(symbol)

    def run(self, raw):
        df = raw.copy()
        if len(df) < 80: return self.setup
        
        c = self.cfg['consolidation']
        m = self.cfg['momentum']
        
        df = add_momentum_indicators(df, sma_len=self.cfg['structure']['sma_trend_lookback'], vol_len=m['volume_lookback'])
        
        i = len(df) - 1
        
        # Macro trend from SMA
        close = float(df.Close.iloc[i])
        sma = float(df.SMA.iloc[i]) if pd.notna(df.SMA.iloc[i]) else close
        trend = 1 if close > sma else -1
        self.setup.trend = trend
        
        s = self.setup
        
        hi = float(df.High.iloc[i-c['min_bars']+1:i+1].max())
        lo = float(df.Low.iloc[i-c['min_bars']+1:i+1].min())
        mid = (hi + lo) / 2
        pct = (hi - lo) / mid * 100 if mid else 999
        
        # Calculate recent volatility (Average of daily ranges)
        ranges = df.High.iloc[i-c['min_bars']+1:i+1] - df.Low.iloc[i-c['min_bars']+1:i+1]
        avg_range = ranges.mean()
        volatility_pct = (avg_range / mid * 100) if mid else 999
        
        if s.state in (State.IDLE, State.FAILED) and pct <= c['max_range_pct'] and volatility_pct <= c['max_volatility_pct']:
            s = Setup(self.symbol, State.CONTRACTION, trend, lo, hi)
            s.cons_index = i - c['min_bars'] + 1
            s.evidence = [f'tight base {pct:.1f}%']
            self.setup = s
            
        if s.state == State.CONTRACTION:
            if i - s.cons_index + 1 > c['max_bars']:
                s.state = State.FAILED
                s.failure_reason = 'consolidation too long'
                return s
                
            curr_r = df.iloc[i]
            rng = max(float(curr_r.High - curr_r.Low), 1e-12)
            body = abs(float(curr_r.Close - curr_r.Open))
            body_ratio = body / rng if rng else 0
            loc_bull = (curr_r.Close - curr_r.Low) / rng if rng else 0
            loc_bear = (curr_r.High - curr_r.Close) / rng if rng else 0
            rel_vol = float(curr_r.RelVol) if pd.notna(curr_r.RelVol) else 1.0
            
            # Breakout logic
            if trend == 1:
                level = s.resistance
                breakout = float(curr_r.Close) > level * (1 + m['buffer_pct']/100)
                strong_close = loc_bull >= m['close_location_min']
            else:
                level = s.support
                breakout = float(curr_r.Close) < level * (1 - m['buffer_pct']/100)
                strong_close = loc_bear >= m['close_location_min']
                
            vol_ok = rel_vol >= m['min_relative_volume']
            body_ok = body_ratio >= m['min_body_ratio']
            
            if breakout and strong_close and body_ok and vol_ok:
                s.state = State.BUY if trend == 1 else State.SELL
                s.entry = float(curr_r.Close)
                s.breakout_index = i
                
                stop_buffer = self.cfg['risk']['stop_loss_buffer_pct'] / 100
                
                # Stop loss placed safely below the breakout box
                if trend == 1:
                    s.stop = s.support * (1 - stop_buffer)
                else:
                    s.stop = s.resistance * (1 + stop_buffer)
                    
                risk = abs(s.entry - s.stop)
                rr = self.cfg['risk']['take_profit_r']
                s.target = s.entry + rr * risk if trend == 1 else s.entry - rr * risk
                
                s.evidence.extend([
                    f'breakout vol {rel_vol:.1f}x',
                    'strong close',
                    'momentum ignition'
                ])
                
        return s
