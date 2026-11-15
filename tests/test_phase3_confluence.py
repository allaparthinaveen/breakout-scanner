"""
Phase 3: Confluence & Context Layer Test Suite.
Validates:
1. Multi-TF 4H Structure & Trend Alignment
2. Broad Market Volatility Regime (Bull Expansion vs High-Vol Selloff)
3. Earnings Proximity Calendar Check
4. Additive Scorer Behavior (default weight 0 = zero regression, active weights = score modulation)
"""

import datetime
import pytest
import pandas as pd
import numpy as np

from breakout_scanner.models import Setup, State
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.confluence import (
    compute_4h_confluence,
    compute_market_regime,
    check_earnings_proximity,
    enrich_confluence_and_context,
    is_non_equity_symbol
)


def test_4h_confluence_bullish_and_bearish():
    """Verify that synthesized 4H candles accurately detect trend structure."""
    # 120 hourly bars: steady upward trend
    dates = pd.date_range("2026-01-01", periods=120, freq="1h")
    closes = np.linspace(100.0, 150.0, 120)
    highs = closes + 1.0
    lows = closes - 1.0
    opens = closes - 0.5
    vols = [1000] * 120

    df_up = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
    }, index=dates)

    trend_up, confirmed_up = compute_4h_confluence(df_up, direction=1, sma_len=10)
    assert trend_up == 1
    assert confirmed_up is True

    # Bearish downward trend
    closes_down = np.linspace(150.0, 100.0, 120)
    df_down = pd.DataFrame({
        "Open": closes_down + 0.5, "High": closes_down + 1.0, "Low": closes_down - 1.0, "Close": closes_down, "Volume": vols
    }, index=dates)

    trend_down, confirmed_down = compute_4h_confluence(df_down, direction=-1, sma_len=10)
    assert trend_down == -1
    assert confirmed_down is True


def test_market_regime_bull_vs_high_vol_selloff():
    """Verify market regime classification on benchmark ETF."""
    dates = pd.date_range("2026-01-01", periods=70, freq="1d")

    # Bullish regime: price above upward-sloping 50 SMA with normal volatility
    closes_bull = np.linspace(400.0, 480.0, 70)
    proxy_bull = pd.DataFrame({
        "Open": closes_bull, "High": closes_bull + 2.0, "Low": closes_bull - 2.0, "Close": closes_bull, "Volume": [10000] * 70
    }, index=dates)

    regime_bull, fav_bull = compute_market_regime(proxy_bull, sma_len=50)
    assert regime_bull == "BULL_EXPANSION"
    assert fav_bull is True

    # High-volatility selloff: sharp collapse below 50 SMA with expanding ATR
    closes_bear = np.concatenate([np.linspace(450.0, 460.0, 55), np.linspace(450.0, 380.0, 15)])
    highs_bear = closes_bear + np.concatenate([[2.0] * 55, [15.0] * 15])  # Huge ATR spike
    lows_bear = closes_bear - np.concatenate([[2.0] * 55, [15.0] * 15])
    proxy_selloff = pd.DataFrame({
        "Open": closes_bear, "High": highs_bear, "Low": lows_bear, "Close": closes_bear, "Volume": [20000] * 70
    }, index=dates)

    regime_bear, fav_bear = compute_market_regime(proxy_selloff, sma_len=50)
    assert regime_bear in ("HIGH_VOL_SELLOFF", "BEAR_TREND")
    assert fav_bear is False


def test_earnings_proximity_check():
    """Verify calculation of days until earnings and imminent flag."""
    today = datetime.datetime.now(datetime.timezone.utc).date()

    # Imminent earnings in 2 days
    cal_imminent = {"Earnings Date": [(today + datetime.timedelta(days=2))]}
    days, imminent = check_earnings_proximity("TEST", calendar_data=cal_imminent)
    assert days == 2
    assert imminent is True

    # Distant earnings in 25 days
    cal_distant = {"Earnings Date": [(today + datetime.timedelta(days=25))]}
    days_dist, imminent_dist = check_earnings_proximity("TEST", calendar_data=cal_distant)
    assert days_dist == 25
    assert imminent_dist is False


