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
