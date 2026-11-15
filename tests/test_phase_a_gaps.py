import pytest
import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.models import Setup, State
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.backtester import Backtester
from breakout_scanner.walkforward import WalkForwardValidator
from tests.test_phase0_fixes import create_synthetic_data

def test_walkforward_capital_after():
    """
    Verify WalkForwardValidator uses 'capital_after' and calls _compute_metrics with valid signature.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['account_size'] = 100000.0

    df = create_synthetic_data(120, 100.0)
    # Inject breakout at bar 50
    df.loc[df.index[50], 'Close'] = 105.0
    df.loc[df.index[50], 'High'] = 105.5
    # Inject breakout at bar 100
    df.loc[df.index[100], 'Close'] = 106.0
    df.loc[df.index[100], 'High'] = 106.5

    validator = WalkForwardValidator(cfg, "TEST")
    param_grid = [{'risk': {'risk_per_trade_pct': 1.0}}]
    
    # Run small walkforward validation (train_size=60, test_size=30, step_size=30)
    res = validator.run_validation(df, param_grid, train_size=60, test_size=30, step_size=30)
    
    assert 'windows' in res
    assert 'global_oos_metrics' in res
    assert isinstance(res['global_oos_metrics'], dict)


def test_is_fresh_entry_base_breach():
    """
    Verify is_fresh_entry rejects setups where price has fallen back inside the base
    or breached stop loss.
    """
    # 1. Long setup holding above base resistance -> Fresh
    s_long_valid = Setup(
        symbol="XYZ",
        state=State.BUY,
        direction=1,
        entry=105.0,
        resistance=104.0,
        stop=102.0,
        current_price=104.5,
        bars_since_breakout=1
    )
    assert s_long_valid.is_fresh_entry is True

    # 2. Long setup fallen back inside base (price < resistance) -> Not fresh
    s_long_in_base = Setup(
        symbol="XYZ",
        state=State.BUY,
        direction=1,
        entry=105.0,
        resistance=104.0,
        stop=102.0,
        current_price=103.5,
        bars_since_breakout=1
    )
    assert s_long_in_base.is_fresh_entry is False

    # 3. Long setup stop breached -> Not fresh
    s_long_stopped = Setup(
        symbol="XYZ",
        state=State.BUY,
        direction=1,
        entry=105.0,
        resistance=104.0,
        stop=102.0,
        current_price=101.5,
        bars_since_breakout=1
    )
    assert s_long_stopped.is_fresh_entry is False

    # 4. Short setup holding below base support -> Fresh
    s_short_valid = Setup(
        symbol="XYZ",
        state=State.SELL,
        direction=-1,
        entry=95.0,
        support=96.0,
        stop=98.0,
        current_price=95.5,
        bars_since_breakout=1
    )
    assert s_short_valid.is_fresh_entry is True

    # 5. Short setup fallen back inside base (price > support) -> Not fresh
    s_short_in_base = Setup(
        symbol="XYZ",
        state=State.SELL,
        direction=-1,
        entry=95.0,
        support=96.0,
        stop=98.0,
        current_price=96.5,
        bars_since_breakout=1
    )
    assert s_short_in_base.is_fresh_entry is False


def test_failed_breakout_exit_not_cleared_on_current_bar():
    """
    Verify engine retains State.FAILED setup when failed breakout exit triggers on current bar.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['management']['use_failed_breakout_exit'] = True
    cfg['management']['failed_breakout_max_bars'] = 3

    df = create_synthetic_data(60, 100.0)
    
    # Resistance at 100.5
    # Bar 50: Breakout candle closes above resistance
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 104.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 103.5

    # Bar 51 (latest bar): Price plunges back below resistance (99.0 < 100.5)
    df = df.iloc[:52].copy()
    df.loc[df.index[51], 'Open'] = 102.0
    df.loc[df.index[51], 'High'] = 102.2
    df.loc[df.index[51], 'Low'] = 98.8
    df.loc[df.index[51], 'Close'] = 99.0

    engine = BreakoutEngine(cfg, "TEST")
    setup = engine.run(df)

    assert setup.state == State.FAILED
    assert setup.failed_breakout_confirmed is True
    assert setup.failure_reason is not None
    assert "FAILED_BREAKOUT" in setup.failure_reason
    assert setup.entry is not None


