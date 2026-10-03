import os
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.models import State

def scan_now():
    print("Loading config...")
    cfg = load_config('config/default.yaml')
    provider = YFinanceProvider()
    
    filename = 'config/crypto_watchlist.txt'
    symbols = []
    
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            for line in f:
                sym = line.split(',')[0].strip()
                if sym and not sym.startswith('#') and sym not in symbols:
                    symbols.append(sym)
    else:
        print(f"File {filename} not found.")
        return

    print(f"Scanning {len(symbols)} symbols from {filename}...")
    
    for sym in symbols:
        try:
            df = provider.candles(sym, period='1y', interval='1d')
            if df.empty or len(df) < 80:
                continue
                
            engine = BreakoutEngine(cfg, sym)
            setup = engine.run(df)
            
            if setup.state in (State.BUY, State.SELL):
                print(f"🚀 SIGNAL: {sym} -> {setup.state.name} | Entry: ${setup.entry:.2f}")
            elif setup.state == State.CONTRACTION:
                print(f"👀 WATCHING: {sym} is forming a tight VCP base.")
            else:
                pass
                
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    print("Scan complete.")

if __name__ == "__main__":
    scan_now()
