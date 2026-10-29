import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from breakout_scanner.providers.validation import validate_and_clean_candles, parse_timeframe_delta
from breakout_scanner.models import Setup, State
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.sectors import get_sector_benchmark


def test_validation_chronological_sort_and_dedup():
    dates = [
        pd.Timestamp('2026-01-01 10:00:00'),
        pd.Timestamp('2026-01-01 09:00:00'),  # Out of order
        pd.Timestamp('2026-01-01 10:00:00'),  # Duplicate
        pd.Timestamp('2026-01-01 11:00:00'),
    ]
    df = pd.DataFrame({
        'Open': [100.0, 99.0, 100.5, 101.0],
        'High': [102.0, 101.0, 103.0, 104.0],
        'Low': [99.0, 98.0, 99.5, 100.0],
        'Close': [101.0, 100.0, 102.0, 103.0],
        'Volume': [1000, 1500, 2000, 2500]
    }, index=dates)

    cleaned = validate_and_clean_candles(df, symbol="TEST", min_bars=2)
    assert len(cleaned) == 3
    assert cleaned.index.is_monotonic_increasing
    # Verify the duplicate at 10:00 kept the latest record (Open 100.5, High 103.0)
    assert cleaned.loc[pd.Timestamp('2026-01-01 10:00:00')]['Open'] == 100.5


def test_validation_geometric_ohlc_integrity():
    dates = pd.date_range('2026-01-01', periods=4, freq='h')
    df = pd.DataFrame({
        'Open': [100.0, 100.0, 100.0, 100.0],
        'High': [105.0, 95.0, 105.0, 105.0],   # Row 1: High < Low (invalid!)
        'Low':  [95.0, 100.0, 95.0, 95.0],
        'Close': [102.0, 98.0, 110.0, 102.0],  # Row 2: Close > High (invalid!)
        'Volume': [1000, 1000, 1000, -500]    # Row 3: Negative volume (invalid!)
    }, index=dates)

    cleaned = validate_and_clean_candles(df, symbol="TEST", min_bars=1)
    # Only the first bar is completely valid
    assert len(cleaned) == 1
    assert cleaned.index[0] == dates[0]


def test_confirmed_candle_policy():
    now_utc = datetime.now(timezone.utc)
    # Create 3 bars: bar 0 is 2 hours ago (closed), bar 1 is 1 hour ago (closed), bar 2 is now (in-progress!)
    t0 = now_utc - timedelta(hours=2)
    t1 = now_utc - timedelta(hours=1)
    t2 = now_utc - timedelta(minutes=10) # 10 mins into the current hour -> still open!
    
    dates = [t0, t1, t2]
    df = pd.DataFrame({
        'Open': [100.0, 101.0, 102.0],
        'High': [102.0, 103.0, 104.0],
        'Low': [99.0, 100.0, 101.0],
        'Close': [101.0, 102.0, 103.0],
        'Volume': [1000, 1200, 500]
    }, index=dates)

    # Without enforcement: returns all 3 bars
    unfiltered = validate_and_clean_candles(df, enforce_closed_candle=False, timeframe='1h')
    assert len(unfiltered) == 3

    # With confirmed candle policy: drops incomplete bar 2
    filtered = validate_and_clean_candles(df, enforce_closed_candle=True, timeframe='1h')
    assert len(filtered) == 2
    assert filtered.index[-1] == t1


def test_bidirectional_composite_scoring_for_sell():
    # Construct a high-conviction Short setup (SELL)
    # Downside alignment: 1H trend is down (-1), 1D macro is down (-1), RS is lagging (-3.5% and slope ↘)
    setup_short = Setup(
        symbol="BEAR-STOCK",
        state=State.SELL,
        direction=-1,
        trend=-1,
        htf_trend=-1,
        htf_confirmed=True,
        sector_rs_pct=-3.5,
        rs_trend="↘",
        base_width_pct=3.0,
        decay_ratio=0.35,
        tightening_confirmed=True,
        rvol=2.2,
        risk_pct=1.0,
        entry=100.0,
        stop=101.0,
        current_price=99.5
    )

    score, grade = calculate_composite_score(setup_short)
    # Should receive top score across all 5 pillars because it is aligned with Short direction!
    assert score >= 90
    assert grade == "A+"