def test_unbiased_forward_returns_on_stopped_out_trades():
    """
    Verify all trades have unbiased forward returns (+1b, +2b, +4b, +8b) populated
    even if the trade exited on bar 1 or 2.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0

    # 70 bars total
    df = create_synthetic_data(70, 100.0)

    # Bar 50: Breakout candle
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.5

    # Bar 51: Entry Open = 105.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.0
    df.loc[df.index[51], 'Close'] = 104.2

    # Bar 52: Plunges into Stop Loss (Low = 100.0) -> Exits STOP_LOSS on bar 2
    df.loc[df.index[52], 'Open'] = 103.0
    df.loc[df.index[52], 'High'] = 103.0
    df.loc[df.index[52], 'Low'] = 100.0
    df.loc[df.index[52], 'Close'] = 101.0

    # Bars 53 to 60: Market continues (bars 53=bar 2, 55=bar 4, 59=bar 8 from entry bar 51)
    # Entry at bar 51 (Open = 105.0)
    # Bar 51 + 1 = Bar 52 Close: 101.0
    # Bar 51 + 4 = Bar 55 Close: 108.0
    df.loc[df.index[55], 'Close'] = 108.0
    # Bar 51 + 8 = Bar 59 Close: 110.0
    df.loc[df.index[59], 'Close'] = 110.0

    tester = Backtester(cfg)
    tester.run('TEST', df)

    assert len(tester.results) == 1
    trade = tester.results[0]
    
    # Trade exited early on STOP_LOSS
    assert trade['exit_reason'] == 'STOP_LOSS'
    assert trade['held_bars'] <= 2

    # Forward returns must STILL be populated (no survivorship bias!)
    assert trade['fwd_ret_1b'] is not None
    assert trade['fwd_ret_2b'] is not None
    assert trade['fwd_ret_4b'] is not None
    assert trade['fwd_ret_8b'] is not None

    entry_price = trade['entry_price']
    expected_fwd_4b = round(float((108.0 - entry_price) / entry_price * 100.0), 2)
    expected_fwd_8b = round(float((110.0 - entry_price) / entry_price * 100.0), 2)

    assert trade['fwd_ret_4b'] == expected_fwd_4b
    assert trade['fwd_ret_8b'] == expected_fwd_8b


def test_engine_open_confirmation_parity():
    """
    Verify engine invalidates breakout if next-bar open fails open-confirmation.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['breakout']['use_open_confirmation'] = True
    cfg['breakout']['max_open_gap_pct'] = 0.50

    df = create_synthetic_data(55, 100.0)
    # Breakout at bar 50: closes at 105.0
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.5
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 105.0

    # Bar 51: Opens with huge adverse gap down (99.0 << 105.0) and below pivot
    df.loc[df.index[51], 'Open'] = 99.0
    df.loc[df.index[51], 'High'] = 100.0
    df.loc[df.index[51], 'Low'] = 98.5
    df.loc[df.index[51], 'Close'] = 99.5

    engine = BreakoutEngine(cfg, "TEST")
    setup = engine.run(df.iloc[:52])

    assert setup.state == State.FAILED
    assert setup.failed_breakout_confirmed is True
    assert "OPEN_CONFIRMATION_FAILED" in setup.failure_reason


def test_engine_max_hold_bars_time_exit():
    """
    Verify engine triggers time exit when held bars reaches max_hold_bars.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['management']['use_time_exit'] = True
    cfg['management']['max_hold_bars'] = 3
    cfg['management']['use_failed_breakout_exit'] = False

    df = create_synthetic_data(60, 100.0)
    # Breakout at bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.5

    # Bars 51, 52, 53: Price hovers without hitting stop (stop ~100.0) or TP (tp1 ~108)
    for b in (51, 52, 53):
        df.loc[df.index[b], 'Open'] = 104.5
        df.loc[df.index[b], 'High'] = 105.0
        df.loc[df.index[b], 'Low'] = 104.0
        df.loc[df.index[b], 'Close'] = 104.5

    engine = BreakoutEngine(cfg, "TEST")
    setup = engine.run(df.iloc[:54])

    # After 3 bars in trade (bars 51, 52, 53), it exits via time exit -> state is IDLE
    assert setup.state == State.IDLE


def test_shared_portfolio_simulation():
    """
    Verify simulate_shared_portfolio correctly computes returns, drawdowns, and limits capacity.
    """
    from breakout_scanner.portfolio import simulate_shared_portfolio

    trades = [
        {'entry_time': '2024-01-01 10:00', 'exit_time': '2024-01-02 10:00', 'realized_r': 1.0},
        {'entry_time': '2024-01-01 11:00', 'exit_time': '2024-01-02 11:00', 'realized_r': -1.0},
        {'entry_time': '2024-01-03 10:00', 'exit_time': '2024-01-04 10:00', 'realized_r': 2.0},
    ]

    res = simulate_shared_portfolio(trades, initial_capital=100000.0, risk_per_trade_pct=1.0, max_concurrent_positions=2)
    assert res['total_trades_considered'] == 3
    assert res['trades_executed'] == 3
    assert res['trades_rejected_capacity'] == 0
    assert res['max_concurrent_reached'] == 2
    assert res['final_capital'] > 100000.0
    assert res['net_return_pct'] > 0.0

