"""
Institutional Breakout Scanner - Phase 2 Single-Variable 1H Experiment Runner.

Systematically evaluates each Phase 1 rule in isolation on 1H candle data across:
  - US Equities
  - Japan Equities
  - European Equities
  - Crypto Assets
"""

import os
import copy
import json
import argparse
from typing import List, Dict, Any
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

def get_market_watchlist(market: str) -> str:
    market = market.lower()
    if market == 'us':
        return 'config/sp_50_watchlist.txt'
    elif market in ('japan', 'jp'):
        return 'config/japan_top30_watchlist.txt'
    elif market in ('europe', 'eu'):
        return 'config/europe_top30_watchlist.txt'
    elif market == 'crypto':
        return 'config/crypto_watchlist.txt'
    elif market == 'nse':
        return 'config/nsetop10_watchlist.txt'
    else:
        return 'config/sp_50_watchlist.txt'

def fetch_and_cache_market_data(symbols: List[str], period: str = '730d', interval: str = '1h', cache_dir: str = 'backtest/cache') -> Dict[str, pd.DataFrame]:
    """Fetch 1H candles for all symbols once and cache them on disk / memory."""
    os.makedirs(cache_dir, exist_ok=True)
    provider = YFinanceProvider()
    data_store = {}
    
    print(f"\n[Data Cache] Fetching/verifying {len(symbols)} symbols ({period}, {interval})...")
    for idx, sym in enumerate(symbols, 1):
        clean_name = sym.replace('^', '').replace(':', '_').replace('/', '_')
        cache_path = os.path.join(cache_dir, f"{clean_name}_{period}_{interval}.pkl")
        
        if os.path.exists(cache_path):
            try:
                df = pd.read_pickle(cache_path)
                data_store[sym] = df
                print(f"  [{idx:02d}/{len(symbols):02d}] {sym:<14} -> Cached ({len(df)} bars)")
                continue
            except Exception:
                pass
                
        print(f"  [{idx:02d}/{len(symbols):02d}] {sym:<14} -> Downloading ... ", end="", flush=True)
        try:
            df = provider.candles(sym, period=period, interval=interval)
            if not df.empty and len(df) >= 50:
                df.to_pickle(cache_path)
                data_store[sym] = df
                print(f"Done ({len(df)} bars)")
            else:
                print("⚠️  Insufficient bars (skipped)")
        except Exception as e:
            print(f"❌ Failed ({e})")
            
    print(f"[Data Cache] Ready with {len(data_store)} valid symbols.\n")
    return data_store