def test_nse_sector_mappings_refinement():
    # Verify Financial Services NBFCs are mapped to Nifty Financial Services
    sec, bench = get_sector_benchmark("BAJFINANCE.NS")
    assert sec == "Fin Services"
    assert bench == "NIFTY_FIN_SERVICE.NS"

    sec, bench = get_sector_benchmark("LICI.NS")
    assert sec == "Fin Services"
    assert bench == "NIFTY_FIN_SERVICE.NS"

    # Verify Banks remain mapped to Bank Nifty
    sec, bench = get_sector_benchmark("HDFCBANK.NS")
    assert sec == "Banking"
    assert bench == "^NSEBANK"

    # Verify Power Utilities are mapped to Infrastructure/Power
    sec, bench = get_sector_benchmark("NTPC.NS")
    assert sec == "Utilities"
    assert bench == "^CNXINFRA"

    sec, bench = get_sector_benchmark("POWERGRID.NS")
    assert sec == "Utilities"
    assert bench == "^CNXINFRA"


def test_provider_enforce_closed_candle_default():
    """Verify Audit Fix 4: YFinanceProvider defaults to enforce_closed_candle = True."""
    from breakout_scanner.providers.yfinance_provider import YFinanceProvider
    provider = YFinanceProvider()
    assert provider.enforce_closed_candle is True


def test_sma50_slope_check_and_cache_ttl():
    """Verify Audit Fix 4:
    1. SMA50 slope check: Price > SMA50 only confirms HTF if SMA50[-1] > SMA50[-5].
    2. Cache TTL: Invalidates cached entries when elapsed time exceeds 4 hours or day advances.
    """
    from breakout_scanner.scanner import MultiSymbolScanner
    from breakout_scanner.models import Setup, State
    from breakout_scanner.config import load_config
    from unittest.mock import MagicMock

    cfg = load_config()
    mock_provider = MagicMock()
    mock_provider.daily_cache = {}
    mock_provider.daily_cache_ts = {}
    mock_provider.market_cache = {}
    mock_provider.market_cache_ts = {}

    scanner = MultiSymbolScanner(mock_provider, cfg)

    # 1. Downward sloping SMA50:
    # 60 bars where prices in the past were high (200.0) and steadily fell to 100.0
    # Price bounces to 102.0 (above SMA50 which is ~101.5), but SMA50[-1] < SMA50[-5]
    dates = pd.date_range('2024-01-01', periods=60, freq='D')
    declining_prices = np.linspace(150.0, 95.0, 60)
    declining_prices[-1] = 130.0  # bounce above SMA50 (~118.5), but SMA50[-1] < SMA50[-5]
    declining_df = pd.DataFrame({
        'Open': declining_prices,
        'High': declining_prices + 1.0,
        'Low': declining_prices - 1.0,
        'Close': declining_prices,
        'Volume': np.full(60, 50000.0)
    }, index=dates)

    mock_provider.candles.return_value = declining_df

    setup_down = Setup(symbol="BOUNCE.NS", state=State.BUY, direction=1, trend=1)
    df_1h = pd.DataFrame({'Close': [105.0] * 25}, index=pd.date_range('2024-03-01 09:15', periods=25, freq='h'))
    scanner.enrich_bias(setup_down, df_1h, period='60d', interval='1h')

    # Since SMA50[-1] < SMA50[-5], macro slope is falling -> htf_confirmed must be FALSE!
    assert setup_down.htf_confirmed is False
    assert setup_down.htf_trend == 0

    # 2. Upward sloping SMA50:
    # Prices steadily rose from 80.0 to 120.0
    rising_prices = np.linspace(80.0, 120.0, 60)
    rising_df = pd.DataFrame({
        'Open': rising_prices,
        'High': rising_prices + 1.0,
        'Low': rising_prices - 1.0,
        'Close': rising_prices,
        'Volume': np.full(60, 50000.0)
    }, index=dates)

    mock_provider.daily_cache.clear()
    mock_provider.daily_cache_ts.clear()
    mock_provider.candles.return_value = rising_df

    setup_up = Setup(symbol="UPTREND.NS", state=State.BUY, direction=1, trend=1)
    scanner.enrich_bias(setup_up, df_1h, period='60d', interval='1h')

    # SMA50[-1] > SMA50[-5] and Close > SMA50 -> htf_confirmed must be TRUE!
    assert setup_up.htf_confirmed is True
    assert setup_up.htf_trend == 1

    # 3. Cache TTL: simulate cache set 5 hours ago
    stale_time = datetime.now(timezone.utc) - timedelta(hours=5)
    mock_provider.daily_cache_ts["UPTREND.NS"] = stale_time

    # Calling enrich_bias again should detect stale cache (> 4 hrs) and trigger a re-download
    call_count_before = mock_provider.candles.call_count
    scanner.enrich_bias(setup_up, df_1h, period='60d', interval='1h')
    assert mock_provider.candles.call_count > call_count_before

