import pytest
import pandas as pd
import numpy as np
from breakout_scanner.models import Setup, State
from breakout_scanner.engine import BreakoutEngine, compute_indicators, compute_engine_trace


def get_test_cfg():
    return {
        "base": {
            "min_setup_bars": 3,
            "base_lookback": 20,
            "tight_lookback": 5,
            "max_base_range_pct": 10.0,
            "max_tight_range_pct": 5.0,
            "tightening_threshold": 0.70,
            "min_box_density_pct": 75.0,
        },
        "breakout": {
            "buffer_pct": 0.10,
            "close_location": 0.70,
            "min_body_pct": 0.50,
            "require_momentum_candle": False,
            "volume_multiplier": 1.5,
        },
        "volume": {
            "breakout_multiplier": 1.20,
            "volume_length": 20,
            "contraction_ratio": 0.80,
            "use_breakout_volume": True,
            "use_volume_contraction": False,
        },
        "trend": {
            "sma_length": 20,
            "use_trend_filter": True,
            "use_slope_filter": False,
        },
        "risk": {
            "atr_length": 14,
            "atr_multiplier": 1.5,
            "min_stop_pct": 0.5,
            "max_stop_pct": 5.0,
            "structure_buffer_pct": 0.10,
            "use_structure_stop": True,
        },
        "target": {
            "target_lookback": 20,
            "tp1_r": 1.0,
            "tp2_r": 2.0,
            "tp3_r": 3.0,
        },
        "management": {
            "trail_after_r": 1.0,
        },
        "signal_quality": {
            "require_clv_confirmation": False,
            "min_clv_threshold": 0.65,
        },
    }


def build_base_dataframe(n=50, price=100.0, volume=1000):
    """Creates a basic valid OHLCV dataframe with SMA, trend, and base formation."""
    dates = pd.date_range("2026-11-01 09:30:00", periods=n, freq="1h", tz="America/New_York")
    df = pd.DataFrame({
        "Open": [price] * n,
        "High": [price + 1.0] * n,
        "Low": [price - 1.0] * n,
        "Close": [price] * n,
        "Volume": [volume] * n,
    }, index=dates)
    return df


def test_contraction_with_close_below_resistance_reports_bull_break():
    """
    Test (a): CONTRACTION setup where close is below resistance*(1+buffer)
    must report first_failed_gate='bull_break', NOT hardcoded 'breakout_volume'.
    """
    cfg = get_test_cfg()
    df = build_base_dataframe(n=40, price=98.0, volume=1000)
    # Lower SMA history so LongTrendOK is True
    df.loc[df.index[:30], "Close"] = 90.0
    df.loc[df.index[:30], "High"] = 91.0
    df.loc[df.index[:30], "Low"] = 89.0

    df = compute_indicators(df, cfg)
    assert bool(df["LongTrendOK"].iloc[-1]) is True

    setup = Setup("TEST")
    setup.state = State.CONTRACTION
    setup.resistance = 100.0
    setup.support = 95.0
    setup.cons_index = 0
    setup.tightening_confirmed = True
    setup.cons_density_confirmed = True

    trace = compute_engine_trace(df, setup, cfg, initial_state=State.CONTRACTION)

    assert trace["first_failed_gate"] == "bull_break", f"Expected bull_break, got: {trace['first_failed_gate']}"
    assert "bull_break" in trace["all_failed_gates"]
    assert trace["gate_details"]["gate"] == "bull_break"
    assert trace["dist_to_pivot_pct"] is not None
    # 98.0 vs pivot 100.0 * 1.001 = 100.10 -> (100.10 - 98.0) / 98.0 * 100 = 2.14% -> round 2.1%
    assert round(trace["dist_to_pivot_pct"], 1) == 2.1


