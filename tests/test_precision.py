import numpy as np
import pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import Setup, State
from breakout_scanner.sectors import get_sector_benchmark

def test_sector_benchmark():
    # Test NSE mapping
    sec, bench = get_sector_benchmark('TCS.NS')
    assert sec == 'IT'
    assert bench == '^CNXIT'
    
    sec_bank, bench_bank = get_sector_benchmark('HDFCBANK.NS')
    assert sec_bank == 'Banking'
    assert bench_bank == '^NSEBANK'
    
    # Test US mapping
    sec_nvda, bench_nvda = get_sector_benchmark('NVDA')
    assert sec_nvda == 'Semis'
    assert bench_nvda == 'SMH'
    
    # Test Crypto mapping
    sec_crypto, bench_crypto = get_sector_benchmark('SOL-USD')
    assert sec_crypto == 'Crypto'
    assert bench_crypto == 'BTC-USD'
    
    # Test generic fallback
    sec_fb, bench_fb = get_sector_benchmark('UNKNOWN.NS')
    assert sec_fb == 'NSE Market'
    assert bench_fb == '^NSEI'

def test_setup_precision_defaults():
    s = Setup('AAPL')
    assert s.htf_trend == 0
    assert s.htf_confirmed is False
    assert s.sector_name is None
    assert s.sector_rs_pct is None
    assert s.breakout_type == "BREAKOUT"
    assert s.retest_confirmed is False
    assert s.breakout_level is None
    assert s.breakout_score == 0
    assert s.decay_ratio is None
    assert s.tightening_confirmed is False
    assert s.rs_slope is None
    assert s.rs_trend == ""

def test_empty_and_short_df_guard():
    cfg = load_config()
    engine = BreakoutEngine(cfg, 'TEST')
    
    # Empty DataFrame
    s_empty = engine.run(pd.DataFrame())
    assert s_empty.state == State.IDLE
    
    # None DataFrame
    s_none = engine.run(None)
    assert s_none.state == State.IDLE
    
    # Short DataFrame (< 10 bars)
    short_df = pd.DataFrame({
        'Open': [100.0] * 5,
        'High': [101.0] * 5,
        'Low': [99.0] * 5,
        'Close': [100.5] * 5,
        'Volume': [1000] * 5
    })
    s_short = engine.run(short_df)
    assert s_short.state == State.IDLE

def test_risk_engine_safe_calculations():
    from breakout_scanner.risk import RiskEngine
    cfg = load_config()
    risk = RiskEngine(cfg)
    
    s = Setup('TEST', state=State.BUY, direction=1, entry=100.0, stop=98.0)
    # Stop loss calculation shouldn't crash with None breakout_level
    sl = risk.calculate_stop_loss(s, None, 0, method='baseline')
    assert sl > 0
    
    # Box invalidation method
    s.support = 97.0
    s.resistance = 103.0
    sl_box = risk.calculate_stop_loss(s, None, 0, method='box_invalidation')
    assert sl_box > 0
    
    # Position sizing shouldn't crash on invalid/zero inputs
    size_zero_entry = risk.calculate_position_size(capital=10000.0, entry=0.0, stop=98.0, current_positions=0)
    assert size_zero_entry == 0.0
    
    size_normal = risk.calculate_position_size(capital=10000.0, entry=100.0, stop=98.0, current_positions=0)
    assert size_normal > 0

def test_extended_sector_mappings():
    # Test new sector mappings
    sec_amd, bench_amd = get_sector_benchmark('AMD')
    assert sec_amd == 'Semis'
    assert bench_amd == 'SMH'
    
    sec_goog, bench_goog = get_sector_benchmark('GOOGL')
    assert bench_goog == 'XLC'
    
    sec_xom, bench_xom = get_sector_benchmark('XOM')
    assert sec_xom == 'Energy'
    assert bench_xom == 'XLE'
    
    sec_cost, bench_cost = get_sector_benchmark('COST')
    assert bench_cost == 'XLP'

def test_vdu_and_rvol_precision():
    cfg = load_config()
    engine = BreakoutEngine(cfg, 'TEST')
    
    # 80 bars: flat consolidation base with volume drying up in the last 5 bars
    n = 80
    close = [100.0] * n
    high = [101.0] * n
    low = [99.0] * n
    open_p = [100.0] * n
    volume = [1000.0] * 75 + [300.0] * 5
    
    df = pd.DataFrame({
        'Open': open_p,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': volume
    })
    
    setup = engine.run(df)
    assert setup.state == State.CONTRACTION
    assert setup.vdu_confirmed is True
    assert setup.vdu_ratio is not None
    assert setup.vdu_ratio <= 0.75

def test_decaying_contraction_ratio():
    cfg = load_config()
    engine = BreakoutEngine(cfg, 'TEST')
    
    # Construct 80 bars:
    # First 60 bars: trend above SMA50
    # Next 15 bars: wider base swing (High: 104, Low: 99 -> ~5% range)
    # Final 5 bars: tight coil contraction (High: 101.5, Low: 100.5 -> ~1% range)
    # decay_ratio = tight_range / earlier_range = 1% / 5% = 0.20 <= 0.70
    n = 80
    close = [100.0] * 60 + [101.5] * 15 + [101.0] * 5
    high = [101.0] * 60 + [104.0] * 15 + [101.5] * 5
    low = [99.0] * 60 + [99.0] * 15 + [100.5] * 5
    open_p = [100.0] * n
    volume = [1000.0] * 75 + [300.0] * 5
    
    df = pd.DataFrame({
        'Open': open_p,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': volume
    })
    
    setup = engine.run(df)
    assert setup.state == State.CONTRACTION
    assert setup.decay_ratio is not None
    assert setup.decay_ratio <= 0.70
    assert setup.tightening_confirmed is True

def test_rs_momentum_slope():
    from breakout_scanner.scanner import MultiSymbolScanner
    from breakout_scanner.providers.yfinance_provider import YFinanceProvider
    
    cfg = load_config()
    provider = YFinanceProvider()
    scanner = MultiSymbolScanner(provider, cfg)
    
    # 20 bars of outperforming stock vs flat benchmark
    stock_ret = pd.Series([1.0 + i * 0.01 for i in range(20)])  # upward trending
    bench_ret = pd.Series([1.0] * 20)  # flat benchmark
    rs_ratio = (stock_ret / bench_ret).dropna()
    
    tail = rs_ratio.iloc[-5:]
    x = np.arange(len(tail))
    slope, _ = np.polyfit(x, tail.values, 1)
    rs_slope = round(float(slope * 100.0), 2)
    rs_trend = "↗" if rs_slope > 0.05 else ("↘" if rs_slope < -0.05 else "→")
    
    assert rs_slope > 0
    assert rs_trend == "↗"
    
    # Lagging/underperforming stock
    stock_lag = pd.Series([1.0 - i * 0.01 for i in range(20)])
    rs_lag = (stock_lag / bench_ret).dropna()
    tail_lag = rs_lag.iloc[-5:]
    slope_lag, _ = np.polyfit(x, tail_lag.values, 1)
    rs_slope_lag = round(float(slope_lag * 100.0), 2)
    rs_trend_lag = "↗" if rs_slope_lag > 0.05 else ("↘" if rs_slope_lag < -0.05 else "→")
    
    assert rs_slope_lag < 0
    assert rs_trend_lag == "↘"

