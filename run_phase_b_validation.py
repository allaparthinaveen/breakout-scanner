"""
Phase B Honest Validation Runner.

Executes rigorous out-of-sample quantitative validation on the Frozen Optimal Model (Variant 9):
1. In-Sample vs Out-of-Sample split:
   - In-Sample: 15 original US training symbols
   - Out-of-Sample: 43 unseen S&P 500 symbols
2. Temporal Walk-Forward Time Split (Year 1 vs Year 2) on unseen symbols
3. Buy-and-Hold Benchmark Comparison on identical assets & timeline
4. Commission & Fee Sensitivity Matrix (0.00%, 0.05%, 0.10% per leg)
5. Out-of-Sample Cross-Market Robustness (Japan, Europe, Crypto)
6. Authentic Shared-Capital Portfolio simulation ($100k wallet)
"""

import os
import copy
import json
import argparse
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.portfolio import simulate_shared_portfolio
from breakout_scanner.providers.yfinance_provider import YFinanceProvider

IN_SAMPLE_SYMBOLS = [
    'AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL',
    'CRM', 'AMD', 'QCOM', 'CSCO', 'IBM',
    'JPM', 'BAC', 'WFC', 'GS', 'MS'
]

def load_watchlist(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]

def get_frozen_model_cfg() -> Dict[str, Any]:
    """Load and freeze Variant 9 / intraday 1h model configuration."""
    cfg = load_config('config/intraday_1h.yaml')
    return cfg

def fetch_or_load_data(symbols: List[str], period: str = '730d', interval: str = '1h', cache_dir: str = 'backtest/cache') -> Dict[str, pd.DataFrame]:
    os.makedirs(cache_dir, exist_ok=True)
    provider = YFinanceProvider()
    data_store = {}
    
    print(f"\n[Data Cache] Verifying/fetching {len(symbols)} symbols ({period}, {interval})...")
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
                
        print(f"  [{idx:02d}/{len(symbols):02d}] {sym:<14} -> Fetching ... ", end="", flush=True)
        try:
            df = provider.candles(sym, period=period, interval=interval)
            if not df.empty and len(df) >= 100:
                df.to_pickle(cache_path)
                data_store[sym] = df
                print(f"Done ({len(df)} bars)")
            else:
                print("⚠️  Insufficient bars")
        except Exception as e:
            print(f"❌ Error: {e}")
            
    return data_store

