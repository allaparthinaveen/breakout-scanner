import pytest
import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester

def create_base_df(n_bars=60, base_price=100.0):
    """Generate base consolidation bars suitable for VCP formation."""
    dates = pd.date_range('2024-01-01', periods=n_bars, freq='D')
    
    # Establish a tight base between 98 and 102
    np.random.seed(42)
    closes = np.full(n_bars, base_price) + np.random.uniform(-0.5, 0.5, n_bars)
    opens = closes - 0.1
    highs = closes + 0.8
    lows = closes - 0.8
    volume = np.full(n_bars, 100000.0)
    
    df = pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volume
    }, index=dates)
    return df

def test_next_bar_entry_and_zero_lookahead():
    """Verify that a breakout signal at Bar i enters strictly at Bar i+1 Open, never same bar."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0  # 0 for exact price check
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_base_df(60, 100.0)
    
    # Bar 50: Clean breakout candle within the 30-bar contraction window
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 106.0
    df.loc[df.index[50], 'Low'] = 100.8
    df.loc[df.index[50], 'Close'] = 105.5  # Breakout close
    df.loc[df.index[50], 'Volume'] = 500000.0
    
    # Bar 51: Next bar opens at 105.8, goes to 108.0, closes at 107.0
    df.loc[df.index[51], 'Open'] = 105.8
    df.loc[df.index[51], 'High'] = 108.0
    df.loc[df.index[51], 'Low'] = 105.5
    df.loc[df.index[51], 'Close'] = 107.0
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    
    # Must enter on bar 51's timestamp, NOT bar 50!
    assert trade['entry_time'] == df.index[51]
    assert trade['entry_price'] == pytest.approx(105.8, rel=1e-3)

def test_tp1_scale_out_and_breakeven_stop():
    """Verify TP1 exits 50% and moves stop to breakeven, then stops out at breakeven."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    cfg['management']['use_breakeven'] = True
    cfg['management']['breakeven_offset_pct'] = 0.10
    
    df = create_base_df(60, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry bar at Open = 105.0
    # Stop is ~102.0, Risk = ~3.0. TP1 = 108.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 106.0
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 105.5
    
    # Bar 52: Touches TP1 (High = 110.0), then closes at 109.0
    df.loc[df.index[52], 'Open'] = 106.0
    df.loc[df.index[52], 'High'] = 110.0  # hits TP1
    df.loc[df.index[52], 'Low'] = 105.5
    df.loc[df.index[52], 'Close'] = 109.0
    
    # Bar 53: Collapses back down to test breakeven stop (105.0 * 1.001 = 105.105)
    df.loc[df.index[53], 'Open'] = 108.0
    df.loc[df.index[53], 'High'] = 108.0
    df.loc[df.index[53], 'Low'] = 104.0  # triggers breakeven stop!
    df.loc[df.index[53], 'Close'] = 104.5
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    
    assert trade['tp1_hit'] is True
    assert trade['tp1_pnl'] > 0
    assert trade['exit_reason'] == 'BREAKEVEN_STOP'
    # Total net PnL must be positive because TP1 captured profit before breakeven stop
    assert trade['net_pnl'] > 0
    assert trade['realized_r'] > 0

def test_gap_through_stop_execution():
    """Verify that when market opens below stop, fill price is Open, not theoretical stop."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_base_df(60, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry at 105.0. Stop is ~102.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.0
    df.loc[df.index[51], 'Close'] = 104.5
    
    # Bar 52: Gaps down well below stop (opens at 99.0!)
    df.loc[df.index[52], 'Open'] = 99.0  # gap down
    df.loc[df.index[52], 'High'] = 99.5
    df.loc[df.index[52], 'Low'] = 98.0
    df.loc[df.index[52], 'Close'] = 98.5
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    
    # Exit price should reflect the gap down open (99.0), NOT stop (~102.0)
    assert trade['exit_reason'] == 'GAP_STOP'
    assert trade['exit_price'] == pytest.approx(99.0, rel=1e-3)
    assert trade['net_pnl'] < 0

def test_slippage_and_commission_friction():
    """Verify slippage and commissions are properly deducted from trade and capital."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.10     # 0.10% slippage
    cfg['risk']['commission_pct'] = 0.05   # 0.05% brokerage
    
    df = create_base_df(60, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry Open = 105.0 -> with 0.10% slippage, entry fill = 105.0 * 1.001 = 105.105
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 106.0
    df.loc[df.index[51], 'Low'] = 104.0
    df.loc[df.index[51], 'Close'] = 105.5
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    
    # Verify entry price includes slippage
    assert trade['entry_price'] == pytest.approx(105.105, rel=1e-3)
    # Verify commissions are recorded and positive
    assert trade['commissions'] > 0
    assert metrics['total_commissions'] > 0
    assert trade['net_pnl'] < trade['gross_pnl']

def test_end_of_test_mark_to_market():
    """Verify open trade at test end is closed at the final candle close (END_OF_DATA)."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['management']['use_time_exit'] = False  # disable to let it reach end of data
    
    df = create_base_df(55, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 105.0
    
    # Bars 52-54 stay within range without hitting target or stop
    for b in [52, 53, 54]:
        df.loc[df.index[b], 'Open'] = 105.0
        df.loc[df.index[b], 'High'] = 105.5
        df.loc[df.index[b], 'Low'] = 104.5
        df.loc[df.index[b], 'Close'] = 105.2
        
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    last_trade = tester.results[-1]
    assert last_trade['exit_reason'] == 'END_OF_DATA'
    assert last_trade['exit_time'] == df.index[-1]
    assert len(tester.equity_curve) == len(df) - (cfg['base']['base_lookback'] + cfg['base']['tight_lookback'])

def test_tp2_full_exit():
    """Verify that hitting both TP1 and TP2 exits 100% of the position with 2R target hit."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_base_df(60, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry at Open = 105.0. Stop ~102.0. Risk ~3.0. TP1 = 108.0, TP2 = 111.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 106.0
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 105.5
    
    # Bar 52: Surges past TP1 and reaches TP2 (High = 112.0)
    df.loc[df.index[52], 'Open'] = 106.0
    df.loc[df.index[52], 'High'] = 112.0
    df.loc[df.index[52], 'Low'] = 105.5
    df.loc[df.index[52], 'Close'] = 111.5
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    assert trade['tp1_hit'] is True
    assert trade['exit_reason'] == 'TP2_HIT'
    assert trade['realized_r'] >= 1.5

def test_time_based_exit():
    """Verify trade exits automatically when held longer than max_hold_days."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    cfg['management']['use_time_exit'] = True
    cfg['management']['max_hold_days'] = 3
    
    df = create_base_df(65, 100.0)
    
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry (Day 0 of trade)
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 105.0
    
    # Bars 52-55: Hover around 105.0 without hitting stop or target
    for b in range(52, 56):
        df.loc[df.index[b], 'Open'] = 105.0
        df.loc[df.index[b], 'High'] = 105.5
        df.loc[df.index[b], 'Low'] = 104.5
        df.loc[df.index[b], 'Close'] = 105.2
        
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    assert trade['exit_reason'] == 'TIME_EXIT'
    assert trade['held_bars'] >= 3

def test_short_trade_execution():
    """Verify Short/SELL breakout execution, Next-Bar entry, TP1 scale-out, and TP2."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_base_df(60, 100.0)
    
    # Bar 50: Bearish breakdown below base low (~99.0)
    df.loc[df.index[50], 'Open'] = 99.5
    df.loc[df.index[50], 'High'] = 99.8
    df.loc[df.index[50], 'Low'] = 95.0
    df.loc[df.index[50], 'Close'] = 95.5  # breakdown close
    df.loc[df.index[50], 'Volume'] = 500000.0
    
    # Bar 51: Next bar entry at Open = 95.0
    df.loc[df.index[51], 'Open'] = 95.0
    df.loc[df.index[51], 'High'] = 95.5
    df.loc[df.index[51], 'Low'] = 94.0
    df.loc[df.index[51], 'Close'] = 94.5
    
    # Bar 52: Plunges down to hit TP1 and TP2
    df.loc[df.index[52], 'Open'] = 94.0
    df.loc[df.index[52], 'High'] = 94.2
    df.loc[df.index[52], 'Low'] = 88.0
    df.loc[df.index[52], 'Close'] = 89.0
    
    tester = Backtester(cfg)
    metrics = tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    assert trade['direction'] == -1
    assert trade['entry_time'] == df.index[51]
    assert trade['tp1_hit'] is True
    assert trade['net_pnl'] > 0

def test_metrics_calculation_and_drawdown():
    """Verify drawdown, expectancy, profit factor, and win rate calculation."""
    cfg = load_config()
    tester = Backtester(cfg)
    
    trades = [
        {'net_pnl': 200.0, 'realized_r': 2.0, 'tp1_hit': True, 'commissions': 10.0},
        {'net_pnl': -100.0, 'realized_r': -1.0, 'tp1_hit': False, 'commissions': 10.0},
        {'net_pnl': 150.0, 'realized_r': 1.5, 'tp1_hit': True, 'commissions': 10.0},
    ]
    equity_curve = [100000.0, 100200.0, 100100.0, 100250.0]
    
    m = tester._compute_metrics(trades, 100000.0, 100250.0, equity_curve)
    
    assert m['trades'] == 3
    assert m['wins'] == 2
    assert m['losses'] == 1
    assert m['win_rate'] == pytest.approx(0.6667, rel=1e-3)
    assert m['profit_factor'] == pytest.approx(3.5, rel=1e-2)
    assert m['average_r'] == pytest.approx(0.83, rel=1e-2)
    assert m['tp1_hit_rate'] == pytest.approx(0.6667, rel=1e-3)
    assert m['max_drawdown_pct'] > 0  # from 100200 down to 100100 is ~0.10%
    assert m['total_commissions'] == 30.0


def test_hourly_timeframe_hold_days_synchronization():
    """Verify that on 1H intraday candles, max_hold_days measures elapsed days via timestamps,
    preventing premature exit after just N hourly bars."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    cfg['management']['use_time_exit'] = True
    cfg['management']['max_hold_days'] = 2  # 2 days hold
    
    # 100 hourly bars (approx 4+ days of data)
    dates = pd.date_range('2024-01-01 09:00', periods=100, freq='h')
    closes = np.full(100, 100.0)
    df = pd.DataFrame({
        'Open': closes - 0.1,
        'High': closes + 0.5,
        'Low': closes - 0.5,
        'Close': closes,
        'Volume': np.full(100, 100000.0)
    }, index=dates)
    
    # Breakout at bar 50 (2024-01-03 11:00)
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 106.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 105.0
    
    # Bar 51: Entry (2024-01-03 12:00)
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 105.0
    
    # Bars 52-99: Hover around 105.0 without hitting stop or target
    for b in range(52, 100):
        df.loc[df.index[b], 'Open'] = 105.0
        df.loc[df.index[b], 'High'] = 105.5
        df.loc[df.index[b], 'Low'] = 104.5
        df.loc[df.index[b], 'Close'] = 105.0
        
    tester = Backtester(cfg)
    tester.run('TEST_1H', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    # Bar 51 + 2 bars = Bar 53 (only 2 hours in). It should NOT exit here!
    # With max_hold_days=2, it should exit after 48 hours (Bar 51 + 48 = Bar 99)
    assert trade['exit_reason'] == 'TIME_EXIT'
    assert trade['held_bars'] >= 48
    assert trade['held_days'] >= 2.0


