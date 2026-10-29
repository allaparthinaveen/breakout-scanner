import pytest
import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.backtester import Backtester
from breakout_scanner.engine import compute_indicators

def create_synthetic_data(n_bars=60, base_price=100.0):
    """Generate consolidation bars with standard structure."""
    dates = pd.date_range('2024-01-01', periods=n_bars, freq='D')
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

def test_p0_stop_loss_books_full_1r_loss():
    """
    Verify Fix for P0 Bug 1 (Half-position exit bug):
    When a trade hits Stop Loss before TP1 is hit, the loss must be calculated on
    100% of the position (shares), NOT 50% (shares_remaining).
    Realized R must be ~ -1.0R (plus slippage/commissions), NOT ~ -0.5R.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['slippage_pct'] = 0.0
    cfg['risk']['commission_pct'] = 0.0
    
    df = create_synthetic_data(60, 100.0)
    
    # Breakout candle at Bar 50
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    
    # Bar 51: Entry Open = 105.0. Stop = ~102.0 (Risk = ~3.0 per share)
    df.loc[df.index[51], 'Open'] = 105.0
    df.loc[df.index[51], 'High'] = 105.5
    df.loc[df.index[51], 'Low'] = 104.0
    df.loc[df.index[51], 'Close'] = 104.5
    
    # Bar 52: Plunges straight into Stop Loss (Low = 101.0) without touching TP1
    df.loc[df.index[52], 'Open'] = 104.0
    df.loc[df.index[52], 'High'] = 104.2
    df.loc[df.index[52], 'Low'] = 101.0  # triggers stop loss
    df.loc[df.index[52], 'Close'] = 101.5
    
    tester = Backtester(cfg)
    tester.run('TEST', df)
    
    assert len(tester.results) == 1
    trade = tester.results[0]
    
    assert trade['exit_reason'] == 'STOP_LOSS'
    assert trade['tp1_hit'] is False
    # Realized R must be ~ -1.0R (full stop), NOT ~ -0.5R
    assert trade['realized_r'] == pytest.approx(-1.0, abs=0.05)
    # Gross loss must equal full risk_cash
    assert abs(trade['gross_pnl']) == pytest.approx(trade['risk_cash'], rel=1e-2)

def test_p0_trend_filter_active_when_enabled():
    """
    Verify Fix for P0 Bug 2 (~ bitwise boolean bug):
    When use_trend_filter=True, setups below 50 SMA MUST be rejected.
    Previously, ~c_trend['use_trend_filter'] evaluated to -2 (truthy), allowing all counter-trend signals.
    """
    cfg = load_config()
    cfg['trend']['use_trend_filter'] = True
    cfg['volume']['use_breakout_volume'] = False
    cfg['base']['require_tightening'] = False
    
    # Create downtrend data: Close steadily falling below SMA50
    df = create_synthetic_data(60, 100.0)
    # Force SMA50 to be higher (e.g. 110) while current prices are at 95
    df['Close'] = 95.0
    df['Open'] = 95.0
    df['High'] = 96.0
    df['Low'] = 94.0
    
    ind_df = compute_indicators(df, cfg)
    
    # LongTrendOK must be FALSE for all bars because price (95) < SMA (100)
    assert not ind_df['LongTrendOK'].iloc[-1]
    
    # Now set use_trend_filter to False: LongTrendOK must be TRUE
    cfg['trend']['use_trend_filter'] = False
    ind_df_no_filter = compute_indicators(df, cfg)
    assert ind_df_no_filter['LongTrendOK'].iloc[-1]

def test_p0_volume_filter_active_when_enabled():
    """
    Verify Fix for P0 Bug 2 (~ bitwise boolean bug on volume):
    When use_breakout_volume=True, breakouts with low volume must have BreakoutVolOK=False.
    """
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = True
    cfg['volume']['breakout_multiplier'] = 1.5
    
    df = create_synthetic_data(60, 100.0)
    # Ensure volume on latest candle is low (50000 vs 100000 avg)
    df.loc[df.index[-1], 'Volume'] = 50000.0
    
    ind_df = compute_indicators(df, cfg)
    assert not ind_df['BreakoutVolOK'].iloc[-1]
    
    # When disabled, BreakoutVolOK must be True
    cfg['volume']['use_breakout_volume'] = False
    ind_df_disabled = compute_indicators(df, cfg)
    assert ind_df_disabled['BreakoutVolOK'].iloc[-1]

def test_p0_momentum_candle_filter_active_when_enabled():
    """
    Verify Fix for P0 Bug 2 (~ bitwise boolean bug on momentum candle body):
    When require_momentum_candle=True, small-bodied candles must not pass BullMom.
    """
    cfg = load_config()
    cfg['breakout']['require_momentum_candle'] = True
    cfg['breakout']['min_body_pct'] = 50.0
    
    df = create_synthetic_data(60, 100.0)
    # Create doji / small body candle: High 105, Low 95 (range 10), Open 100.0, Close 100.5 (body 0.5 = 5%)
    last_idx = df.index[-1]
    df.loc[last_idx, 'Open'] = 100.0
    df.loc[last_idx, 'High'] = 105.0
    df.loc[last_idx, 'Low'] = 95.0
    df.loc[last_idx, 'Close'] = 100.5
    
    ind_df = compute_indicators(df, cfg)
    assert not ind_df['BullMom'].iloc[-1]
    
    # When require_momentum_candle is False, body requirement is bypassed
    cfg['breakout']['require_momentum_candle'] = False
    ind_df_disabled = compute_indicators(df, cfg)
    # Close > Open and close location still check
    assert ind_df_disabled['BullMom'].iloc[-1] == (
        (ind_df_disabled['Close'].iloc[-1] > ind_df_disabled['Open'].iloc[-1]) and
        (ind_df_disabled['BullCloseLoc'].iloc[-1] >= cfg['breakout']['close_location'])
    )