def define_experiment_variants(base_cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Define the isolated single-variable configurations for testing."""
    variants = {}
    
    # Variant 0: Baseline (10d hold, all Phase 1 flags disabled)
    variants["0_Baseline_10d"] = copy.deepcopy(base_cfg)
    
    # Variant 1: Time Exit (1-2 Days Hold: max_hold_bars = 14)
    v1 = copy.deepcopy(base_cfg)
    v1['management']['max_hold_bars'] = 14
    v1['management']['max_hold_days'] = 2.0
    variants["1_TimeExit_2d_14b"] = v1
    
    # Variant 2: SMA50 Slope Filter
    v2 = copy.deepcopy(base_cfg)
    v2['trend']['use_slope_filter'] = True
    v2['trend']['slope_lookback_bars'] = 5
    variants["2_SMA50_Slope"] = v2
    
    # Variant 3: Hourly Volume Profile (Median of same hour)
    v3 = copy.deepcopy(base_cfg)
    v3['volume']['use_hourly_volume_profile'] = True
    v3['volume']['hourly_profile_days'] = 20
    variants["3_Hourly_Vol_Profile"] = v3
    
    # Variant 4: Next-Bar Open Confirmation
    v4 = copy.deepcopy(base_cfg)
    v4['breakout']['use_open_confirmation'] = True
    v4['breakout']['max_open_gap_pct'] = 0.50
    variants["4_Open_Confirmation"] = v4
    
    # Variant 5: Failed Breakout Exit
    v5 = copy.deepcopy(base_cfg)
    v5['management']['use_failed_breakout_exit'] = True
    v5['management']['failed_breakout_max_bars'] = 3
    variants["5_Failed_Breakout_Exit"] = v5
    
    # Variant 6: Cost / Friction Gate
    v6 = copy.deepcopy(base_cfg)
    v6['risk']['use_cost_filter'] = True
    v6['risk']['max_friction_r'] = 0.15
    variants["6_Cost_Friction_Gate"] = v6
    
    # Variant 7: Long-Only (Discarding shorts to evaluate structural drag)
    v7 = copy.deepcopy(base_cfg)
    v7['breakout']['allow_shorts'] = False
    variants["7_Long_Only"] = v7
    
    # Variant 8: All Positive Rules Combined (1H Optimized Model)
    v8 = copy.deepcopy(base_cfg)
    v8['management']['max_hold_bars'] = 14
    v8['management']['max_hold_days'] = 2.0
    v8['trend']['use_slope_filter'] = True
    v8['trend']['slope_lookback_bars'] = 5
    v8['volume']['use_hourly_volume_profile'] = True
    v8['volume']['hourly_profile_days'] = 20
    v8['breakout']['use_open_confirmation'] = True
    v8['breakout']['max_open_gap_pct'] = 0.50
    v8['management']['use_failed_breakout_exit'] = True
    v8['management']['failed_breakout_max_bars'] = 3
    v8['risk']['use_cost_filter'] = True
    v8['risk']['max_friction_r'] = 0.15
    variants["8_All_Rules_Combined"] = v8
    
    # Variant 9: Optimal Long Model (Long-Only + Cost Gate + Hourly Vol + Open Conf)
    v9 = copy.deepcopy(base_cfg)
    v9['breakout']['allow_shorts'] = False
    v9['risk']['use_cost_filter'] = True
    v9['risk']['max_friction_r'] = 0.15
    v9['volume']['use_hourly_volume_profile'] = True
    v9['volume']['hourly_profile_days'] = 20
    v9['breakout']['use_open_confirmation'] = True
    v9['breakout']['max_open_gap_pct'] = 0.50
    variants["9_Optimal_Long_Model"] = v9
    
    return variants

def evaluate_variant(name: str, cfg: Dict[str, Any], data_store: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """Run a variant across the cached dataset and compute comprehensive metrics."""
    all_trades = []
    
    for sym, df in data_store.items():
        try:
            tester = Backtester(cfg)
            tester.run(sym, df)
            all_trades.extend(tester.results)
        except Exception as e:
            print(f"\n[Warning: Error evaluating {sym} on {name}: {e}]")
            continue
            
    # Sort chronologically
    all_trades.sort(key=lambda t: str(t.get('exit_time') or t.get('entry_time') or ''))
    
    tot_trades = len(all_trades)
    if tot_trades == 0:
        return {
            'variant': name,
            'trades': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'avg_r': 0.0,
            'long_avg_r': 0.0,
            'short_avg_r': 0.0,
            'tp1_rate': 0.0,
            'max_drawdown': 0.0,
            'net_pnl': 0.0,
            'total_fees': 0.0,
            'friction_ratio_pct': 0.0,
            'stop_floor_pct': 0.0,
            'avg_mfe': 0.0,
            'avg_mae': 0.0,
            'fwd_ret_1b': 0.0,
            'fwd_ret_4b': 0.0,
            'fwd_ret_8b': 0.0,
            'exit_breakdown': {}
        }
        
    wins = [t for t in all_trades if t['net_pnl'] > 0]
    losses = [t for t in all_trades if t['net_pnl'] <= 0]
    win_rate = (len(wins) / tot_trades * 100.0)
    
    gross_profit = sum(t['net_pnl'] for t in wins)
    gross_loss = abs(sum(t['net_pnl'] for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    
    avg_r = float(np.mean([t['realized_r'] for t in all_trades]))
    long_trades = [t for t in all_trades if t['direction'] == 1]
    short_trades = [t for t in all_trades if t['direction'] == -1]
    long_avg_r = float(np.mean([t['realized_r'] for t in long_trades])) if long_trades else 0.0
    short_avg_r = float(np.mean([t['realized_r'] for t in short_trades])) if short_trades else 0.0
    
    tp1_hits = sum(1 for t in all_trades if t['tp1_hit'])
    tp1_rate = (tp1_hits / tot_trades * 100.0)
    
    tot_net_pnl = sum(t['net_pnl'] for t in all_trades)
    tot_fees = sum(t['commissions'] for t in all_trades)
    tot_gross = sum(t['gross_pnl'] for t in all_trades)
    friction_pct = (tot_fees / abs(tot_gross) * 100.0) if abs(tot_gross) > 0 else 0.0
    
    stop_floor_cnt = sum(1 for t in all_trades if t.get('stop_floor_binding', False))
    stop_floor_pct = (stop_floor_cnt / tot_trades * 100.0)
    
    mfe_vals = [t['mfe_r'] for t in all_trades if t.get('mfe_r') is not None]
    mae_vals = [t['mae_r'] for t in all_trades if t.get('mae_r') is not None]
    avg_mfe = float(np.mean(mfe_vals)) if mfe_vals else 0.0
    avg_mae = float(np.mean(mae_vals)) if mae_vals else 0.0
    
    f1 = [t['fwd_ret_1b'] for t in all_trades if t.get('fwd_ret_1b') is not None]
    f4 = [t['fwd_ret_4b'] for t in all_trades if t.get('fwd_ret_4b') is not None]
    f8 = [t['fwd_ret_8b'] for t in all_trades if t.get('fwd_ret_8b') is not None]
    
    avg_f1 = float(np.mean(f1)) if f1 else 0.0
    avg_f4 = float(np.mean(f4)) if f4 else 0.0
    avg_f8 = float(np.mean(f8)) if f8 else 0.0
    
    # Portfolio Max Drawdown
    cum_pnl = np.cumsum([t['net_pnl'] for t in all_trades])
    init_cap = float(cfg.get('risk', {}).get('account_size', 100000.0))
    portfolio_curve = init_cap + cum_pnl
    running_peak = np.maximum.accumulate(portfolio_curve)
    drawdowns = (portfolio_curve - running_peak) / running_peak * 100.0
    max_dd = float(abs(np.min(drawdowns))) if len(drawdowns) > 0 else 0.0
    
    # Exit reasons
    exit_counts = {}
    for t in all_trades:
        r = t.get('exit_reason', 'UNKNOWN')
        exit_counts[r] = exit_counts.get(r, 0) + 1
        
    return {
        'variant': name,
        'trades': tot_trades,
        'win_rate': round(win_rate, 2),
        'profit_factor': round(pf, 2),
        'avg_r': round(avg_r, 3),
        'long_avg_r': round(long_avg_r, 3),
        'short_avg_r': round(short_avg_r, 3),
        'tp1_rate': round(tp1_rate, 2),
        'max_drawdown': round(max_dd, 2),
        'net_pnl': round(tot_net_pnl, 2),
        'total_fees': round(tot_fees, 2),
        'friction_ratio_pct': round(friction_pct, 1),
        'stop_floor_pct': round(stop_floor_pct, 1),
        'avg_mfe': round(avg_mfe, 2),
        'avg_mae': round(avg_mae, 2),
        'fwd_ret_1b': round(avg_f1, 2),
        'fwd_ret_4b': round(avg_f4, 2),
        'fwd_ret_8b': round(avg_f8, 2),
        'exit_breakdown': exit_counts
    }

def print_and_save_report(market: str, results: List[Dict[str, Any]], out_dir: str = 'backtest'):
    """Format results as an ASCII & Markdown report."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"phase2_{market.lower()}_results.json")
    md_path = os.path.join(out_dir, f"phase2_{market.lower()}_summary.md")
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
        
    md_lines = []
    md_lines.append(f"# Phase 2 Experiment Results: {market.upper()} (1H Data)")
    md_lines.append("")
    md_lines.append("| Experiment Variant | Trades | Win Rate | Profit Factor | Avg R | Long R | Short R | Max DD | Fees ($) | Stop Floor % |")
    md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    
    print("\n" + "=" * 90)
    print(f"  PHASE 2 SINGLE-VARIABLE EXPERIMENT RESULTS: {market.upper()} (1H)")
    print("=" * 90)
    print(f"{'Variant':<25} {'Trades':<8} {'WinRate':<9} {'PF':<6} {'Avg R':<8} {'Long R':<8} {'Short R':<8} {'Max DD':<8} {'StopFloor':<10}")
    print("-" * 90)
    
    for r in results:
        v_name = r['variant']
        tr = r['trades']
        wr = f"{r['win_rate']:.1f}%"
        pf = f"{r['profit_factor']:.2f}"
        ar = f"{r['avg_r']:+.3f}R"
        lar = f"{r['long_avg_r']:+.3f}R"
        sar = f"{r['short_avg_r']:+.3f}R"
        dd = f"{r['max_drawdown']:.1f}%"
        fees = f"${r['total_fees']:,.0f}"
        sf = f"{r['stop_floor_pct']:.1f}%"
        
        print(f"{v_name:<25} {tr:<8} {wr:<9} {pf:<6} {ar:<8} {lar:<8} {sar:<8} {dd:<8} {sf:<10}")
        md_lines.append(f"| **{v_name}** | {tr} | {wr} | {pf} | {ar} | {lar} | {sar} | {dd} | {fees} | {sf} |")
        
    print("=" * 90 + "\n")
    
    # Forward return analysis
    md_lines.append("")
    md_lines.append("### Forward Returns & Excursion Analysis")
    md_lines.append("")
    md_lines.append("| Variant | Avg MFE (R) | Avg MAE (R) | Fwd Ret (+1b) | Fwd Ret (+4b) | Fwd Ret (+8b) |")
    md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for r in results:
        md_lines.append(f"| **{r['variant']}** | +{r['avg_mfe']:.2f}R | {r['avg_mae']:.2f}R | {r['fwd_ret_1b']:+.2f}% | {r['fwd_ret_4b']:+.2f}% | {r['fwd_ret_8b']:+.2f}% |")
        
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md_lines) + "\n")
        
    print(f"📁 Results saved: {json_path}")
    print(f"📁 Markdown saved: {md_path}\n")

