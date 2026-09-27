import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from copy import deepcopy
from .backtester import Backtester

class WalkForwardValidator:
    def __init__(self, base_cfg: dict, symbol: str):
        self.base_cfg = base_cfg
        self.symbol = symbol
        
    def generate_windows(self, df: pd.DataFrame, train_size: int, test_size: int, step_size: int) -> List[Dict]:
        """
        Generates rolling train/test windows.
        train_size, test_size, step_size are in number of bars.
        """
        windows = []
        n = len(df)
        start = 0
        
        while start + train_size + test_size <= n:
            train_end = start + train_size
            test_end = train_end + test_size
            
            windows.append({
                'train': df.iloc[start:train_end],
                'test': df.iloc[train_end:test_end],
                'window_id': len(windows) + 1
            })
            
            start += step_size
            
        return windows

    def grid_search(self, train_df: pd.DataFrame, param_grid: List[dict]) -> dict:
        """
        Runs a grid search over the training data to find the best configuration.
        Returns the best config.
        """
        best_cfg = None
        best_metric = -float('inf')
        
        for p in param_grid:
            # Create a localized config for this iteration
            cfg = deepcopy(self.base_cfg)
            # Example param structure: {'breakout': {'min_score': 3}}
            for category, params in p.items():
                if category in cfg:
                    cfg[category].update(params)
                    
            tester = Backtester(cfg)
            metrics = tester.run(self.symbol, train_df)
            
            # Objective function: Maximize Expectancy * (Win Rate > 0.4)
            # In a real system, you'd want something balancing return and drawdown.
            expectancy = metrics.get('expectancy', 0)
            if expectancy > best_metric and metrics.get('trades', 0) > 5:
                best_metric = expectancy
                best_cfg = cfg
                
        return best_cfg if best_cfg else self.base_cfg

    def run_validation(self, df: pd.DataFrame, param_grid: List[dict], train_size=2000, test_size=500, step_size=500) -> Dict:
        """
        Executes the full walk-forward validation process.
        """
        windows = self.generate_windows(df, train_size, test_size, step_size)
        
        out_of_sample_results = []
        out_of_sample_trades = []
        initial_capital = self.base_cfg['risk']['account_size']
        capital = initial_capital
        
        for w in windows:
            print(f"Running Window {w['window_id']} / {len(windows)}")
            
            # 1. Train / Optimize
            best_cfg = self.grid_search(w['train'], param_grid)
            
            # 2. Test (Out of Sample)
            # We want continuous capital compounding
            test_cfg = deepcopy(best_cfg)
            test_cfg['risk']['account_size'] = capital
            
            tester = Backtester(test_cfg)
            metrics = tester.run(self.symbol, w['test'])
            
            # Collect trades and adjust capital
            if tester.results:
                out_of_sample_trades.extend(tester.results)
                capital = tester.results[-1]['capital']
                
            out_of_sample_results.append({
                'window_id': w['window_id'],
                'best_cfg': best_cfg,
                'oos_metrics': metrics
            })
            
        # Compute global OOS metrics
        global_tester = Backtester(self.base_cfg)
        global_metrics = global_tester._compute_metrics(out_of_sample_trades, capital)
        
        return {
            'windows': out_of_sample_results,
            'global_oos_metrics': global_metrics,
            'all_oos_trades': out_of_sample_trades
        }
