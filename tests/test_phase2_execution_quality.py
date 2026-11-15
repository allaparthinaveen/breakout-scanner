"""
Phase 2: Signal Quality Execution & Noise Reduction Test Suite (tests/test_phase2_execution_quality.py)

Validates:
1. Close Location Value (CLV) wick rejection filter (shooting stars rejected, upper closes approved)
2. Base Piercing / False Breakout Memory rejection
3. Anti-Chase / Extension Guard in PortfolioGuard
4. Control Group preservation (Golden Rule: default flags preserve 100% baseline behavior)
"""

import pytest
import pandas as pd
import numpy as np

from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.models import Setup, State
from breakout_scanner.portfolio_guard import DailyRiskBudgetEngine, PositionSizer, CorrelationStackingGuard
from breakout_scanner.signal_quality import compute_candle_close_location, compute_failed_breakouts_in_base


def create_consolidation_df(n_bars: int = 60, price: float = 100.0) -> pd.DataFrame:
    """Helper to generate a clean flat consolidation dataset."""
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    df = pd.DataFrame({
        "Open": price,
        "High": price + 1.0,
        "Low": price - 1.0,
        "Close": price,
        "Volume": 1000.0
    }, index=dates)
    return df


def test_clv_shooting_star_rejection():
    """Verify that a breakout candle with a heavy upper wick (CLV < 0.65) is rejected when require_clv_confirmation=True."""
    cfg = load_config("config/default.yaml")
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['signal_quality']['require_clv_confirmation'] = True
    cfg['signal_quality']['min_clv_threshold'] = 0.65

    df = create_consolidation_df(60, 100.0)
    # Bar 50 attempts breakout above 101.0, but closes near the low (Shooting Star: High 106, Close 101.5, Low 100.5 -> CLV = 0.18)
    df.loc[df.index[50], "Open"] = 101.0
    df.loc[df.index[50], "High"] = 106.0
    df.loc[df.index[50], "Low"] = 100.5
    df.loc[df.index[50], "Close"] = 101.5

    engine = BreakoutEngine(cfg, "TEST_SHOOTING_STAR")
    setup = engine.run(df.iloc[:51])

    # Must NOT be BUY because CLV failed the 0.65 threshold
    assert setup.state != State.BUY


def test_clv_strong_upper_close_approval():
    """Verify that a breakout candle closing in the top 20% of its range (CLV >= 0.80) is approved."""
    cfg = load_config("config/default.yaml")
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['signal_quality']['require_clv_confirmation'] = True
    cfg['signal_quality']['min_clv_threshold'] = 0.65

    df = create_consolidation_df(60, 100.0)
    # Bar 50: Strong institutional thrust closing near the high (High 105.0, Close 104.8, Low 100.5 -> CLV = 0.95)
    df.loc[df.index[50], "Open"] = 101.0
    df.loc[df.index[50], "High"] = 105.0
    df.loc[df.index[50], "Low"] = 100.5
    df.loc[df.index[50], "Close"] = 104.8

    engine = BreakoutEngine(cfg, "TEST_STRONG_CLOSE")
    setup = engine.run(df.iloc[:51])

    assert setup.state == State.BUY
    assert setup.direction == 1
    assert setup.entry == pytest.approx(104.8, abs=0.01)