def test_contraction_with_trend_false_reports_trend():
    """
    Test (b): CONTRACTION setup where trend is False (close < SMA)
    must report first_failed_gate='trend', because trend precedes bull_break in pipeline order.
    """
    cfg = get_test_cfg()
    df = build_base_dataframe(n=40, price=100.0, volume=1000)
    df.loc[df.index[-1], "Close"] = 90.0
    df.loc[df.index[-1], "Open"] = 90.0
    df.loc[df.index[-1], "High"] = 91.0
    df.loc[df.index[-1], "Low"] = 89.0

    df = compute_indicators(df, cfg)
    assert bool(df["LongTrendOK"].iloc[-1]) is False

    setup = Setup("TEST")
    setup.state = State.CONTRACTION
    setup.resistance = 100.0
    setup.support = 95.0
    setup.cons_index = 0
    setup.tightening_confirmed = True
    setup.cons_density_confirmed = True

    trace = compute_engine_trace(df, setup, cfg, initial_state=State.CONTRACTION)
    assert trace["first_failed_gate"] == "trend", f"Expected trend, got: {trace['first_failed_gate']}"
    assert "trend" in trace["all_failed_gates"]


def test_reported_volume_threshold_equals_cfg_breakout_multiplier():
    """
    Test (c): Reported volume threshold in gate_details / trace must equal
    cfg['volume']['breakout_multiplier'] (e.g. 1.20), not hardcoded 1.5.
    """
    cfg = get_test_cfg()
    df = build_base_dataframe(n=40, price=95.0, volume=1000)
    # Breakout bar: close above resistance 100.0 * 1.001 = 100.10
    df.loc[df.index[-1], "Open"] = 99.0
    df.loc[df.index[-1], "High"] = 102.0
    df.loc[df.index[-1], "Low"] = 98.0
    df.loc[df.index[-1], "Close"] = 101.5
    df.loc[df.index[-1], "Volume"] = 500  # 500 < 1.20 * 1000

    df = compute_indicators(df, cfg)
    assert "BreakoutVolBenchmark" in df.columns

    setup = Setup("TEST")
    setup.state = State.CONTRACTION
    setup.resistance = 100.0
    setup.support = 95.0
    setup.cons_index = 0
    setup.tightening_confirmed = True
    setup.cons_density_confirmed = True

    trace = compute_engine_trace(df, setup, cfg, initial_state=State.CONTRACTION)
    assert trace["first_failed_gate"] == "volume", f"Expected volume, got: {trace['first_failed_gate']}"
    assert trace["gate_details"]["threshold"] == 1.20, f"Expected 1.20, got: {trace['gate_details']['threshold']}"


def test_synthetic_frame_firing_long_signal_gives_first_failed_gate_none():
    """
    Test (d): A synthetic frame that satisfies all 8 long_signal conditions
    must yield first_failed_gate='none' and all_failed_gates=[] in compute_engine_trace.
    """
    cfg = get_test_cfg()
    cfg["breakout"]["require_momentum_candle"] = True
    cfg["signal_quality"]["require_clv_confirmation"] = True
    cfg["signal_quality"]["min_clv_threshold"] = 0.65

    df = build_base_dataframe(n=40, price=95.0, volume=1000)
    # Final breakout bar:
    df.loc[df.index[-1], "Open"] = 99.0
    df.loc[df.index[-1], "Low"] = 98.8
    df.loc[df.index[-1], "High"] = 102.0
    df.loc[df.index[-1], "Close"] = 101.8  # Strong bull close near high (CLV > 0.90)
    df.loc[df.index[-1], "Volume"] = 2000  # 2.0x > 1.20x benchmark

    df = compute_indicators(df, cfg)

    setup = Setup("TEST")
    setup.state = State.CONTRACTION
    setup.resistance = 100.0
    setup.support = 95.0
    setup.cons_index = 0
    setup.tightening_confirmed = True
    setup.cons_density_confirmed = True

    trace = compute_engine_trace(df, setup, cfg, initial_state=State.CONTRACTION)
    assert trace["first_failed_gate"] == "none", f"Expected none, got: {trace['first_failed_gate']}"
    assert trace["all_failed_gates"] == []
    assert trace["gate_details"] is None
