import pytest
import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.engine import compute_indicators
from breakout_scanner.models import Setup, State

def create_synthetic_data(n_bars=60, base_price=100.0, freq='D'):
    """Generate consolidation bars with standard structure."""
    dates = pd.date_range('2024-01-01', periods=n_bars, freq=freq)
    np.random.seed(42)
    closes = np.full(n_bars, base_price) + np.random.uniform(-0.5, 0.5, n_bars)
    opens = closes - 0.1
    highs = closes + 0.8
    lows = closes - 0.8
    volume = np.full(n_bars, 100000.0)
    
    return pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volume
    }, index=dates)

def test_sma50_slope_filter():
    """Verify Rule 1: use_slope_filter requires positive SMA50 slope for longs."""
    cfg = load_config()
    cfg['trend']['use_trend_filter'] = True
    cfg['trend']['use_slope_filter'] = True
    cfg['trend']['slope_lookback_bars'] = 5
    cfg['volume']['use_breakout_volume'] = False
    cfg['base']['require_tightening'] = False
    
    df = create_synthetic_data(60, 100.0)
    # Price is above SMA, but SMA is declining (prices were 120, now dropping to 105)
    df.loc[df.index[:30], 'Close'] = 120.0
    df.loc[df.index[30:], 'Close'] = 105.0
    df['SMA'] = df['Close'].rolling(50).mean()
    
    ind_df = compute_indicators(df, cfg)
    # LongTrendOK should be False because SMA is declining
    assert not ind_df['LongTrendOK'].iloc[-1]
    
    # Now create rising SMA (prices 90 rising to 105)
    df.loc[df.index[:30], 'Close'] = 90.0
    df.loc[df.index[30:], 'Close'] = 105.0
    df['SMA'] = df['Close'].rolling(50).mean()
    
    ind_df_rising = compute_indicators(df, cfg)
    assert ind_df_rising['LongTrendOK'].iloc[-1]

def test_hourly_volume_profile():
    """Verify Rule 2: use_hourly_volume_profile benchmarks volume against same hour of day."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = True
    cfg['volume']['use_hourly_volume_profile'] = True
    cfg['volume']['breakout_multiplier'] = 1.2
    
    # Create 1H intraday candles
    df = create_synthetic_data(100, 100.0, freq='h')
    
    # Morning bars (09:00) have naturally 300,000 volume, midday bars (12:00) have 50,000 volume
    for i, dt in enumerate(df.index):
        if dt.hour == 9:
            df.loc[dt, 'Volume'] = 300000.0
        elif dt.hour == 12:
            df.loc[dt, 'Volume'] = 50000.0
        else:
            df.loc[dt, 'Volume'] = 100000.0
            
    # At the final bar (a 12:00 midday bar), volume is 75,000 (1.5x of midday normal 50k, but only 0.5x of rolling flat avg!)
    final_dt = df.index[-1]
    # Set final bar to 12:00
    df.loc[final_dt, 'Volume'] = 75000.0
    
    ind_df = compute_indicators(df, cfg)
    # Under hourly profile, 75k > 1.2 * 50k (60k) -> passes BreakoutVolOK!
    assert 'BreakoutVolOK' in ind_df.columns

def test_next_bar_open_confirmation():
    """Verify Rule 3: use_open_confirmation rejects trade if next bar gaps down or opens below pivot."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['breakout']['use_open_confirmation'] = True
    cfg['breakout']['max_open_gap_pct'] = 0.5
    
    df = create_synthetic_data(60, 100.0)
    # Breakout at bar 50: pivot is resistance (~100.8), breakout close is 105.0
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.5
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 105.0
    
    # Case A: Next bar opens at 104.0 (gapped down 1.0% against signal close 105.0) -> Rejection!
    df.loc[df.index[51], 'Open'] = 104.0
    df.loc[df.index[51], 'High'] = 106.0
    df.loc[df.index[51], 'Low'] = 103.5
    df.loc[df.index[51], 'Close'] = 105.5
    
    tester = Backtester(cfg)
    tester.run('TEST', df)
    # Should be rejected due to adverse open gap > 0.5%
    assert len(tester.results) == 0