def test_scorer_phase3_zero_weight_golden_rule_invariant():
    """
    CRITICAL GOLDEN RULE CHECK:
    Verify that calculate_composite_score produces the EXACT same score
    when context_cfg is None or when weights are 0.0.
    """
    s = Setup("MSFT")
    s.base_width_pct = 3.5
    s.decay_ratio = 0.50
    s.tightening_confirmed = True
    s.vdu_confirmed = True
    s.vdu_ratio = 0.65
    s.state = State.CONTRACTION
    s.htf_4h_confirmed = True
    s.market_regime = "HIGH_VOL_SELLOFF"
    s.earnings_warning = True

    score_base, grade_base = calculate_composite_score(s, quality_cfg=None, context_cfg=None)

    disabled_cfg = {
        "enabled": False,
        "htf_4h_weight": 0.0,
        "market_regime_weight": 0.0,
        "earnings_risk_weight": 0.0
    }
    score_disabled, grade_disabled = calculate_composite_score(s, quality_cfg=None, context_cfg=disabled_cfg)

    assert score_base == score_disabled
    assert grade_base == grade_disabled


def test_scorer_phase3_active_weights_modifiers():
    """Verify that active Phase 3 weights modulate score additively."""
    # Setup with 4H confluence
    s = Setup("AAPL")
    s.state = State.BUY
    s.base_width_pct = 3.0
    s.decay_ratio = 0.45
    s.tightening_confirmed = True
    s.vdu_confirmed = True
    s.vdu_ratio = 0.60
    s.htf_4h_confirmed = True
    s.market_regime = "NORMAL"
    s.earnings_warning = False

    score_unweighted, _ = calculate_composite_score(s, context_cfg={"enabled": False})

    weighted_cfg = {
        "enabled": True,
        "htf_4h_weight": 1.0,
        "market_regime_weight": 1.0,
        "earnings_risk_weight": 1.0
    }
    score_boosted, _ = calculate_composite_score(s, context_cfg=weighted_cfg)

    # Should gain +5 pts for 4H confirmation
    assert score_boosted == score_unweighted + 5

    # Flawed macro setup: high vol selloff + earnings warning
    s_macro_drag = Setup("DRAG")
    s_macro_drag.state = State.BUY
    s_macro_drag.base_width_pct = 3.0
    s_macro_drag.decay_ratio = 0.45
    s_macro_drag.htf_4h_confirmed = False
    s_macro_drag.market_regime = "HIGH_VOL_SELLOFF"
    s_macro_drag.earnings_warning = True

    score_drag_base, _ = calculate_composite_score(s_macro_drag, context_cfg={"enabled": False})
    score_drag_penalized, _ = calculate_composite_score(s_macro_drag, context_cfg=weighted_cfg)

    # Should be penalized (-8 for high-vol selloff, -5 for earnings warning = -13 pts)
    assert score_drag_penalized == max(0, score_drag_base - 13)


def test_is_non_equity_symbol_classification():
    """Verify that macro, future, forex, index, and crypto symbols are correctly identified."""
    # Futures & Commodities
    assert is_non_equity_symbol("GC=F") is True
    assert is_non_equity_symbol("SI=F") is True
    assert is_non_equity_symbol("CL=F") is True
    assert is_non_equity_symbol("BZ=F") is True
    assert is_non_equity_symbol("NG=F") is True

    # Forex
    assert is_non_equity_symbol("GBPUSD=X") is True
    assert is_non_equity_symbol("USDJPY=X") is True
    assert is_non_equity_symbol("AUDUSD=X") is True
    assert is_non_equity_symbol("CAD=X") is True

    # Indices
    assert is_non_equity_symbol("^FTSE") is True
    assert is_non_equity_symbol("^GDAXI") is True
    assert is_non_equity_symbol("^DJI") is True
    assert is_non_equity_symbol("^NDX") is True
    assert is_non_equity_symbol("^HSI") is True

    # Crypto
    assert is_non_equity_symbol("BTC-USD") is True
    assert is_non_equity_symbol("ETH-USDT") is True

    # Equities (should be False)
    assert is_non_equity_symbol("AAPL") is False
    assert is_non_equity_symbol("NVDA") is False
    assert is_non_equity_symbol("RELIANCE.NS") is False
    assert is_non_equity_symbol("") is False
    assert is_non_equity_symbol(None) is False


def test_non_equity_skips_earnings_proximity():
    """Verify that non-equity instruments bypass earnings check without calling yfinance."""
    for sym in ["GC=F", "SI=F", "CL=F", "^NDX", "^DJI", "GBPUSD=X"]:
        days, imminent = check_earnings_proximity(sym)
        assert days is None
        assert imminent is False