def test_pierced_base_rejection():
    """Verify that a base with excessive failed piercings (> max_allowed) is disqualified."""
    cfg = load_config("config/default.yaml")
    cfg['volume']['use_breakout_volume'] = False
    cfg['trend']['use_trend_filter'] = False
    cfg['base']['require_tightening'] = False
    cfg['signal_quality']['max_failed_breakouts_in_base'] = 2

    df = create_consolidation_df(60, 100.0)
    resistance = 101.0

    # Create 3 prior failed piercing attempts in the base
    df.loc[df.index[30], "High"] = 103.0
    df.loc[df.index[30], "Close"] = 100.5
    df.loc[df.index[35], "High"] = 103.5
    df.loc[df.index[35], "Close"] = 100.2
    df.loc[df.index[40], "High"] = 104.0
    df.loc[df.index[40], "Close"] = 100.8

    # Bar 50: Breakout attempt
    df.loc[df.index[50], "Open"] = 101.0
    df.loc[df.index[50], "High"] = 105.0
    df.loc[df.index[50], "Low"] = 100.5
    df.loc[df.index[50], "Close"] = 104.5

    engine = BreakoutEngine(cfg, "TEST_PIERCED")
    # Manually attach failed piercings count to setup
    engine.setup.failed_breakouts_in_base = 3
    setup = engine.run(df.iloc[:51])

    # Should be rejected because failed piercings (3) > max allowed (2)
    assert setup.state != State.BUY


def test_anti_chase_extension_guard_portfolio_suppression():
    """Verify that an extended trade (> 1.0% or > 0.5 ATR from entry) is suppressed by DailyRiskBudgetEngine as LATE_CHASE."""
    cfg = load_config("config/default.yaml")
    cfg['portfolio_guard']['enabled'] = True

    guard = DailyRiskBudgetEngine(cfg)

    # Extended trade: Entry was 100.0, current price is 103.0 (+3.0% extended)
    extended_setup = Setup("EXTENDED_STOCK")
    extended_setup.state = State.BUY
    extended_setup.direction = 1
    extended_setup.entry = 100.0
    extended_setup.current_price = 103.0
    extended_setup.stop = 96.0
    extended_setup.tp1 = 104.0
    extended_setup.tp2 = 108.0
    extended_setup.composite_score = 90
    extended_setup.composite_grade = "A+"
    extended_setup.atr = 2.0

    assert extended_setup.is_fresh_entry is False
    assert extended_setup.trade_status_label == "LATE_CHASE"

    slate = guard.allocate_slate([extended_setup])

    # Trade MUST be suppressed with LATE_CHASE reason
    assert len(slate.actionable_trades) == 0
    assert len(slate.suppressed_trades) == 1
    assert "LATE_CHASE" in slate.suppressed_trades[0].reason


def test_fresh_candidate_approved_by_portfolio_guard():
    """Verify that a fresh trade within pivot tolerance (+0.2% from entry) is APPROVED by DailyRiskBudgetEngine."""
    cfg = load_config("config/default.yaml")
    cfg['portfolio_guard']['enabled'] = True

    guard = DailyRiskBudgetEngine(cfg)

    fresh_setup = Setup("FRESH_STOCK")
    fresh_setup.state = State.BUY
    fresh_setup.direction = 1
    fresh_setup.entry = 100.0
    fresh_setup.current_price = 100.2  # +0.2% from entry
    fresh_setup.stop = 96.0
    fresh_setup.tp1 = 104.0
    fresh_setup.tp2 = 108.0
    fresh_setup.composite_score = 92
    fresh_setup.composite_grade = "A+"
    fresh_setup.atr = 2.0

    assert fresh_setup.is_fresh_entry is True
    assert fresh_setup.trade_status_label == "CONFIRMED"

    slate = guard.allocate_slate([fresh_setup])

    # Trade MUST be approved
    assert len(slate.actionable_trades) == 1
    assert slate.actionable_trades[0].status == "APPROVED"
    assert slate.actionable_trades[0].symbol == "FRESH_STOCK"


def test_control_group_unmodified():
    """Assert that default config runs with 100% control behavior (all Phase 2 flags disabled by default)."""
    cfg = load_config("config/default.yaml")
    assert cfg['signal_quality']['enabled'] is False
    assert cfg['signal_quality']['require_clv_confirmation'] is False
    assert cfg['signal_quality']['close_location_weight'] == 0.0
    assert cfg['signal_quality']['vdu_slope_weight'] == 0.0
    assert cfg['signal_quality']['failed_breakout_penalty_weight'] == 0.0