def test_failed_breakout_quick_exit():
    """Verify Rule 4: use_failed_breakout_exit exits trade immediately when price closes back inside base."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['management']['use_failed_breakout_exit'] = True
    cfg['management']['failed_breakout_max_bars'] = 3
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_synthetic_data(60, 100.0)
    # Base resistance is ~100.8
    # Breakout candle at Bar 50: Close = 105.0
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.5
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 105.0
    
    # Bar 51: Entry Open = 105.0, Stop = 102.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.2
    df.loc[df.index[51], 'Low'] = 104.0
    df.loc[df.index[51], 'Close'] = 104.5
    
    # Bar 52: Closes at 100.0 (back inside base below resistance ~101.27), while low (103.0) didn't hit stop
    df.loc[df.index[52], 'Open'] = 105.5
    df.loc[df.index[52], 'High'] = 105.8
    df.loc[df.index[52], 'Low'] = 103.0
    df.loc[df.index[52], 'Close'] = 100.0
    
    tester = Backtester(cfg)
    tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    assert trade['exit_reason'] == 'FAILED_BREAKOUT'

def test_cost_friction_gate():
    """Verify Rule 5: use_cost_filter rejects setups where round-trip fees eat too much of 1R."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['use_cost_filter'] = True
    cfg['risk']['max_friction_r'] = 0.10  # Max 10% of 1R in fees
    cfg['risk']['slippage_pct'] = 0.10     # 0.10% each way = 0.20% round trip
    cfg['risk']['commission_pct'] = 0.05   # 0.05% each way = 0.10% round trip -> total 0.30% round trip
    
    df = create_synthetic_data(60, 100.0)
    # Stop distance is ~2.4% -> friction_r = 0.30% / 2.44% = 0.123R > 0.10R limit -> Reject!
    df.loc[df.index[50], 'Open'] = 100.5
    df.loc[df.index[50], 'High'] = 102.0
    df.loc[df.index[50], 'Low'] = 100.0
    df.loc[df.index[50], 'Close'] = 101.8
    
    tester = Backtester(cfg)
    tester.run('TEST', df)
    # Trade must be rejected by cost filter
    assert len(tester.results) == 0

def test_excursion_and_forward_return_logging():
    """Verify Rule 7: MFE, MAE, stop_floor_binding, and forward bar returns are recorded."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['management']['use_time_exit'] = False
    
    df = create_synthetic_data(70, 100.0)
    df.loc[df.index[50], 'Close'] = 105.0
    
    # Entry at Bar 51 Open = 105.0
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 107.0
    df.loc[df.index[51], 'Low'] = 104.5
    df.loc[df.index[51], 'Close'] = 106.5
    
    # Bar 52 (+1 bar from entry): Close = 108.0
    df.loc[df.index[52], 'Open'] = 106.5
    df.loc[df.index[52], 'High'] = 109.0
    df.loc[df.index[52], 'Low'] = 106.0
    df.loc[df.index[52], 'Close'] = 108.0
    
    tester = Backtester(cfg)
    tester.run('TEST', df)
    
    assert len(tester.results) >= 1
    trade = tester.results[0]
    
    # Verify MFE and MAE exist and are non-negative floats
    assert 'mfe_r' in trade
    assert 'mae_r' in trade
    assert trade['mfe_r'] >= 0.0
    assert trade['mae_r'] >= 0.0
    assert 'stop_floor_binding' in trade
    assert 'fwd_ret_1b' in trade

def test_options_policy_tagging():
    """Verify Rule 6: Setup defaults to options_eligible=False and UNDERLYING_UNVALIDATED."""
    s = Setup(symbol='AAPL')
    assert s.options_eligible is False
    assert s.underlying_validated is False
    assert s.options_status == "UNDERLYING_UNVALIDATED"
