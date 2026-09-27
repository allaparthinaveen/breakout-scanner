import argparse
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.models import State

def run_pre_market_scan(watchlist_file: str):
    print("🌅 Running Pre-Market Breakout Scanner...")
    
    cfg = load_config('config/default.yaml')
    provider = YFinanceProvider()
    
    with open(watchlist_file, 'r') as f:
        symbols = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
    print(f"Scanning {len(symbols)} symbols on the 1-hour timeframe...\n")
    
    results = []
    
    for sym in symbols:
        df = provider.candles(sym, period='60d', interval='1h')
        if df.empty or len(df) < 100:
            continue
            
        engine = BreakoutEngine(cfg, sym)
        setup = engine.run(df)
        
        results.append({
            'Symbol': sym,
            'State': setup.state.name,
            'Trend': "UP 🔼" if setup.trend == 1 else "DOWN 🔽" if setup.trend == -1 else "FLAT ➖",
            'Resistance': f"${setup.resistance:.2f}" if setup.resistance else "-",
            'Support': f"${setup.support:.2f}" if setup.support else "-"
        })
        
    # Group results to highlight actionable setups
    df_results = pd.DataFrame(results)
    
    forming_box = df_results[df_results['State'] == 'CONSOLIDATION']
    breaking_out = df_results[df_results['State'] == 'ACCEPTANCE_WATCH']
    idle = df_results[df_results['State'].isin(['IDLE', 'FAILED'])]
    
    print("=====================================================")
    print(" 📦 STOCKS FORMING A BOX (CONSOLIDATION)")
    print(" Focus on these today. Wait for them to break out.")
    print("=====================================================")
    if not forming_box.empty:
        print(forming_box[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False))
    else:
        print(" None currently forming a box.")
        
    print("\n=====================================================")
    print(" 💥 STOCKS ACTIVELY BREAKING OUT (ACCEPTANCE_WATCH)")
    print(" These smashed the box yesterday! Watch for a retest.")
    print("=====================================================")
    if not breaking_out.empty:
        print(breaking_out[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False))
    else:
        print(" None currently breaking out.")
        
    print("\n=====================================================")
    print(" 💤 IDLE / FAILED STOCKS")
    print(" Ignore these for today.")
    print("=====================================================")
    if not idle.empty:
        print(", ".join(idle['Symbol'].tolist()))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Pre-Market Scanner")
    parser.add_argument('--watchlist', type=str, default='config/watchlist.txt', help='Path to watchlist file')
    args = parser.parse_args()
    
    run_pre_market_scan(args.watchlist)