def main():
    parser = argparse.ArgumentParser(description="Phase 2 Single-Variable 1H Experiment Runner")
    parser.add_argument('--market', type=str, default='us', choices=['us', 'japan', 'jp', 'europe', 'eu', 'crypto', 'nse', 'all'],
                        help="Target market to test")
    parser.add_argument('--period', type=str, default='730d',
                        help="Lookback period (default: 730d)")
    parser.add_argument('--interval', type=str, default='1h',
                        help="Timeframe (default: 1h)")
    parser.add_argument('--max-symbols', type=int, default=15,
                        help="Max symbols to download and test (default: 15 for rapid iteration)")
    args = parser.parse_args()
    
    markets = ['us', 'japan', 'europe', 'crypto'] if args.market == 'all' else [args.market]
    base_cfg = load_config('config/default.yaml')
    
    for m in markets:
        print(f"\n{'#' * 70}\n  RUNNING EXPERIMENTS FOR MARKET: {m.upper()}\n{'#' * 70}")
        wl_path = get_market_watchlist(m)
        all_symbols = load_watchlist(wl_path)
        if args.max_symbols and args.max_symbols > 0:
            symbols = all_symbols[:args.max_symbols]
        else:
            symbols = all_symbols
            
        print(f"Loaded {len(symbols)} symbols from {wl_path}")
        data_store = fetch_and_cache_market_data(symbols, period=args.period, interval=args.interval)
        
        if not data_store:
            print(f"❌ No valid data for market {m}, skipping.")
            continue
            
        variants = define_experiment_variants(base_cfg)
        market_results = []
        
        for v_name, v_cfg in variants.items():
            print(f"Running Variant: {v_name:<25} ... ", end="", flush=True)
            res = evaluate_variant(v_name, v_cfg, data_store)
            market_results.append(res)
            print(f"Done! Trades: {res['trades']:>4} | WR: {res['win_rate']:>5.1f}% | PF: {res['profit_factor']:>4.2f} | Avg R: {res['avg_r']:>+6.3f}R")
            
        print_and_save_report(m, market_results)

if __name__ == '__main__':
    main()
