import pandas as pd
from .models import Setup,State
from .indicators import atr,trend_from_structure

class BreakoutEngine:
    def __init__(self,cfg,symbol): self.cfg=cfg; self.symbol=symbol; self.setup=Setup(symbol)

    def _score(self,df,i,level,direction):
        b=self.cfg['breakout']; r=df.iloc[i]; rng=max(float(r.High-r.Low),1e-12); body=abs(float(r.Close-r.Open))
        body_ok=body/rng>=b['min_body_ratio']
        close_loc=(r.Close-r.Low)/rng if direction==1 else (r.High-r.Close)/rng
        loc_ok=close_loc>=b['close_location_min']
        ma=df.Volume.iloc[max(0,i-b['volume_lookback']+1):i+1].mean(); vol_ratio=float(r.Volume/ma) if ma and ma>0 else 1
        vol_ok=vol_ratio>=b['min_volume_ratio']
        mom=df.Close.iloc[max(0,i-b['momentum_lookback']):i+1]
        mom_ok=float(r.Close)>float(mom.iloc[0]) if direction==1 else float(r.Close)<float(mom.iloc[0])
        side_ok=float(r.Close)>level*(1+b['buffer_pct']/100) if direction==1 else float(r.Close)<level*(1-b['buffer_pct']/100)
        score=sum([side_ok,body_ok,loc_ok,vol_ok,mom_ok]); ev=[]
        for ok,name in [(side_ok,'close beyond level'),(body_ok,'healthy body'),(loc_ok,'strong close'),(vol_ok,f'volume {vol_ratio:.2f}x'),(mom_ok,'momentum')]:
            if ok: ev.append(name)
        return score,ev

    def _reset_smc(self, s):
        s.long_sweep=False; s.short_sweep=False
        s.long_reaction=False; s.short_reaction=False
        s.long_sweep_index=None; s.short_sweep_index=None
        s.long_liquidity_level=None; s.short_liquidity_level=None

    def run(self,raw):
        df=raw.copy()
        if len(df)<80:return self.setup
        df['_ATR']=atr(df)
        c,b,a,rt,lq=[self.cfg[x] for x in ('consolidation','breakout','acceptance','retest','liquidity')]
        i=len(df)-1; trend=trend_from_structure(df,self.cfg['structure']['pivot_len'],self.cfg['structure']['min_trend_swings']); self.setup.trend=trend
        hi=float(df.High.iloc[i-c['min_bars']+1:i+1].max()); lo=float(df.Low.iloc[i-c['min_bars']+1:i+1].min()); mid=(hi+lo)/2; pct=(hi-lo)/mid*100 if mid else 999
        s=self.setup
        
        if s.state in (State.IDLE,State.FAILED) and trend and pct<=c['max_range_pct']:
            s=Setup(self.symbol,State.CONSOLIDATION,trend,lo,hi); s.cons_index=i-c['min_bars']+1; self.setup=s
            self._reset_smc(s)
            
        if s.state==State.CONSOLIDATION:
            if i-s.cons_index+1 > c['max_bars']:
                s.state=State.FAILED; s.failure_reason='consolidation too long'; self._reset_smc(s); return s
            
            level=s.resistance if trend==1 else s.support; d=1 if trend==1 else -1
            
            # Liquidity Mapping & SMC
            curr_r = df.iloc[i]
            rng = max(float(curr_r.High - curr_r.Low), 1e-12)
            body = abs(float(curr_r.Close - curr_r.Open))
            body_ratio = body / rng
            loc_bull = (curr_r.Close - curr_r.Low) / rng
            loc_bear = (curr_r.High - curr_r.Close) / rng
            ma=df.Volume.iloc[max(0,i-b['volume_lookback']+1):i+1].mean()
            vol_ratio=float(curr_r.Volume/ma) if ma and ma>0 else 1
            
            sell_liq = s.support
            buy_liq = s.resistance
            
            # Sweep Detection
            bullish_sweep = curr_r.Low < sell_liq * (1 - lq['sweep_buffer_pct']/100) and curr_r.Close > sell_liq
            bearish_sweep = curr_r.High > buy_liq * (1 + lq['sweep_buffer_pct']/100) and curr_r.Close < buy_liq
            
            if trend == 1 and bullish_sweep:
                s.long_sweep = True; s.long_reaction = False; s.long_sweep_index = i; s.long_liquidity_level = sell_liq
            if trend == -1 and bearish_sweep:
                s.short_sweep = True; s.short_reaction = False; s.short_sweep_index = i; s.short_liquidity_level = buy_liq
                
            # Reaction Detection
            if s.long_sweep and not s.long_reaction and s.long_sweep_index is not None:
                if i - s.long_sweep_index <= lq['reaction_bars']:
                    if curr_r.Close > curr_r.Open and body_ratio >= lq['reaction_body_ratio'] and loc_bull >= lq['reaction_close_loc'] and vol_ratio >= lq['sweep_vol_ratio']:
                        s.long_reaction = True
                else:
                    s.long_sweep = False; s.long_reaction = False
                    
            if s.short_sweep and not s.short_reaction and s.short_sweep_index is not None:
                if i - s.short_sweep_index <= lq['reaction_bars']:
                    if curr_r.Close < curr_r.Open and body_ratio >= lq['reaction_body_ratio'] and loc_bear >= lq['reaction_close_loc'] and vol_ratio >= lq['sweep_vol_ratio']:
                        s.short_reaction = True
                else:
                    s.short_sweep = False; s.short_reaction = False

            # Breakout Scoring
            score,ev=self._score(df,i,level,d)
            
            # Liquidity Gate
            liq_ok = (lq['mode'] == "Off") or (lq['mode'] == "Optional Confirmation") or (d == 1 and s.long_reaction) or (d == -1 and s.short_reaction)
            
            if score>=b['min_score'] and liq_ok:
                s.state=State.ACCEPTANCE_WATCH; s.direction=d; s.breakout_level=level; s.breakout_score=score; s.breakout_index=i; s.acceptance_count=0; s.evidence=ev
            return s
            
        if s.state==State.ACCEPTANCE_WATCH:
            level=s.breakout_level; d=s.direction; close=float(df.Close.iloc[i]); beyond=close>level if d==1 else close<level
            back=((level-close)/level*100) if d==1 else ((close-level)/level*100)
            if (not beyond) or back>a['max_back_inside_pct'] or i-s.breakout_index>a['max_wait_bars']:
                s.state=State.FAILED; s.failure_reason='acceptance failed'; self._reset_smc(s); return s
            s.acceptance_count+=1
            if s.acceptance_count>=a['hold_bars']:
                s.state=State.ACCEPTED_RETEST_WATCH; s.retest_index=i; s.evidence.append('level accepted')
            return s
            
        if s.state==State.ACCEPTED_RETEST_WATCH:
            level=s.breakout_level; d=s.direction; close=float(df.Close.iloc[i]); low=float(df.Low.iloc[i]); high=float(df.High.iloc[i]); age=i-s.retest_index
            touched=low<=level*(1+rt['buffer_pct']/100) if d==1 else high>=level*(1-rt['buffer_pct']/100)
            held=close>level if d==1 else close<level
            if touched and held:
                s.state=State.BUY if d==1 else State.SELL; s.entry=close
                av=float(df._ATR.iloc[i]) if pd.notna(df._ATR.iloc[i]) else 0; buf=b['buffer_pct']/100
                s.stop=level*(1-buf) if d==1 else level*(1+buf)
                risk=abs(s.entry-s.stop); rr=self.cfg['risk']['reward_risk']; s.target=s.entry+rr*risk if d==1 else s.entry-rr*risk; s.evidence.append('retest held')
                self._reset_smc(s)
            elif age>rt['max_wait_bars']:
                s.state=State.FAILED; s.failure_reason='retest timeout'
            return s
            
        return s
