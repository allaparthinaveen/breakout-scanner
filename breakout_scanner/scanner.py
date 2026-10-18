import time
from typing import Dict, List
import pandas as pd
import numpy as np
from .engine import BreakoutEngine
from .prediction_chat import explain
from .models import Setup, State
from .sectors import get_sector_benchmark
from .scorer import calculate_composite_score

class MultiSymbolScanner:
    def __init__(self, provider, cfg):
        self.provider = provider
        self.cfg = cfg
        self.engines: Dict[str, BreakoutEngine] = {}

    def get_engine(self, symbol: str) -> BreakoutEngine:
        if symbol not in self.engines:
            self.engines[symbol] = BreakoutEngine(self.cfg, symbol)
        return self.engines[symbol]

    def scan(self, symbols: List[str], period='60d', interval='1h') -> List[Setup]:
        """
        Poll-based scan that maintains state. In a real system, this could be 
        triggered periodically (e.g., every minute) as a fallback to websockets.
        """
        out = []
        for symbol in symbols:
            try:
                df = self.provider.candles(symbol, period, interval)
                if not df.empty:
                    engine = self.get_engine(symbol)
                    setup = engine.run(df)
                    if setup.state in (State.CONTRACTION, State.BUY, State.SELL):
                        self.enrich_bias(setup, df, period, interval)
                        
                    out.append(setup)
            except Exception as e:
                print(f"Error scanning {symbol}: {e}")
        return out

    def enrich_bias(self, setup: Setup, df, period, interval):
        score = 0
        target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.trend >= 0 else -1)
        
        # Pillar 1: 1H Trend Alignment
        if target_dir == 1 and setup.trend == 1:
            score += 1
        elif target_dir == -1 and setup.trend == -1:
            score += 1
            
        try:
            import yfinance as yf
            import datetime
            
            # ----------------------------------------------------
            # 1. Higher-Timeframe (1D) Confluence Check & Cache TTL
            # ----------------------------------------------------
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if not hasattr(self.provider, 'daily_cache'):
                self.provider.daily_cache = {}
            if not hasattr(self.provider, 'daily_cache_ts'):
                self.provider.daily_cache_ts = {}

            def get_cached_daily(sym: str) -> pd.DataFrame:
                cached_df = self.provider.daily_cache.get(sym)
                cached_ts = self.provider.daily_cache_ts.get(sym)
                is_stale = False
                if cached_df is None or cached_ts is None:
                    is_stale = True
                else:
                    elapsed = (now_utc - cached_ts).total_seconds()
                    # Invalidate if older than 4 hours or if calendar day/session has advanced
                    if elapsed > 4 * 3600 or now_utc.date() != cached_ts.date():
                        is_stale = True
                if is_stale:
                    fresh_df = self.provider.candles(sym, period='1y', interval='1d')
                    self.provider.daily_cache[sym] = fresh_df
                    self.provider.daily_cache_ts[sym] = now_utc
                    return fresh_df
                return cached_df
                
            daily_df = get_cached_daily(setup.symbol)
            if not daily_df.empty and len(daily_df) >= 55:
                daily_sma = daily_df['Close'].rolling(50).mean()
                sma50_curr = daily_sma.iloc[-1]
                sma50_prev5 = daily_sma.iloc[-5]
                daily_close = daily_df['Close'].iloc[-1]
                
                # SMA50 slope check: SMA50[-1] > SMA50[-5]
                slope_up = sma50_curr > sma50_prev5
                slope_down = sma50_curr < sma50_prev5
                
                if daily_close > sma50_curr and slope_up:
                    setup.htf_trend = 1
                elif daily_close < sma50_curr and slope_down:
                    setup.htf_trend = -1
                else:
                    setup.htf_trend = 0
                
                # Check HTF alignment with current setup direction
                if target_dir == 1 and setup.htf_trend == 1:
                    setup.htf_confirmed = True
                elif target_dir == -1 and setup.htf_trend == -1:
                    setup.htf_confirmed = True
                else:
                    setup.htf_confirmed = False

            # ----------------------------------------------------
            # 2. Sector-Level Relative Strength (Forward-Only Alignment)
            # ----------------------------------------------------
            sector_name, benchmark_symbol = get_sector_benchmark(setup.symbol)
            setup.sector_name = sector_name
            proxy = benchmark_symbol
                
            cache_key = f"{proxy}_{period}_{interval}"
            if not hasattr(self.provider, 'market_cache'):
                self.provider.market_cache = {}
            if not hasattr(self.provider, 'market_cache_ts'):
                self.provider.market_cache_ts = {}

            def get_cached_proxy(key: str, p_sym: str, p_per: str, p_int: str) -> pd.DataFrame:
                cached_df = self.provider.market_cache.get(key)
                cached_ts = self.provider.market_cache_ts.get(key)
                is_stale = False
                if cached_df is None or cached_ts is None:
                    is_stale = True
                else:
                    elapsed = (now_utc - cached_ts).total_seconds()
                    if elapsed > 4 * 3600 or now_utc.date() != cached_ts.date():
                        is_stale = True
                if is_stale:
                    fresh_df = self.provider.candles(p_sym, period=p_per, interval=p_int)
                    self.provider.market_cache[key] = fresh_df
                    self.provider.market_cache_ts[key] = now_utc
                    return fresh_df
                return cached_df
            
            proxy_df = get_cached_proxy(cache_key, proxy, period, interval)
            
            # Relative Strength without backward-filling future data
            if not proxy_df.empty and len(df) > 20 and len(proxy_df) > 20:
                stock_tail = df['Close'].iloc[-20:]
                proxy_aligned = proxy_df['Close'].reindex(stock_tail.index).ffill()
                
                # Drop leading NaNs if proxy history started after stock_tail (NO bfill!)
                valid_idx = proxy_aligned.dropna().index
                if len(valid_idx) >= 10:
                    proxy_aligned = proxy_aligned.loc[valid_idx]
                    stock_aligned = stock_tail.loc[valid_idx]
                    
                    if proxy_aligned.iloc[0] > 0 and stock_aligned.iloc[0] > 0:
                        stock_returns = stock_aligned / stock_aligned.iloc[0]
                        proxy_returns = proxy_aligned / proxy_aligned.iloc[0]
                        
                        rs_delta = (stock_returns.iloc[-1] - proxy_returns.iloc[-1]) * 100.0
                        setup.sector_rs_pct = round(rs_delta, 2)
                        
                        # Symmetric RS evaluation
                        if target_dir == 1 and stock_returns.iloc[-1] > proxy_returns.iloc[-1]:
                            score += 1
                        elif target_dir == -1 and stock_returns.iloc[-1] < proxy_returns.iloc[-1]:
                            score += 1

                        # Step 3: RS Momentum Slope (Linear regression slope over trailing bars)
                        rs_ratio = (stock_returns / proxy_returns.replace(0, np.nan)).dropna()
                        if len(rs_ratio) >= 3:
                            tail = rs_ratio.iloc[-min(len(rs_ratio), 5):]
                            x = np.arange(len(tail))
                            slope, _ = np.polyfit(x, tail.values, 1)
                            setup.rs_slope = round(float(slope * 100.0), 2)
                            setup.rs_trend = "↗" if setup.rs_slope > 0.05 else ("↘" if setup.rs_slope < -0.05 else "→")

            # ----------------------------------------------------
            # 3. Institutional Consensus / Macro Regime
            # ----------------------------------------------------
            is_crypto = setup.symbol.endswith('-USD') or setup.symbol.endswith('-USDT')
            is_nse = setup.symbol.endswith('.NS') or setup.symbol.endswith('.BO')

            if is_crypto:
                # Crypto: Bitcoin Macro Regime (Daily BTC-USD trading above upward-sloping 50-day SMA)
                btc_daily = get_cached_daily('BTC-USD')
                btc_bullish = False
                if btc_daily is not None and not btc_daily.empty and len(btc_daily) >= 55:
                    btc_sma = btc_daily['Close'].rolling(50).mean()
                    btc_bullish = (btc_daily['Close'].iloc[-1] > btc_sma.iloc[-1]) and (btc_sma.iloc[-1] > btc_sma.iloc[-5])
                elif not proxy_df.empty and len(proxy_df) >= 55:
                    btc_sma = proxy_df['Close'].rolling(50).mean()
                    btc_bullish = (proxy_df['Close'].iloc[-1] > btc_sma.iloc[-1]) and (btc_sma.iloc[-1] > btc_sma.iloc[-5])
                
                if target_dir == 1 and btc_bullish:
                    score += 1
                elif target_dir == -1 and not btc_bullish:
                    score += 1

            elif is_nse:
                # NSE: Nifty 50 Trend Regime or Analyst Consensus if available
                nifty_bullish = False
                nifty_daily = get_cached_daily('^NSEI')
                if nifty_daily is not None and not nifty_daily.empty and len(nifty_daily) >= 55:
                    nifty_sma = nifty_daily['Close'].rolling(50).mean()
                    nifty_bullish = (nifty_daily['Close'].iloc[-1] > nifty_sma.iloc[-1]) and (nifty_sma.iloc[-1] > nifty_sma.iloc[-5])
                elif not proxy_df.empty and len(proxy_df) >= 55:
                    nifty_sma = proxy_df['Close'].rolling(50).mean()
                    nifty_bullish = (proxy_df['Close'].iloc[-1] > nifty_sma.iloc[-1]) and (nifty_sma.iloc[-1] > nifty_sma.iloc[-5])
                
                has_analyst_buy = False
                try:
                    ticker = yf.Ticker(setup.symbol)
                    info = ticker.info
                    rec = info.get('recommendationKey', '')
                    if rec in ['buy', 'strong_buy', 'outperform']:
                        has_analyst_buy = True
                except Exception:
                    pass
                    
                if target_dir == 1 and (has_analyst_buy or nifty_bullish):
                    score += 1
                elif target_dir == -1 and not nifty_bullish:
                    score += 1
            else:
                # US Stocks: Wall Street Analyst Ratings & Earnings Risk
                try:
                    ticker = yf.Ticker(setup.symbol)
                    info = ticker.info
                    rec = info.get('recommendationKey', '')
                    if rec in ['buy', 'strong_buy', 'outperform']:
                        score += 1
                        
                    cal = ticker.calendar
                    if isinstance(cal, dict) and 'Earnings Date' in cal:
                        dates = cal['Earnings Date']
                        if dates and isinstance(dates, list) and len(dates) > 0:
                            earning_date = dates[0]
                            days_until = (earning_date - datetime.date.today()).days
                            if 0 <= days_until <= 3:
                                setup.earnings_warning = True
                    elif hasattr(cal, 'get'):
                        earnings_val = cal.get('Earnings Date')
                        if earnings_val is not None and len(earnings_val) > 0:
                            earning_date = earnings_val[0] if isinstance(earnings_val, (list, pd.Series)) else earnings_val
                            if hasattr(earning_date, 'date'):
                                earning_date = earning_date.date()
                            days_until = (earning_date - datetime.date.today()).days
                            if 0 <= days_until <= 3:
                                setup.earnings_warning = True
                except Exception:
                    pass
        except Exception:
            pass
            
        setup.bias_score = score
        c_score, c_grade = calculate_composite_score(setup)
        setup.composite_score = c_score
        setup.composite_grade = c_grade

    def report(self, setups: List[Setup]):
        print("\n=======================================================")
        print(" SECTION 1: ◉ VCP BASE (Actively Ready To Provide Signal)")
        print("=======================================================")
        
        base_setups = [s for s in setups if s.state == State.CONTRACTION and s.bias_score >= 3]
        base_setups.sort(key=lambda s: (s.composite_score, s.bias_score), reverse=True)
        if not base_setups:
            print("  (None)")
        else:
            for s in base_setups:
                res = f"\033[91mResistance: {s.resistance:.5g}\033[0m"
                sup = f"\033[92mSupport: {s.support:.5g}\033[0m"
                width = f"Width: {s.base_width_pct:.1f}%" if s.base_width_pct is not None else ""
                coil = f"\033[94m🗜️ Coil: {s.decay_ratio:.1f}x\033[0m" if (s.decay_ratio is not None and s.tightening_confirmed) else ""
                vdu = f"\033[96m💧 VDU: {s.vdu_ratio:.1f}x\033[0m" if s.vdu_confirmed else ""
                trend_sym = f" {s.rs_trend}" if s.rs_trend else ""
                sector = f"[{s.sector_name} RS: {s.sector_rs_pct:+.1f}%{trend_sym}]" if s.sector_name and s.sector_rs_pct is not None else (f"[{s.sector_name}]" if s.sector_name else "")
                htf = "\033[93m⭐ 1D Confirmed\033[0m" if s.htf_confirmed else ""
                grade_badge = f"\033[93m🏆 Grade: {s.composite_grade} [{s.composite_score}]\033[0m" if s.composite_grade else ""
                
                target_dir = s.direction if s.direction in (1, -1) else (1 if s.trend >= 0 else -1)
                bias_label = "Upside" if target_dir == 1 else "Downside"
                if s.bias_score >= 2:
                    bias_text = f"Strong {bias_label} Bias ({s.bias_score}/3)"
                    bias_color = "\033[92m" if target_dir == 1 else "\033[91m"
                elif s.bias_score == 1:
                    bias_text = f"Weak {bias_label} Bias ({s.bias_score}/3)"
                    bias_color = "\033[93m"
                else:
                    bias_text = f"Neutral/Counter Bias ({s.bias_score}/3)"
                    bias_color = "\033[90m"
                    
                if s.earnings_warning:
                    bias_text += " ⚠️ EARNINGS RISK"
                    
                bias_str = f"{bias_color}Bias: {bias_text}\033[0m"
                parts = [f"  • {s.symbol:9}", res, sup]
                if width:
                    parts.append(width)
                if coil:
                    parts.append(coil)
                if vdu:
                    parts.append(vdu)
                if sector:
                    parts.append(sector)
                if htf:
                    parts.append(htf)
                if grade_badge:
                    parts.append(grade_badge)
                parts.append(bias_str)
                print(" | ".join(parts))

        active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]
        fresh_setups = [s for s in active_setups if s.is_fresh_entry]
        progress_setups = [s for s in active_setups if not s.is_fresh_entry]

        fresh_setups.sort(key=lambda s: (s.composite_score, -abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf')), reverse=True)
        progress_setups.sort(key=lambda s: (s.composite_score, -abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf')), reverse=True)

        print("\n=======================================================")
        print(" SECTION 2A: 🟢 FRESH ACTIONABLE ENTRIES (Playable Now)")
        print("=======================================================")
        if not fresh_setups:
            print("  (None)")
        else:
            for s in fresh_setups:
                print(self.format_active_setup(s, is_fresh=True))

        print("\n=======================================================")
        print(" SECTION 2B: 🔒 TRADES IN PROGRESS (Holders Only — Do Not Chase)")
        print("=======================================================")
        if not progress_setups:
            print("  (None)")
        else:
            for s in progress_setups:
                print(self.format_active_setup(s, is_fresh=False))
                
        print("\n")

    def format_active_setup(self, s: Setup, is_fresh: bool) -> str:
        tag = "🔄 RETEST" if s.retest_confirmed else "🚀 BREAKOUT"
        dir_color = "\033[92m" if s.direction == 1 else "\033[91m"
        dir_arrow = "▲ BUY" if s.direction == 1 else "▼ SELL"
        dir_str = f"{dir_color}{dir_arrow} [{tag}]\033[0m"
        
        cp_str = f"CP: {s.current_price:.5g}" if s.current_price is not None else "CP: N/A"
        entry = f"Entry: {s.entry:.5g}" if s.entry is not None else "Entry: N/A"
        stop = f"Stop: {s.stop:.5g}" if s.stop is not None else "Stop: N/A"
        targets = f"TP1: {s.tp1:.5g} | TP2: {s.tp2:.5g}" if (s.tp1 is not None and s.tp2 is not None) else ""
        risk_str = f"Risk: {s.risk_pct:.2f}%" if s.risk_pct is not None else ""
        rvol_str = f"[Vol: {s.rvol:.1f}x]" if s.rvol is not None else ""
        
        pnl_str = ""
        pnl_sign = ""
        if s.pnl_pct is not None:
            pnl_color = "\033[92m" if s.pnl_pct >= 0 else "\033[91m"
            pnl_sign = "+" if s.pnl_pct >= 0 else ""
            pnl_str = f"{pnl_color}P&L: {pnl_sign}{s.pnl_pct:.2f}%\033[0m"
            
        trend_sym = f" {s.rs_trend}" if s.rs_trend else ""
        sector = f"[{s.sector_name}{trend_sym}]" if s.sector_name else ""
        htf = "\033[93m⭐ 1D\033[0m" if s.htf_confirmed else ""
        grade_badge = f"\033[93m🏆 {s.composite_grade} [{s.composite_score}]\033[0m" if s.composite_grade else ""
        
        parts = [f"  • {s.symbol:9}", dir_str, cp_str, entry, stop]
        if risk_str:
            parts.append(risk_str)
        if pnl_str:
            parts.append(pnl_str)
        if rvol_str:
            parts.append(rvol_str)
        if sector:
            parts.append(sector)
        if htf:
            parts.append(htf)
        if grade_badge:
            parts.append(grade_badge)
            
        if not is_fresh:
            pnl_val = f"{pnl_sign}{s.pnl_pct:.2f}%" if s.pnl_pct is not None else ""
            if s.is_tp1_reached:
                parts.append(f"\033[91m[TP1 HIT: {pnl_val} - DO NOT ENTER]\033[0m")
            else:
                parts.append(f"\033[93m[EXTENDED: {pnl_val} - DO NOT CHASE]\033[0m")
        else:
            parts.append("\033[92m[ENTER NOW]\033[0m")
            
        if targets:
            parts.append(targets)
        return " | ".join(parts)


