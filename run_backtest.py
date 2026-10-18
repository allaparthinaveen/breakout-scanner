import os
import json
import argparse
from typing import List
import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.providers.yfinance_provider import YFinanceProvider

def load_watchlist(path: str) -> List[str]:
    """Load symbols from a watchlist file, ignoring comments and whitespace."""
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        symbols = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    return symbols

def run_backtest_cli():
    parser = argparse.ArgumentParser(description="Breakout Scanner Institutional Multi-Market Backtester")
    parser.add_argument('--market', type=str, choices=['nse', 'crypto', 'us'], default='nse',
                        help="Target market: nse, crypto, or us (default: nse)")
    parser.add_argument('--watchlist', type=str, default=None,
                        help="Path to custom watchlist file (overrides --market default)")
    parser.add_argument('--period', type=str, default='3y',
                        help="Data lookback period: 1y, 2y, 3y, 5y (default: 3y)")
    parser.add_argument('--interval', type=str, default='1d',
                        help="Candle timeframe: 1d, 1h, etc. (default: 1d)")
    parser.add_argument('--config', type=str, default='config/default.yaml',
                        help="Path to strategy config yaml (default: config/default.yaml)")
    parser.add_argument('--output-dir', type=str, default='backtest',
                        help="Output directory for backtest logs and metrics (default: backtest)")
    parser.add_argument('--max-symbols', type=int, default=None,
                        help="Optional limit on the number of symbols to test")
    
    args = parser.parse_args()
    
    # Resolve watchlist path based on market if not explicitly provided
    watchlist_path = args.watchlist
    if not watchlist_path:
        if args.market == 'nse':
            watchlist_path = 'config/nsetop10_watchlist.txt'
        elif args.market == 'crypto':
            watchlist_path = 'config/crypto_watchlist.txt'
        elif args.market == 'us':
            watchlist_path = 'config/top30_watchlist.txt'
        else:
            watchlist_path = 'config/nsetop10_watchlist.txt'
            
    print("=" * 70)
    print("  INSTITUTIONAL BREAKOUT BACKTEST ENGINE (ZERO LOOKAHEAD)")
    print("=" * 70)
    print(f"  • Market:          {args.market.upper()}")
    print(f"  • Watchlist:       {watchlist_path}")
    print(f"  • Timeframe:       {args.interval.upper()} (Period: {args.period})")
    print(f"  • Strategy Config: {args.config}")
    
    cfg = load_config(args.config)
    symbols = load_watchlist(watchlist_path)
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[:args.max_symbols]
        
    if not symbols:
        print(f"❌ Error: No symbols loaded from {watchlist_path}")
        return
        
    print(f"  • Loaded Symbols:  {len(symbols)} stocks/assets")
    c_risk = cfg.get('risk', {})
    print(f"  • Account Size:    ${c_risk.get('account_size', 100000.0):,.2f}")
    print(f"  • Risk Per Trade:  {c_risk.get('risk_per_trade_pct', 1.0)}%")
    print(f"  • Slippage:        {c_risk.get('slippage_pct', 0.05)}% per fill")
    print(f"  • Brokerage/Fees:  {c_risk.get('commission_pct', 0.05)}% per leg")
    print("=" * 70)
    
    provider = YFinanceProvider()
    os.makedirs(args.output_dir, exist_ok=True)
    
    all_trades = []
    symbol_metrics = {}
    
    print("\nStarting chronological bar-by-bar simulation...\n")
    
    for idx, sym in enumerate(symbols, 1):
        print(f"[{idx:02d}/{len(symbols):02d}] Fetching {sym:<14} ... ", end="", flush=True)
        try:
            df = provider.candles(sym, period=args.period, interval=args.interval)
            if df.empty or len(df) < 50:
                print("⚠️  Insufficient historical data (skipped)")
                continue
                
            tester = Backtester(cfg)
            metrics = tester.run(sym, df)
            symbol_metrics[sym] = metrics
            all_trades.extend(tester.results)
            
            # Save individual symbol equity curve
            if tester.equity_curve:
                eq_df = pd.DataFrame({'equity': tester.equity_curve})
                eq_df.to_csv(os.path.join(args.output_dir, f"{sym.replace('^', '').replace(':', '_')}_equity.csv"), index=False)
                
            n_tr = metrics['trades']
            wr = metrics['win_rate'] * 100.0
            ret = metrics['net_return_pct']
            pf = metrics['profit_factor']
            print(f"Done! {n_tr:>3} trades | Win Rate: {wr:>5.1f}% | Net Ret: {ret:>+6.2f}% | PF: {pf:>4.2f}")
            
        except Exception as e:
            print(f"❌ Failed: {e}")
            
    # Compute Aggregated Portfolio Performance
    print("\n" + "=" * 70)
    print("  AGGREGATE PORTFOLIO PERFORMANCE SUMMARY")
    print("=" * 70)
    
    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_csv_path = os.path.join(args.output_dir, "all_trades.csv")
        trades_df.to_csv(trades_csv_path, index=False)
        
        wins = [t for t in all_trades if t['net_pnl'] > 0]
        losses = [t for t in all_trades if t['net_pnl'] <= 0]
        tot_trades = len(all_trades)
        win_rate = (len(wins) / tot_trades * 100.0) if tot_trades > 0 else 0.0
        
        gross_profit = sum(t['net_pnl'] for t in wins)
        gross_loss = abs(sum(t['net_pnl'] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        
        avg_r = np.mean([t['realized_r'] for t in all_trades]) if tot_trades > 0 else 0.0
        tp1_hits = sum(1 for t in all_trades if t['tp1_hit'])
        tp1_rate = (tp1_hits / tot_trades * 100.0) if tot_trades > 0 else 0.0
        
        tot_net_pnl = sum(t['net_pnl'] for t in all_trades)
        tot_commissions = sum(t['commissions'] for t in all_trades)
        tot_gross_pnl = sum(t['gross_pnl'] for t in all_trades)
        
        # Portfolio drawdown across chronological sequence of all trades
        cum_pnl = np.cumsum([t['net_pnl'] for t in all_trades])
        initial_cap = float(c_risk.get('account_size', 100000.0))
        portfolio_curve = initial_cap + cum_pnl
        running_peak = np.maximum.accumulate(portfolio_curve)
        drawdowns = (portfolio_curve - running_peak) / running_peak * 100.0
        portfolio_max_dd = float(abs(np.min(drawdowns))) if len(drawdowns) > 0 else 0.0
        portfolio_net_return = (tot_net_pnl / initial_cap) * 100.0
        
        summary = {
            'market': args.market,
            'period': args.period,
            'interval': args.interval,
            'symbols_tested': len(symbol_metrics),
            'total_trades': tot_trades,
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'win_rate_pct': round(win_rate, 2),
            'profit_factor': round(profit_factor, 2),
            'average_r': round(float(avg_r), 2),
            'tp1_hit_rate_pct': round(tp1_rate, 2),
            'gross_pnl': round(tot_gross_pnl, 2),
            'total_commissions': round(tot_commissions, 2),
            'net_pnl': round(tot_net_pnl, 2),
            'portfolio_net_return_pct': round(portfolio_net_return, 2),
            'max_drawdown_pct': round(portfolio_max_dd, 2)
        }
        
        print(f"  • Total Completed Trades:   {tot_trades}")
        print(f"  • Win / Loss Distribution:  {len(wins)} Wins / {len(losses)} Losses")
        print(f"  • Realized Win Rate:        {win_rate:.2f}%")
        print(f"  • Profit Factor:            {profit_factor:.2f}")
        print(f"  • Average R-Multiple:       {avg_r:+.2f}R")
        print(f"  • TP1 (1.0R) Hit Rate:      {tp1_rate:.2f}%")
        print(f"  • Total Gross P&L:          ${tot_gross_pnl:>+12,.2f}")
        print(f"  • Total Frictions (Fees):   ${tot_commissions:>12,.2f}")
        print(f"  • Net Realized P&L:         ${tot_net_pnl:>+12,.2f}")
        print(f"  • Portfolio Net Return:     {portfolio_net_return:>+6.2f}%")
        print(f"  • Portfolio Max Drawdown:   {portfolio_max_dd:>6.2f}%")
        print("-" * 70)
        print(f"  📁 Trade Log Saved:         {trades_csv_path}")
        
        with open(os.path.join(args.output_dir, "portfolio_summary.json"), 'w') as f:
            json.dump(summary, f, indent=4)
    else:
        print("  ⚠️ No trades triggered across the selected watchlist and period.")
        
    with open(os.path.join(args.output_dir, "metrics.json"), 'w') as f:
        json.dump(symbol_metrics, f, indent=4)
        
    print(f"  📁 Metrics Saved:           {os.path.join(args.output_dir, 'metrics.json')}")
    print("=" * 70 + "\n")

if __name__ == '__main__':
    run_backtest_cli()
