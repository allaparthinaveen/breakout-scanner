import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine, is_finite_num
from breakout_scanner.models import Setup, State
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.providers.yfinance_provider import YFinanceProvider

def create_base_data(n_bars=60, base_price=100.0):
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

def test_consolidation_quality_gate_passes_clean_base():
    """Verify that a clean consolidation with >= 75% closes inside base confirms and triggers breakout."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['base']['min_box_density_pct'] = 75.0
    
    df = create_base_data(60, 100.0)
    
    # Bar 50: Breakout candle
    df.loc[df.index[50], 'Open'] = 101.0
    df.loc[df.index[50], 'High'] = 105.0
    df.loc[df.index[50], 'Low'] = 100.5
    df.loc[df.index[50], 'Close'] = 104.8
    df.loc[df.index[50], 'Volume'] = 200000.0
    
    engine = BreakoutEngine(cfg, 'CLEAN_TEST')
    setup = engine.run(df.iloc[:51])
    
    assert setup.state == State.BUY
    assert setup.cons_density_confirmed is True
    assert setup.cons_density_pct >= 75.0

def test_consolidation_quality_gate_rejects_erratic_chop():
    """Verify that erratic chop (>25% closes outside base) fails the quality gate and rejects the setup."""
    cfg = load_config()
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['base']['min_box_density_pct'] = 75.0
    
    df = create_base_data(60, 100.0)
    
    # Establish base around 100 (support ~99.2, resistance ~100.8)
    # Then introduce erratic swings outside base prior to breakout
    # Bars 30 to 45: Whipping between 95 and 115
    for bar_idx in range(30, 45):
        if bar_idx % 2 == 0:
            df.loc[df.index[bar_idx], 'Close'] = 93.0
            df.loc[df.index[bar_idx], 'Open'] = 94.0
            df.loc[df.index[bar_idx], 'Low'] = 92.0
            df.loc[df.index[bar_idx], 'High'] = 95.0
        else:
            df.loc[df.index[bar_idx], 'Close'] = 112.0
            df.loc[df.index[bar_idx], 'Open'] = 110.0
            df.loc[df.index[bar_idx], 'High'] = 113.0
            df.loc[df.index[bar_idx], 'Low'] = 109.0
            
    engine = BreakoutEngine(cfg, 'CHOP_TEST')
    setup = engine.run(df)
    
    # Setup should NOT be in BUY state because erratic chop invalidates the base
    assert setup.state != State.BUY

def test_nan_guards_in_composite_scorer():
    """Verify that composite scorer does not assign phantom points or raise exceptions on NaN values."""
    setup = Setup('NAN_TEST')
    setup.base_width_pct = float('nan')
    setup.decay_ratio = float('nan')
    setup.vdu_ratio = float('nan')
    setup.rvol = float('nan')
    setup.sector_rs_pct = float('nan')
    setup.risk_pct = float('nan')
    setup.entry = float('nan')
    setup.current_price = float('nan')
    setup.trend = 0
    setup.htf_trend = 0
    
    score, grade = calculate_composite_score(setup)
    
    assert isinstance(score, int)
    assert not np.isnan(score)
    assert 0 <= score <= 100
    assert grade in ('A+', 'A', 'B', 'C')
    # Because all data is NaN, score should be zero (no fallback points awarded)
    assert score == 0

def test_sorting_with_nans_does_not_crash_or_poison():
    """Verify sorting setups with NaN values produces deterministic results."""
    s1 = Setup('S1')
    s1.composite_score = 85
    s1.entry = 100.0
    s1.current_price = 100.5
    
    s2 = Setup('S2')
    s2.composite_score = 90
    s2.entry = float('nan')
    s2.current_price = float('nan')
    
    s3 = Setup('S3')
    s3.composite_score = 70
    s3.entry = 50.0
    s3.current_price = 50.2

    setups = [s1, s2, s3]
    
    def safe_sort(s):
        score = s.composite_score if is_finite_num(s.composite_score) else 0
        if is_finite_num(s.entry) and is_finite_num(s.current_price) and s.entry > 0:
            prox = -abs(s.entry - s.current_price) / s.entry
            if not is_finite_num(prox):
                prox = -999.0
        else:
            prox = -999.0
        return (score, prox)

    setups.sort(key=safe_sort, reverse=True)
    
    # S2 has score 90, so it must be first; S1 (85) second; S3 (70) third
    assert [s.symbol for s in setups] == ['S2', 'S1', 'S3']

def test_yfinance_provider_retries_on_failure():
    """Verify YFinanceProvider retries with exponential backoff before succeeding."""
    provider = YFinanceProvider(enforce_closed_candle=False, max_retries=3)
    
    sample_df = pd.DataFrame({
        'Open': [100.0, 101.0],
        'High': [102.0, 103.0],
        'Low': [99.0, 100.0],
        'Close': [101.0, 102.0],
        'Volume': [1000, 2000]
    }, index=pd.date_range('2024-01-01', periods=2, freq='D'))
    
    # Mock yf.download to fail first 2 times, then succeed on 3rd attempt
    call_count = 0
    def mock_download(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Temporary network glitch")
        return sample_df

    with patch('yfinance.download', side_effect=mock_download):
        with patch('time.sleep', return_value=None):  # fast test
            df = provider.candles('RETRY_TEST', period='2d', interval='1d')
            assert call_count == 3
            assert not df.empty
            assert len(df) == 2
