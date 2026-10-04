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
    
    from breakout_scanner.scanner import MultiSymbolScanner
    scanner = MultiSymbolScanner(provider, cfg)
    
    # Run scanner and grab results
    setups = scanner.scan(symbols, period='1y', interval='1d')
    
    # Report using the new beautiful format
    scanner.report(setups)
            
    print("Scan complete.")

if __name__ == "__main__":
    scan_now()
