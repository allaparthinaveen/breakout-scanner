import time
from typing import Dict, List
from .engine import BreakoutEngine
from .prediction_chat import explain
from .models import Setup, State

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
                    if setup.state == State.CONTRACTION:
                        self.enrich_bias(setup, df, period, interval)
                        
                    out.append(setup)
            except Exception as e:
                print(f"Error scanning {symbol}: {e}")
        return out

    def enrich_bias(self, setup: Setup, df, period, interval):
        score = 0
        if setup.trend == 1:
            score += 1
            
        try:
            import yfinance as yf
            import datetime
            
            is_crypto = setup.symbol.endswith('-USD') or setup.symbol.endswith('-USDT')
            is_nse = setup.symbol.endswith('.NS') or setup.symbol.endswith('.BO')
            
            if is_crypto:
                proxy = 'BTC-USD'
            elif is_nse:
                proxy = '^NSEI'
            else:
                proxy = 'SPY'
                
            cache_key = f"{proxy}_{period}_{interval}"
            if not hasattr(self.provider, 'market_cache'):
                self.provider.market_cache = {}
            
            if cache_key not in self.provider.market_cache:
                self.provider.market_cache[cache_key] = self.provider.candles(proxy, period, interval)
                
            proxy_df = self.provider.market_cache[cache_key]
            
            # Point 2: Relative Strength against asset class benchmark
            if not proxy_df.empty and len(df) > 20 and len(proxy_df) > 20:
                stock_returns = df['Close'].iloc[-20:] / df['Close'].iloc[-20]
                proxy_returns = proxy_df['Close'].reindex(stock_returns.index).ffill()
                if not proxy_returns.empty and proxy_returns.iloc[0] > 0:
                    proxy_returns = proxy_returns / proxy_returns.iloc[0]
                    if stock_returns.iloc[-1] > proxy_returns.iloc[-1]:
                        score += 1

            # Point 3: Institutional Consensus / Macro Regime
            if is_crypto:
                # Crypto: Bitcoin Macro Regime (BTC-USD trading above 50 SMA)
                if not proxy_df.empty and len(proxy_df) >= 50:
                    btc_sma50 = proxy_df['Close'].rolling(50).mean()
                    if proxy_df['Close'].iloc[-1] > btc_sma50.iloc[-1]:
                        score += 1
            elif is_nse:
                # NSE: Nifty 50 Trend Regime or Analyst Consensus if available
                nifty_bullish = False
                if not proxy_df.empty and len(proxy_df) >= 50:
                    nifty_sma50 = proxy_df['Close'].rolling(50).mean()
                    nifty_bullish = proxy_df['Close'].iloc[-1] > nifty_sma50.iloc[-1]
                
                has_analyst_buy = False
                try:
                    ticker = yf.Ticker(setup.symbol)
                    info = ticker.info
                    rec = info.get('recommendationKey', '')
                    if rec in ['buy', 'strong_buy', 'outperform']:
                        has_analyst_buy = True
                except Exception:
                    pass
                    
                if has_analyst_buy or nifty_bullish:
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

    def report(self, setups: List[Setup]):
        print("\n=======================================================")
        print(" SECTION 1: ◉ VCP BASE (Actively Ready To Provide Signal)")
        print("=======================================================")
        
        base_setups = [s for s in setups if s.state == State.CONTRACTION and s.bias_score >= 2]
        base_setups.sort(key=lambda s: s.bias_score, reverse=True)
        if not base_setups:
            print("  (None)")
        else:
            for s in base_setups:
                res = f"\033[91mResistance: {s.resistance:.5g}\033[0m"
                sup = f"\033[92mSupport: {s.support:.5g}\033[0m"
                width = f"Width: {s.base_width_pct:.1f}%" if s.base_width_pct is not None else ""
                
                if s.bias_score >= 2:
                    bias_text = f"Strong Upside Bias ({s.bias_score}/3)"
                    bias_color = "\033[92m"
                elif s.bias_score == 1:
                    bias_text = f"Weak Upside Bias ({s.bias_score}/3)"
                    bias_color = "\033[93m"
                else:
                    bias_text = f"Downside Bias ({s.bias_score}/3)"
                    bias_color = "\033[91m"
                    
                if s.earnings_warning:
                    bias_text += " ⚠️ EARNINGS RISK"
                    
                bias_str = f"{bias_color}Bias: {bias_text}\033[0m"
                parts = [f"  • {s.symbol:9}", res, sup]
                if width:
                    parts.append(width)
                parts.append(bias_str)
                print(" | ".join(parts))

        print("\n=======================================================")
        print(" SECTION 2: 🚀 ACTIVE SIGNALS (Trade In Progress)")
        print("=======================================================")
        
        active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]
        active_setups.sort(key=lambda s: abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf'))
        if not active_setups:
            print("  (None)")
        else:
            for s in active_setups:
                dir_str = "\033[92m▲ BUY\033[0m" if s.direction == 1 else "\033[91m▼ SELL\033[0m"
                cp_str = f"CP: {s.current_price:.5g}" if s.current_price else "CP: N/A"
                entry = f"Entry: {s.entry:.5g}"
                stop = f"Stop: {s.stop:.5g}"
                targets = f"TP1: {s.tp1:.5g} | TP2: {s.tp2:.5g}"
                risk_str = f"Risk: {s.risk_pct:.2f}%" if s.risk_pct is not None else ""
                
                pnl_str = ""
                if s.pnl_pct is not None:
                    pnl_color = "\033[92m" if s.pnl_pct >= 0 else "\033[91m"
                    pnl_sign = "+" if s.pnl_pct >= 0 else ""
                    pnl_str = f"{pnl_color}P&L: {pnl_sign}{s.pnl_pct:.2f}%\033[0m"
                    
                parts = [f"  • {s.symbol:9}", dir_str, cp_str, entry, stop]
                if risk_str:
                    parts.append(risk_str)
                if pnl_str:
                    parts.append(pnl_str)
                parts.append(targets)
                print(" | ".join(parts))
                
        print("\n")

