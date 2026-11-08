"""
Phase 2: Signal Quality & Core Noise Reduction Test Suite.
Validates:
1. Breakout Candle Close Location (wick rejection filter)
2. Intra-Base Volume Dry-Up Slope (last 1/3 vs first 1/3 volume decay)
3. Distance-to-Trigger & Continuous Tightness Scoring
4. Base Piercing & Failed Breakout Memory
5. Additive Scorer Behavior (default weight 0 = zero regression, active weights = score modulation)
"""

import pytest
import pandas as pd
import numpy as np

from breakout_scanner.models import Setup, State
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.signal_quality import (
    compute_candle_close_location,
    compute_vdu_slope,
    compute_distance_to_trigger,
    compute_failed_breakouts_in_base,
    enrich_signal_quality
)


def test_candle_close_location_long():
    """Verify upper close confirmation and shooting star wick detection for long setups."""
    # Strong institutional close in top 10% of candle
    df_bullish = pd.DataFrame([
        {"High": 110.0, "Low": 100.0, "Close": 109.0, "Volume": 1000}
    ])
    loc, confirmed = compute_candle_close_location(df_bullish, direction=1)
    assert loc == 0.90
    assert confirmed is True

    # Shooting star / upper wick rejection in bottom 20%
    df_shooting_star = pd.DataFrame([
        {"High": 110.0, "Low": 100.0, "Close": 102.0, "Volume": 1000}
    ])
    loc, confirmed = compute_candle_close_location(df_shooting_star, direction=1)
    assert loc == 0.20
    assert confirmed is False


def test_candle_close_location_short():
    """Verify symmetrical close location calculation for short setups."""
    df_bearish = pd.DataFrame([
        {"High": 110.0, "Low": 100.0, "Close": 101.0, "Volume": 1000}
    ])
    loc, confirmed = compute_candle_close_location(df_bearish, direction=-1)
    assert loc == 0.90
    assert confirmed is True


def test_vdu_slope_decay_confirmed():
    """Verify that volume systematically decaying into base apex is confirmed."""
    # 20 bars: initial volume 1000, final volume 400
    vols = [1000] * 7 + [700] * 6 + [400] * 7
    df = pd.DataFrame({"Volume": vols})

    slope, confirmed = compute_vdu_slope(df, base_lookback=20)
    assert slope == pytest.approx(0.40, abs=0.05)
    assert confirmed is True


def test_vdu_slope_expanding_unconfirmed():
    """Verify that expanding volume inside base is not confirmed."""
    vols = [400] * 7 + [700] * 6 + [1200] * 7
    df = pd.DataFrame({"Volume": vols})

    slope, confirmed = compute_vdu_slope(df, base_lookback=20)
    assert slope > 1.0
    assert confirmed is False


def test_distance_to_trigger_and_tightness():
    """Verify continuous distance to trigger and tightness confirmation."""
    s = Setup("TIGHT_TEST")
    s.state = State.CONTRACTION
    s.direction = 1
    s.resistance = 100.0
    s.base_width_pct = 3.2  # Ultra-tight
    s.current_price = 99.6  # 0.4% from resistance

    dist, confirmed = compute_distance_to_trigger(s, s.current_price)
    assert dist == 0.40
    assert confirmed is True

    # Wide base should NOT get tightness confirmation even if close to pivot
    s_loose = Setup("LOOSE_TEST")
    s_loose.state = State.CONTRACTION
    s_loose.direction = 1
    s_loose.resistance = 100.0
    s_loose.base_width_pct = 9.5
    s_loose.current_price = 99.6

    dist_loose, confirmed_loose = compute_distance_to_trigger(s_loose, s_loose.current_price)
    assert dist_loose == 0.40
    assert confirmed_loose is False


def test_failed_breakouts_in_base_counter():
    """Verify counting of wick pierces that fail back into base."""
    # 5 bars: bars 1 and 3 pierce resistance (100.0) but close below
    df = pd.DataFrame([
        {"High": 99.0, "Close": 98.5},
        {"High": 102.0, "Close": 99.5},  # Pierce 1
        {"High": 99.5, "Close": 99.0},
        {"High": 101.5, "Close": 99.8},  # Pierce 2
        {"High": 99.8, "Close": 99.5},
    ])
    count = compute_failed_breakouts_in_base(df, resistance=100.0, support=95.0, base_lookback=5, direction=1)
    assert count == 2


def test_scorer_zero_weight_golden_rule_invariant():
    """
    CRITICAL GOLDEN RULE CHECK:
    Verify that calculate_composite_score produces the EXACT same score
    when quality_cfg is None or when weights are 0.0.
    """
    s = Setup("AAPL")
    s.base_width_pct = 3.5
    s.decay_ratio = 0.50
    s.tightening_confirmed = True
    s.vdu_confirmed = True
    s.vdu_ratio = 0.65
    s.state = State.CONTRACTION

    score_base, grade_base = calculate_composite_score(s, quality_cfg=None)

    disabled_cfg = {
        "enabled": False,
        "close_location_weight": 0.0,
        "vdu_slope_weight": 0.0,
        "trigger_tightness_weight": 0.0,
        "failed_breakout_penalty_weight": 0.0
    }
    score_disabled, grade_disabled = calculate_composite_score(s, quality_cfg=disabled_cfg)

    assert score_base == score_disabled
    assert grade_base == grade_disabled


def test_scorer_active_weights_bonus_and_penalty():
    """Verify that enabling weights adjusts score additively without breaking bounds."""
    # Pristine setup: upper close, VDU slope confirmed, tight trigger
    s_clean = Setup("CLEAN")
    s_clean.state = State.BUY
    s_clean.base_width_pct = 3.0
    s_clean.decay_ratio = 0.45
    s_clean.tightening_confirmed = True
    s_clean.vdu_confirmed = True
    s_clean.vdu_ratio = 0.60
    s_clean.candle_close_loc = 0.85
    s_clean.candle_close_confirmed = True
    s_clean.failed_breakouts_in_base = 0

    score_unweighted, _ = calculate_composite_score(s_clean, quality_cfg={"enabled": False})

    weighted_cfg = {
        "enabled": True,
        "close_location_weight": 1.0,
        "vdu_slope_weight": 1.0,
        "trigger_tightness_weight": 1.0,
        "failed_breakout_penalty_weight": 1.0
    }
    score_boosted, _ = calculate_composite_score(s_clean, quality_cfg=weighted_cfg)

    # Should gain +5 points for confirmed upper close
    assert score_boosted == score_unweighted + 5

    # Flawed setup: heavy upper wick rejection and 2 failed pierces
    s_flawed = Setup("FLAWED")
    s_flawed.state = State.BUY
    s_flawed.base_width_pct = 8.0
    s_flawed.decay_ratio = 0.75
    s_flawed.candle_close_loc = 0.25  # Weak wick
    s_flawed.candle_close_confirmed = False
    s_flawed.failed_breakouts_in_base = 2  # Churned pivot

    score_flawed_base, _ = calculate_composite_score(s_flawed, quality_cfg={"enabled": False})
    score_flawed_penalized, _ = calculate_composite_score(s_flawed, quality_cfg=weighted_cfg)

    # Should be penalized (-5 for weak close, -10 for 2 failed breakouts)
    assert score_flawed_penalized < score_flawed_base
    assert score_flawed_penalized == max(0, score_flawed_base - 15)
