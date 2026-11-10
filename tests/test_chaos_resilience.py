"""
Phase 5 — Chaos & Data-Failure Resilience Test Suite
=====================================================
Verifies graceful degradation and zero unhandled exceptions across adversarial
data conditions: missing bars, zero-volume candles, partial frames, extreme ATR
spikes, negative prices, NaN floods, and simulated API rate-limit failures.

Golden Rule guarantee: engine and scorer must NEVER raise an unhandled exception.
They may return State.IDLE or a default score when data is invalid.
"""

import pytest
import numpy as np
import pandas as pd
from copy import deepcopy

from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine, compute_indicators
from breakout_scanner.backtester import Backtester
from breakout_scanner.models import State, Setup
from breakout_scanner.scorer import calculate_composite_score


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def make_normal_df(n_bars: int = 80, price: float = 100.0, volume: float = 100_000.0) -> pd.DataFrame:
    """Generate a clean, sane OHLCV DataFrame."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    noise = rng.normal(0, 0.3, n_bars)
    close = np.maximum(price + np.cumsum(noise), 0.01)
    high = close + abs(rng.normal(0, 0.15, n_bars))
    low = close - abs(rng.normal(0, 0.15, n_bars))
    open_ = close - rng.normal(0, 0.1, n_bars)
    vol = np.full(n_bars, volume)
    return pd.DataFrame({
        "Open": open_, "High": high, "Low": np.minimum(low, open_),
        "Close": close, "Volume": vol,
    }, index=dates)


def make_engine() -> BreakoutEngine:
    cfg = load_config()
    cfg["trend"]["use_trend_filter"] = False
    cfg["volume"]["use_breakout_volume"] = False
    cfg["base"]["require_tightening"] = False
    return BreakoutEngine(cfg, "TEST_CHAOS")


# ─────────────────────────────────────────────
# Test 1 — Minimum bar threshold (< min_bars)
# ─────────────────────────────────────────────

def test_too_few_bars_returns_idle():
    """Engine must not raise when given fewer bars than the lookback requires."""
    engine = make_engine()
    df_tiny = make_normal_df(n_bars=5)
    try:
        result = engine.run(df_tiny)
        assert result.state == State.IDLE
    except Exception as exc:
        pytest.fail(f"Engine raised exception on tiny DataFrame: {exc}")


# ─────────────────────────────────────────────
# Test 2 — Empty DataFrame
# ─────────────────────────────────────────────

def test_empty_dataframe_returns_idle():
    """Engine must not raise when given an empty DataFrame."""
    engine = make_engine()
    df_empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    try:
        result = engine.run(df_empty)
        assert result.state == State.IDLE
    except Exception as exc:
        pytest.fail(f"Engine raised exception on empty DataFrame: {exc}")


# ─────────────────────────────────────────────
# Test 3 — All-zero volume (holiday / halt)
# ─────────────────────────────────────────────

def test_zero_volume_bars_no_crash():
    """Engine must handle zero-volume candles (e.g. holiday sessions) gracefully."""
    engine = make_engine()
    df = make_normal_df(n_bars=60)
    df["Volume"] = 0.0  # All bars: zero volume
    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on zero-volume bars: {exc}")


# ─────────────────────────────────────────────
# Test 4 — Partial NaN flood (missing bars)
# ─────────────────────────────────────────────

def test_nan_flood_no_crash():
    """Engine must survive a large proportion of NaN values in OHLCV data."""
    engine = make_engine()
    df = make_normal_df(n_bars=80)

    # Flood 30% of rows with NaN close prices (simulating data gaps)
    nan_idx = np.random.default_rng(7).choice(len(df), size=24, replace=False)
    df.iloc[nan_idx, df.columns.get_loc("Close")] = np.nan
    df.iloc[nan_idx, df.columns.get_loc("High")] = np.nan
    df.iloc[nan_idx, df.columns.get_loc("Low")] = np.nan

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on NaN-flooded DataFrame: {exc}")


# ─────────────────────────────────────────────
# Test 5 — Extreme ATR spike (flash crash / gap)
# ─────────────────────────────────────────────

def test_extreme_atr_spike_no_crash():
    """Engine must handle an extreme single-bar ATR spike (flash crash / circuit breaker event)."""
    engine = make_engine()
    df = make_normal_df(n_bars=80)

    # Insert a single catastrophic gap-down bar at index 60
    df.iloc[60, df.columns.get_loc("Open")] = 100.0
    df.iloc[60, df.columns.get_loc("High")] = 100.0
    df.iloc[60, df.columns.get_loc("Low")] = 1.0     # 99% plunge
    df.iloc[60, df.columns.get_loc("Close")] = 5.0
    df.iloc[60, df.columns.get_loc("Volume")] = 50_000_000.0

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on extreme ATR spike: {exc}")


# ─────────────────────────────────────────────
# Test 6 — Negative prices (data corruption)
# ─────────────────────────────────────────────

def test_negative_prices_no_crash():
    """Engine must survive corrupted data with negative prices without raising."""
    engine = make_engine()
    df = make_normal_df(n_bars=60)
    df.iloc[30:35, df.columns.get_loc("Close")] = -5.0
    df.iloc[30:35, df.columns.get_loc("Low")] = -10.0

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on negative price data: {exc}")


# ─────────────────────────────────────────────
# Test 7 — Flat/constant price (zero range)
# ─────────────────────────────────────────────

def test_flat_price_no_crash():
    """Engine must handle perfect flat-line data (zero candle range) without division-by-zero."""
    engine = make_engine()
    n = 60
    flat_price = 100.0
    df = pd.DataFrame({
        "Open": np.full(n, flat_price),
        "High": np.full(n, flat_price),
        "Low": np.full(n, flat_price),
        "Close": np.full(n, flat_price),
        "Volume": np.full(n, 100_000.0),
    }, index=pd.date_range("2024-01-01", periods=n, freq="h"))

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on flat-price data: {exc}")


# ─────────────────────────────────────────────
# Test 8 — All-NaN volume column
# ─────────────────────────────────────────────

def test_all_nan_volume_no_crash():
    """Engine must handle an all-NaN volume column without raising."""
    engine = make_engine()
    df = make_normal_df(n_bars=60)
    df["Volume"] = np.nan

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on all-NaN volume: {exc}")


# ─────────────────────────────────────────────
# Test 9 — Backtester robustness on adversarial data
# ─────────────────────────────────────────────

def test_backtester_nan_df_no_crash():
    """Backtester must return empty metrics instead of raising on NaN-heavy data."""
    cfg = load_config()
    cfg["trend"]["use_trend_filter"] = False
    cfg["volume"]["use_breakout_volume"] = False
    bt = Backtester(cfg)

    df = make_normal_df(n_bars=60)
    df.iloc[20:50, df.columns.get_loc("Close")] = np.nan

    try:
        result = bt.run("CHAOS_TEST", df)
        assert isinstance(result, dict)
        # Must return trades key even if zero
        assert "trades" in result
    except Exception as exc:
        pytest.fail(f"Backtester raised exception on NaN data: {exc}")


# ─────────────────────────────────────────────
# Test 10 — Scorer on incomplete/default Setup
# ─────────────────────────────────────────────

def test_scorer_on_default_setup_no_crash():
    """Scorer must not raise on a minimal/default Setup with all fields at defaults."""
    setup = Setup(symbol="CHAOS")
    try:
        score, grade = calculate_composite_score(setup)
        assert isinstance(score, int)
        assert grade in ("A+", "A", "B", "C", "")
    except Exception as exc:
        pytest.fail(f"Scorer raised exception on default Setup: {exc}")


# ─────────────────────────────────────────────
# Test 11 — compute_indicators on minimal valid df
# ─────────────────────────────────────────────

def test_compute_indicators_minimal_df():
    """compute_indicators must not raise on a very short but valid DataFrame."""
    cfg = load_config()
    df = make_normal_df(n_bars=10)
    try:
        result = compute_indicators(df, cfg)
        assert "SMA" in result.columns
        assert "ATR" in result.columns
    except Exception as exc:
        pytest.fail(f"compute_indicators raised exception on minimal df: {exc}")


# ─────────────────────────────────────────────
# Test 12 — Missing OHLCV columns
# ─────────────────────────────────────────────

def test_missing_columns_no_crash():
    """Engine must not raise on DataFrames missing expected OHLCV columns."""
    engine = make_engine()

    # DataFrame with only Close and Volume
    df_minimal = pd.DataFrame({
        "Close": np.random.uniform(90, 110, 60),
        "Volume": np.full(60, 50_000.0),
    }, index=pd.date_range("2024-01-01", periods=60, freq="h"))

    try:
        result = engine.run(df_minimal)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on missing-column DataFrame: {exc}")


# ─────────────────────────────────────────────
# Test 13 — Simultaneous missing bars + extreme spike
# ─────────────────────────────────────────────

def test_combined_chaos_stress():
    """Combined chaos test: NaN gaps + flat zones + ATR spike + zero volume in same dataset."""
    engine = make_engine()
    df = make_normal_df(n_bars=100)

    # Gap zones
    df.iloc[10:15, df.columns.get_loc("Close")] = np.nan
    df.iloc[10:15, df.columns.get_loc("Volume")] = 0.0

    # Flat zone
    df.iloc[40:50, df.columns.get_loc("High")] = 100.0
    df.iloc[40:50, df.columns.get_loc("Low")] = 100.0
    df.iloc[40:50, df.columns.get_loc("Open")] = 100.0
    df.iloc[40:50, df.columns.get_loc("Close")] = 100.0

    # Flash crash spike
    df.iloc[70, df.columns.get_loc("Low")] = 0.01
    df.iloc[70, df.columns.get_loc("Close")] = 0.5

    try:
        result = engine.run(df)
        assert result.state in (State.IDLE, State.CONTRACTION, State.BUY, State.SELL, State.FAILED)
    except Exception as exc:
        pytest.fail(f"Engine raised exception on combined chaos data: {exc}")
