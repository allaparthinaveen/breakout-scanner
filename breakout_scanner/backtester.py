import pandas as pd
import numpy as np
from typing import List, Dict
from .models import Setup, State
from .engine import BreakoutEngine

class Backtester:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.results = []
        self.equity_curve = []
        
    def run(self, symbol: str, df: pd.DataFrame) -> Dict:
        """
        Run backtest on a single symbol.
        df must contain: Open, High, Low, Close, Volume
        """
        df = df.copy()
        
        engine = BreakoutEngine(self.cfg, symbol)
        
        capital = self.cfg['risk']['account_size']
        risk_pct = self.cfg['risk']['risk_per_trade_pct'] / 100.0
        
        # We will simulate feeding the engine bar by bar
        # For efficiency in backtesting, we bypass engine.run(raw) and use its logic directly 
        # on pre-calculated arrays, or we can refactor engine to take a row and history.
        # To keep it simple and maintain parity, we will just use a modified loop here that replicates engine.
        # But wait, we want to prove the *engine* behaves correctly.
        # Let's patch the engine to accept a pre-calculated dataframe and current index.
        
        active_trade = None
        trades = []
        
        for i in range(80, len(df)):
            # Update engine setup using the pre-calculated data
            # To prove engine, we can feed it the slice, but that's O(N^2).
            # We'll use a hack to make it O(N): engine just needs df and index i.
            # We can pass df and let engine run on the full df but constrained to index i.
            
            # Since engine.run expects a truncated df, let's create a proxy or just run it.
            # If we run it on df.iloc[:i+1], it takes time. Let's try it for small datasets first.
            raw_slice = df.iloc[:i+1]
            setup = engine.run(raw_slice)
            
            # Check for entry
            if setup.state in (State.BUY, State.SELL) and active_trade is None:
                risk_cash = capital * risk_pct
                risk_per_share = abs(setup.entry - setup.stop)
                shares = risk_cash / risk_per_share if risk_per_share > 0 else 0
                
                active_trade = {
                    'symbol': symbol,
                    'direction': setup.direction,
                    'entry_time': df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i,
                    'entry_price': setup.entry,
                    'stop': setup.stop,
                    'target': setup.target,
                    'shares': shares,
                    'risk': risk_cash
                }
                
                # Reset setup state after entry to look for new setups
                # In real life, we track the position separately from the setup.
                engine.setup = Setup(symbol)
                
            # Manage active trade
            if active_trade is not None:
                current_high = float(df.High.iloc[i])
                current_low = float(df.Low.iloc[i])
                
                # Check stop and target (assuming no gaps for simplicity in V1)
                exit_price = None
                pnl = 0
                
                if active_trade['direction'] == 1: # Long
                    if current_low <= active_trade['stop']:
                        exit_price = active_trade['stop']
                    elif current_high >= active_trade['target']:
                        exit_price = active_trade['target']
                else: # Short
                    if current_high >= active_trade['stop']:
                        exit_price = active_trade['stop']
                    elif current_low <= active_trade['target']:
                        exit_price = active_trade['target']
                        
                if exit_price is not None:
                    if active_trade['direction'] == 1:
                        pnl = (exit_price - active_trade['entry_price']) * active_trade['shares']
                    else:
                        pnl = (active_trade['entry_price'] - exit_price) * active_trade['shares']
                        
                    capital += pnl
                    active_trade['exit_time'] = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
                    active_trade['exit_price'] = exit_price
                    active_trade['pnl'] = pnl
                    active_trade['capital'] = capital
                    
                    trades.append(active_trade)
                    active_trade = None
                    
            self.equity_curve.append(capital)
            
        self.results.extend(trades)
        return self._compute_metrics(trades, capital)
        

        
    def _compute_metrics(self, trades: List[Dict], final_capital: float) -> Dict:
        if not trades:
            return {'trades': 0}
            
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        
        win_rate = len(wins) / len(trades) if trades else 0
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_r = np.mean([t['pnl'] / t['risk'] for t in trades])
        
        return {
            'trades': len(trades),
            'win_rate': round(win_rate, 4),
            'profit_factor': round(profit_factor, 2),
            'expectancy': round((win_rate * np.mean([t['pnl'] for t in wins] if wins else [0])) - ((1 - win_rate) * np.mean([abs(t['pnl']) for t in losses] if losses else [0])), 2),
            'average_r': round(avg_r, 2),
            'net_return_pct': round((final_capital - self.cfg['risk']['account_size']) / self.cfg['risk']['account_size'] * 100, 2)
        }
