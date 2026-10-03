import os
import json
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.providers.yfinance_provider import YFinanceProvider

def run_historical_backtest():
    print("Loading config...")
    cfg = load_config('config/default.yaml')
    
    # We will use yfinance for backtesting
    provider = YFinanceProvider()
    
    symbols = [
        'NVDA'
    ]
    
    tester = Backtester(cfg)
    
    all_trades = []
    global_metrics = {}
    
    print("Starting 5-Year Daily (1D) Sector Scan...")
    
    for sym in symbols:
        print(f"Fetching historical data for {sym}...")
        try:
            # Using 5y (5 years) on 1d timeframe
            df = provider.candles(sym, period='5y', interval='1d')
            if df.empty or len(df) < 100:
                print(f"Not enough data for {sym}, skipping.")
                continue
                
            print(f"Running engine for {sym} ({len(df)} bars)...")
            
            # Create an independent backtester per symbol to reset equity to base
            # Alternatively, we can use the same tester and it compounds. Let's do independent for clean stats.
            sym_tester = Backtester(cfg)
            metrics = sym_tester.run(sym, df)
            
            global_metrics[sym] = metrics
            all_trades.extend(sym_tester.results)
            
            # Save symbol equity curve
            equity_df = pd.DataFrame({'equity': sym_tester.equity_curve})
            equity_df.to_csv(f'backtest/{sym}_equity.csv', index=False)
            
        except Exception as e:
            print(f"Failed to backtest {sym}: {e}")
            
    # Save all trades
    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_df.to_csv('backtest/all_trades.csv', index=False)
        
    # Save metrics
    with open('backtest/metrics.json', 'w') as f:
        json.dump(global_metrics, f, indent=4)
        
    print("Backtest Complete! Results saved to /backtest directory.")
    print(json.dumps(global_metrics, indent=2))

if __name__ == '__main__':
    run_historical_backtest()
