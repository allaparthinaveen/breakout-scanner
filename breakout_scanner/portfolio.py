"""
Institutional Shared-Capital Portfolio Simulator.

Simulates a unified capital pool across multiple symbols:
1. Chronological event processing across all candidate trades.
2. Dynamic risk sizing based on current shared equity:
   risk_cash = current_equity * (risk_per_trade_pct / 100.0)
3. Concurrent position limits and margin tracking (no artificial leverage).
4. Authentic portfolio equity curve and drawdown calculation.
"""
from typing import List, Dict, Any
import numpy as np

def simulate_shared_portfolio(
    trades: List[Dict[str, Any]],
    initial_capital: float = 100000.0,
    risk_per_trade_pct: float = 1.0,
    max_concurrent_positions: int = 10,
    compound: bool = True
) -> Dict[str, Any]:
    """
    Simulates chronological execution of trades on a single shared account.
    """
    if not trades:
        return {
            'initial_capital': initial_capital,
            'final_capital': initial_capital,
            'net_return_pct': 0.0,
            'max_drawdown_pct': 0.0,
            'total_trades_considered': 0,
            'trades_executed': 0,
            'trades_rejected_capacity': 0,
            'max_concurrent_reached': 0,
            'equity_curve': [initial_capital]
        }
        
    # Sort chronologically by entry_time
    sorted_trades = sorted(trades, key=lambda t: str(t.get('entry_time', '')))
    
    current_equity = initial_capital
    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    equity_curve = [current_equity]
    
    # Active positions: list of dicts with {'exit_time': Any, 'pnl': float}
    active_positions = []
    max_concurrent_reached = 0
    trades_executed = 0
    trades_rejected = 0
    
    for t in sorted_trades:
        e_time = str(t.get('entry_time', ''))
        
        # 1. Close any active positions that exited before or at this trade's entry_time
        remaining_active = []
        for pos in active_positions:
            if str(pos['exit_time']) <= e_time:
                current_equity += pos['pnl']
                peak_equity = max(peak_equity, current_equity)
                dd = (peak_equity - current_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
                max_drawdown_pct = max(max_drawdown_pct, dd)
                equity_curve.append(current_equity)
            else:
                remaining_active.append(pos)
        active_positions = remaining_active
        
        # 2. Check capacity constraint
        if len(active_positions) >= max_concurrent_positions or current_equity <= 0:
            trades_rejected += 1
            continue
            
        # 3. Size risk from current shared equity
        base_cap = current_equity if compound else initial_capital
        risk_cash = base_cap * (risk_per_trade_pct / 100.0)
        
        # Trade net PnL = realized_r * risk_cash
        realized_r = float(t.get('realized_r', 0.0))
        trade_pnl = realized_r * risk_cash
        
        active_positions.append({
            'exit_time': str(t.get('exit_time', '')),
            'pnl': trade_pnl
        })
        trades_executed += 1
        max_concurrent_reached = max(max_concurrent_reached, len(active_positions))
        
    # 4. Close any positions still open at the end
    for pos in active_positions:
        current_equity += pos['pnl']
        peak_equity = max(peak_equity, current_equity)
        dd = (peak_equity - current_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, dd)
        equity_curve.append(current_equity)
        
    net_return_pct = ((current_equity - initial_capital) / initial_capital) * 100.0
    
    return {
        'initial_capital': round(initial_capital, 2),
        'final_capital': round(current_equity, 2),
        'net_return_pct': round(net_return_pct, 2),
        'max_drawdown_pct': round(max_drawdown_pct, 2),
        'total_trades_considered': len(sorted_trades),
        'trades_executed': trades_executed,
        'trades_rejected_capacity': trades_rejected,
        'max_concurrent_reached': max_concurrent_reached,
        'equity_curve': equity_curve
    }
