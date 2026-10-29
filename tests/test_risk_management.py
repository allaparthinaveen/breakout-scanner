import pytest
import pandas as pd
import numpy as np
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import State, Setup
from breakout_scanner.risk import RiskEngine
from breakout_scanner.backtester import Backtester

def create_base_df(n_bars=60, base_high=102.0, base_low=98.0):
    """Generate consolidation bars with explicit support and resistance."""
    dates = pd.date_range('2024-01-01', periods=n_bars, freq='D')
    
    mid = (base_high + base_low) / 2.0
    half_range = (base_high - base_low) / 2.0
    
    closes = np.full(n_bars, mid)
    opens = closes - 0.1
    highs = closes + half_range
    lows = closes - half_range
    volume = np.full(n_bars, 100000.0)
    
    df = pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volume
    }, index=dates)
    return df

def test_structural_stop_not_clamped_into_noise():
    """Verify that a structural stop at 4.2% is preserved and NOT clamped to an artificial 3%."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['max_stop_pct'] = 5.00
    cfg['risk']['structure_buffer_pct'] = 0.25
    
    # Base: Support at 96.0, Resistance at 100.0
    df = create_base_df(60, base_high=100.0, base_low=96.0)
    
    # Bar 50: Breakout candle at Close = 100.5
    df.loc[df.index[50], 'Open'] = 99.5
    df.loc[df.index[50], 'High'] = 101.0
    df.loc[df.index[50], 'Low'] = 99.0
    df.loc[df.index[50], 'Close'] = 100.5
    df.loc[df.index[50], 'Volume'] = 500000.0
    
    engine = BreakoutEngine(cfg, 'TEST')
    setup = engine.run(df)
    
    assert setup.state == State.BUY
    # Support is 96.0 -> structure_stop = 96.0 * (1 - 0.0025) = 95.76
    expected_stop = 96.0 * (1.0 - 0.0025)
    assert setup.stop == pytest.approx(expected_stop, rel=1e-3)
    # The stop MUST NOT be clamped to 3% (100.5 * 0.97 = 97.485)!
    assert setup.stop < 97.0
    assert setup.risk_pct > 3.0  # Risk is ~4.7%
    assert setup.risk_pct <= 5.0

def test_setup_rejected_when_structural_risk_exceeds_max_stop():
    """Verify that setups with excessive structural risk (> max_stop_pct) are rejected, not clamped."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['risk']['max_stop_pct'] = 5.00  # Max allowable risk 5%
    cfg['risk']['structure_buffer_pct'] = 0.25
    
    # Very wide base: Support at 92.0, Resistance at 100.0 (risk > 8%)
    df = create_base_df(60, base_high=100.0, base_low=92.0)
    
    # Bar 50: Breakout candle at 100.5
    df.loc[df.index[50], 'Open'] = 99.5
    df.loc[df.index[50], 'High'] = 101.0
    df.loc[df.index[50], 'Low'] = 98.0
    df.loc[df.index[50], 'Close'] = 100.5
    df.loc[df.index[50], 'Volume'] = 500000.0
    
    engine = BreakoutEngine(cfg, 'TEST')
    setup = engine.run(df)
    
    # Setup must be rejected because base is too wide and loose!
    assert setup.state == State.IDLE

def test_dynamic_position_sizing_locks_dollar_risk():
    """Verify that position sizing dynamically locks dollar loss to exactly risk_pct (1.0%)."""
    cfg = load_config()
    cfg['risk']['risk_per_trade_pct'] = 1.0  # 1.0% risk
    cfg['risk']['max_exposure_pct'] = 50.0   # Allow up to 50% notional for this test
    risk_eng = RiskEngine(cfg)
    
    capital = 100000.0
    expected_dollar_risk = 1000.0  # 1% of 100k
    
    # Case A: Narrow Stop (2% risk -> $2.00 distance on $100 stock)
    entry_a = 100.0
    stop_a = 98.0
    shares_a = risk_eng.calculate_position_size(capital, entry_a, stop_a)
    assert shares_a == pytest.approx(500.0)
    actual_dollar_risk_a = shares_a * (entry_a - stop_a)
    assert actual_dollar_risk_a == pytest.approx(expected_dollar_risk)
    
    # Case B: Wide Stop (5% risk -> $5.00 distance on $100 stock)
    entry_b = 100.0
    stop_b = 95.0
    shares_b = risk_eng.calculate_position_size(capital, entry_b, stop_b)
    assert shares_b == pytest.approx(200.0)
    actual_dollar_risk_b = shares_b * (entry_b - stop_b)
    assert actual_dollar_risk_b == pytest.approx(expected_dollar_risk)
    
    # Both trades risk the exact same $1,000 cash!
    assert actual_dollar_risk_a == pytest.approx(actual_dollar_risk_b)

def test_risk_engine_validation_method():
    """Verify RiskEngine.validate_structural_risk logic."""
    cfg = load_config()
    cfg['risk']['max_stop_pct'] = 5.0
    cfg['risk']['min_stop_pct'] = 0.75
    risk_eng = RiskEngine(cfg)
    
    # Valid 3.0% risk
    is_valid, pct, reason = risk_eng.validate_structural_risk(100.0, 97.0)
    assert is_valid is True
    assert pct == pytest.approx(3.0)
    assert reason == "VALID"
    
    # Invalid 7.0% risk (exceeds 5.0%)
    is_valid_wide, pct_wide, reason_wide = risk_eng.validate_structural_risk(100.0, 93.0)
    assert is_valid_wide is False
    assert pct_wide == pytest.approx(7.0)
    assert "EXCEEDS_MAX_RISK" in reason_wide