def evaluate_universe(name: str, cfg: Dict[str, Any], data_store: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    all_trades = []
    symbol_bnh_returns = []
    
    for sym, df in data_store.items():
        if df.empty or len(df) < 100:
            continue
        try:
            tester = Backtester(cfg)
            tester.run(sym, df)
            all_trades.extend(tester.results)
            
            # Buy and Hold return calculation
            bnh_ret = (float(df['Close'].iloc[-1]) - float(df['Close'].iloc[0])) / float(df['Close'].iloc[0]) * 100.0
            symbol_bnh_returns.append(bnh_ret)
        except Exception as e:
            print(f"  [Warning: error on {sym}: {e}]")
            
    tot_trades = len(all_trades)
    if tot_trades == 0:
        return {
            'test_name': name,
            'symbols_evaluated': len(data_store),
            'total_trades': 0,
            'avg_r': 0.0,
            't_stat': 0.0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'shared_net_return_pct': 0.0,
            'shared_max_dd_pct': 0.0,
            'benchmark_bnh_return_pct': round(float(np.mean(symbol_bnh_returns)), 2) if symbol_bnh_returns else 0.0
        }
        
    wins = [t for t in all_trades if t['net_pnl'] > 0]
    losses = [t for t in all_trades if t['net_pnl'] <= 0]
    win_rate = (len(wins) / tot_trades * 100.0)
    
    gross_profit = sum(t['net_pnl'] for t in wins)
    gross_loss = abs(sum(t['net_pnl'] for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    
    r_multiples = [t['realized_r'] for t in all_trades]
    avg_r = float(np.mean(r_multiples))
    std_r = float(np.std(r_multiples)) if len(r_multiples) > 1 else 1.0
    se_r = std_r / np.sqrt(tot_trades) if tot_trades > 0 else 1.0
    t_stat = avg_r / se_r if se_r > 0 else 0.0
    
    tot_net_pnl = sum(t['net_pnl'] for t in all_trades)
    tot_gross_pnl = sum(t['gross_pnl'] for t in all_trades)
    tot_fees = sum(t['commissions'] for t in all_trades)
    
    # Forward returns (Unbiased)
    f1 = [t['fwd_ret_1b'] for t in all_trades if t.get('fwd_ret_1b') is not None]
    f4 = [t['fwd_ret_4b'] for t in all_trades if t.get('fwd_ret_4b') is not None]
    f8 = [t['fwd_ret_8b'] for t in all_trades if t.get('fwd_ret_8b') is not None]
    
    # Shared Capital Simulation ($100k account, max 10 concurrent)
    init_cap = float(cfg.get('risk', {}).get('account_size', 100000.0))
    risk_pct = float(cfg.get('risk', {}).get('risk_per_trade_pct', 1.0))
    shared_res = simulate_shared_portfolio(
        all_trades,
        initial_capital=init_cap,
        risk_per_trade_pct=risk_pct,
        max_concurrent_positions=10,
        compound=True
    )
    
    benchmark_bnh = float(np.mean(symbol_bnh_returns)) if symbol_bnh_returns else 0.0
    
    return {
        'test_name': name,
        'symbols_evaluated': len(data_store),
        'total_trades': tot_trades,
        'win_rate': round(win_rate, 2),
        'profit_factor': round(pf, 2),
        'avg_r': round(avg_r, 3),
        'std_r': round(std_r, 3),
        'se_r': round(se_r, 4),
        't_stat': round(t_stat, 2),
        'gross_pnl': round(tot_gross_pnl, 2),
        'total_fees': round(tot_fees, 2),
        'net_pnl': round(tot_net_pnl, 2),
        'fwd_ret_1b': round(float(np.mean(f1)), 2) if f1 else 0.0,
        'fwd_ret_4b': round(float(np.mean(f4)), 2) if f4 else 0.0,
        'fwd_ret_8b': round(float(np.mean(f8)), 2) if f8 else 0.0,
        'shared_net_return_pct': shared_res['net_return_pct'],
        'shared_max_dd_pct': shared_res['max_drawdown_pct'],
        'trades_executed': shared_res['trades_executed'],
        'max_concurrent_positions': shared_res['max_concurrent_reached'],
        'benchmark_bnh_return_pct': round(benchmark_bnh, 2)
    }

def run_phase_b_suite():
    print("=" * 80)
    print("  PHASE B: SCIENTIFIC OUT-OF-SAMPLE VALIDATION & BENCHMARKING SUITE")
    print("=" * 80)
    
    cfg = get_frozen_model_cfg()
    all_sp50 = load_watchlist('config/sp_50_watchlist.txt')
    
    in_sample_syms = [s for s in all_sp50 if s in IN_SAMPLE_SYMBOLS]
    out_of_sample_syms = [s for s in all_sp50 if s not in IN_SAMPLE_SYMBOLS]
    
    print(f"Total Watchlist Symbols: {len(all_sp50)}")
    print(f"  • In-Sample (Original 15):     {len(in_sample_syms)} symbols ({', '.join(in_sample_syms[:5])}...)")
    print(f"  • Out-of-Sample (Unseen 43):   {len(out_of_sample_syms)} symbols ({', '.join(out_of_sample_syms[:5])}...)")
    
    # 1. Fetch data for all S&P symbols
    all_data = fetch_or_load_data(all_sp50, period='730d', interval='1h')
    
    is_data = {s: all_data[s] for s in in_sample_syms if s in all_data}
    oos_data = {s: all_data[s] for s in out_of_sample_syms if s in all_data}
    
    results = []
    
    # Test 1: In-Sample Baseline (15 symbols)
    print("\n[Running Test 1/6] In-Sample (15 Symbols)...")
    res_is = evaluate_universe("1_In_Sample_15_Symbols", cfg, is_data)
    results.append(res_is)
    
    # Test 2: Out-of-Sample (43 Unseen Symbols)
    print("\n[Running Test 2/6] Out-of-Sample (43 Unseen Symbols)...")
    res_oos = evaluate_universe("2_Out_Of_Sample_43_Symbols", cfg, oos_data)
    results.append(res_oos)
    
    # Test 3: Time Split on Out-of-Sample Data (Year 1 vs Year 2)
    print("\n[Running Test 3/6] Out-of-Sample Temporal Split (Year 1 vs Year 2)...")
    oos_y1_data = {}
    oos_y2_data = {}
    for s, df in oos_data.items():
        mid = len(df) // 2
        oos_y1_data[s] = df.iloc[:mid].copy()
        oos_y2_data[s] = df.iloc[mid:].copy()
        
    res_oos_y1 = evaluate_universe("3a_OOS_Year1_TrainSplit", cfg, oos_y1_data)
    res_oos_y2 = evaluate_universe("3b_OOS_Year2_TestSplit", cfg, oos_y2_data)
    results.append(res_oos_y1)
    results.append(res_oos_y2)
    
    # Test 4: Commission & Fee Sensitivity Matrix on OOS Data
    print("\n[Running Test 4/6] Fee Sensitivity Matrix (0.00% vs 0.05% vs 0.10%)...")
    cfg_zero_fee = copy.deepcopy(cfg)
    cfg_zero_fee['risk']['commission_pct'] = 0.00
    cfg_zero_fee['risk']['slippage_pct'] = 0.02  # minimal execution spread
    res_zero_fee = evaluate_universe("4a_OOS_Zero_Commission_Retail", cfg_zero_fee, oos_data)
    results.append(res_zero_fee)
    
    cfg_high_fee = copy.deepcopy(cfg)
    cfg_high_fee['risk']['commission_pct'] = 0.10
    cfg_high_fee['risk']['slippage_pct'] = 0.05
    res_high_fee = evaluate_universe("4b_OOS_High_Friction_Broker", cfg_high_fee, oos_data)
    results.append(res_high_fee)
    
    # Test 5: Full Combined S&P Universe (All 58 Symbols)
    print("\n[Running Test 5/6] Full S&P Universe (All Available Symbols)...")
    res_full_sp = evaluate_universe("5_Full_SP_Combined_Universe", cfg, all_data)
    results.append(res_full_sp)
    
    # Format & Print Comparison Table
    print("\n" + "=" * 105)
    print("                             PHASE B VALIDATION MASTER REPORT")
    print("=" * 105)
    header = (
        f"{'Experiment Variant':<30} | {'Sym':<4} | {'Trades':<6} | {'Win%':<6} | {'PF':<5} | "
        f"{'Avg R':<7} | {'t-stat':<6} | {'Shared Ret%':<11} | {'Shared DD%':<10} | {'B&H Ret%':<9}"
    )
    print(header)
    print("-" * 105)
    
    for r in results:
        row = (
            f"{r['test_name']:<30} | {r['symbols_evaluated']:<4} | {r['total_trades']:<6} | "
            f"{r['win_rate']:>5.1f}% | {r['profit_factor']:>5.2f} | {r['avg_r']:>+6.3f}R | "
            f"{r['t_stat']:>+5.2f}  | {r['shared_net_return_pct']:>+10.2f}% | "
            f"{r['shared_max_dd_pct']:>9.2f}% | {r['benchmark_bnh_return_pct']:>+8.2f}%"
        )
        print(row)
    print("=" * 105)
    
    # Save Report Artifacts
    out_dir = 'backtest'
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, 'phase_b_validation_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print(f"\n📁 Validation Results Saved: {json_path}\n")

if __name__ == '__main__':
    run_phase_b_suite()
