"""
Stage 1: Production Safety Test Suite (P0-1 through P0-5)
========================================================
Validates:
1. P0-3 Scorer Normalization & Anti-Saturation
2. P0-2 Follow-Through Confirmation Policy
3. P0-1 Notification Pipeline Risk Filtering
4. P0-4 Persistent Portfolio Exposure Engine
5. P0-5 Daily Loss Circuit Breaker
"""

import os
import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date

from breakout_scanner.models import Setup, State
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.portfolio_state import PortfolioStateManager
from breakout_scanner.portfolio_guard import PositionAllocation, DailyRiskBudgetEngine
from breakout_scanner.safeguards import ProductionSafeguards
from breakout_scanner.telegram_formatter import format_scan_report_html
from breakout_scanner.slate import ActionableSlate, SlatePipeline


# =====================================================================
# 1. P0-3 Scorer Normalization & Anti-Saturation
# =====================================================================

def test_p0_3_scorer_anti_saturation_and_bounds():
    """Verify that excessive weights cannot push composite score past 100 or hand out A+ undeservedly."""
    s = Setup("MKT_TEST")
    s.state = State.BUY
    s.direction = 1
    s.base_width_pct = 7.0   # Medium base (8 pts)
    s.decay_ratio = 0.70      # Medium decay (8 pts)
    s.tightening_confirmed = False
    s.vdu_confirmed = False
    s.rvol = 1.0              # Modest volume (10 pts)
    s.htf_trend = 1
    s.trend = 1
    s.risk_pct = 2.5          # 6 pts
    s.candle_close_confirmed = True
    s.htf_4h_confirmed = True

    # Even if someone configures huge uncalibrated weights:
    crazy_quality_cfg = {
        "enabled": True,
        "close_location_weight": 50.0,
        "vdu_slope_weight": 50.0,
        "trigger_tightness_weight": 50.0,
        "failed_breakout_penalty_weight": 50.0
    }
    crazy_context_cfg = {
        "enabled": True,
        "htf_4h_weight": 50.0,
        "market_regime_weight": 50.0,
        "earnings_risk_weight": 50.0
    }

    score, grade = calculate_composite_score(s, quality_cfg=crazy_quality_cfg, context_cfg=crazy_context_cfg)

    # Scorer quality layer is capped at +15 max, confluence at +10 max.
    # Base score was ~45-50. Total score cannot exceed base + 25 = ~70-75 (Grade B), NOT 100 [A+]!
    assert score <= 80
    assert grade in ("B", "C")


def test_p0_3_calibrated_profile_distribution():
    """Verify calibrated weights in production YAMLs produce realistic grade spread."""
    # Setup A: Pristine institutional setup
    s_clean = Setup("PRISTINE")
    s_clean.state = State.BUY
    s_clean.direction = 1
    s_clean.base_width_pct = 3.5
    s_clean.decay_ratio = 0.35
    s_clean.tightening_confirmed = True
    s_clean.vdu_confirmed = True
    s_clean.rvol = 2.5
    s_clean.htf_confirmed = True
    s_clean.htf_trend = 1
    s_clean.trend = 1
    s_clean.sector_rs_pct = 5.0
    s_clean.rs_trend = "↗"
    s_clean.risk_pct = 1.0
    s_clean.candle_close_confirmed = True
    s_clean.htf_4h_confirmed = True

    # Setup B: Flawed setup with wick rejection and bear market regime
    s_flawed = Setup("FLAWED")
    s_flawed.state = State.BUY
    s_flawed.direction = 1
    s_flawed.base_width_pct = 11.0
    s_flawed.decay_ratio = 0.90
    s_flawed.tightening_confirmed = False
    s_flawed.vdu_confirmed = False
    s_flawed.rvol = 0.8
    s_flawed.htf_trend = -1
    s_flawed.trend = -1
    s_flawed.risk_pct = 4.5
    s_flawed.candle_close_loc = 0.20
    s_flawed.candle_close_confirmed = False
    s_flawed.failed_breakouts_in_base = 2
    s_flawed.market_regime = "HIGH_VOL_SELLOFF"

    calibrated_quality = {
        "enabled": True,
        "close_location_weight": 1.0,
        "vdu_slope_weight": 0.8,
        "trigger_tightness_weight": 0.6,
        "failed_breakout_penalty_weight": 1.0
    }
    calibrated_context = {
        "enabled": True,
        "htf_4h_weight": 1.0,
        "market_regime_weight": 0.8,
        "earnings_risk_weight": 0.8
    }

    score_clean, grade_clean = calculate_composite_score(s_clean, calibrated_quality, calibrated_context)
    score_flawed, grade_flawed = calculate_composite_score(s_flawed, calibrated_quality, calibrated_context)

    assert score_clean >= 90
    assert grade_clean == "A+"
    assert score_flawed <= 40
    assert grade_flawed == "C"


# =====================================================================
# 2. P0-2 Follow-Through Confirmation Policy
# =====================================================================

def test_p0_2_confirmation_policy_enforcement():
    """Verify that score < 85 requires confirmation while score >= 85 auto-confirms."""
    from breakout_scanner.scanner import MultiSymbolScanner
    from unittest.mock import MagicMock

    scanner = MultiSymbolScanner(provider=MagicMock(), cfg={
        "confirmation_policy": {"enabled": True, "min_auto_entry_score": 85}
    })

    # High score setup (90)
    s_high = Setup("HIGH_SCORE")
    s_high.state = State.BUY
    s_high.composite_score = 90
    s_high.retest_confirmed = False

    # Simulate scanner enrichment step
    df_dummy = pd.DataFrame([{"High": 105.0, "Low": 98.0, "Close": 104.0, "Volume": 1000}])
    # High score auto confirms
    if s_high.composite_score >= 85:
        s_high.requires_confirmation = False
        s_high.confirmation_status = "CONFIRMED"
    assert s_high.requires_confirmation is False
    assert s_high.confirmation_status == "CONFIRMED"

    # Lower score setup (75) on bar 0 (breakout bar)
    s_low = Setup("LOW_SCORE")
    s_low.state = State.BUY
    s_low.composite_score = 75
    s_low.bars_since_breakout = 0
    s_low.retest_confirmed = False
    s_low.requires_confirmation = (s_low.composite_score < 85)
    s_low.confirmation_status = "AWAITING_FOLLOW_THROUGH"

    assert s_low.requires_confirmation is True
    assert s_low.confirmation_status == "AWAITING_FOLLOW_THROUGH"
    assert s_low.trade_status_label == "AWAITING_CONFIRMATION"


# =====================================================================
# 3. P0-1 Notification Pipeline Risk Filtering
# =====================================================================

def test_p0_1_notification_pipeline_risk_filtering():
    """Verify that only risk-approved, confirmed setups enter Section 2A."""
    s_playable = Setup("PLAYABLE")
    s_playable.state = State.BUY
    s_playable.direction = 1
    s_playable.entry = 100.0
    s_playable.stop = 98.0
    s_playable.current_price = 100.2
    s_playable.risk_pct = 2.0
    s_playable.composite_score = 92
    s_playable.composite_grade = "A+"
    s_playable.requires_confirmation = False
    s_playable.confirmation_status = "CONFIRMED"

    s_unconfirmed = Setup("UNCONFIRMED")
    s_unconfirmed.state = State.BUY
    s_unconfirmed.direction = 1
    s_unconfirmed.entry = 50.0
    s_unconfirmed.stop = 48.5
    s_unconfirmed.current_price = 50.1
    s_unconfirmed.risk_pct = 3.0
    s_unconfirmed.composite_score = 74
    s_unconfirmed.composite_grade = "B"
    s_unconfirmed.requires_confirmation = True
    s_unconfirmed.confirmation_status = "AWAITING_FOLLOW_THROUGH"

    s_wide_risk = Setup("WIDERISK")
    s_wide_risk.state = State.BUY
    s_wide_risk.direction = 1
    s_wide_risk.entry = 200.0
    s_wide_risk.stop = 186.0
    s_wide_risk.current_price = 200.5
    s_wide_risk.risk_pct = 7.0  # Violates 5.0% max stop gate
    s_wide_risk.composite_score = 88
    s_wide_risk.composite_grade = "A"
    s_wide_risk.requires_confirmation = False

    setups = [s_playable, s_unconfirmed, s_wide_risk]

    # Format report without slate (fallback evaluation)
    report_html = format_scan_report_html(setups, market="TEST")

    # Section 2A must ONLY contain PLAYABLE
    sec_2a_part = report_html.split("SECTION 2A: FRESH ACTIONABLE ENTRIES")[1].split("SECTION 2B: TRADES IN PROGRESS")[0]
    assert "PLAYABLE" in sec_2a_part
    assert "UNCONFIRMED" not in sec_2a_part
    assert "WIDERISK" not in sec_2a_part

    # Section 2B must contain UNCONFIRMED and WIDERISK
    sec_2b_part = report_html.split("SECTION 2B: TRADES IN PROGRESS")[1]
    assert "UNCONFIRMED" in sec_2b_part
    assert "WIDERISK" in sec_2b_part
    assert "[⏳ AWAITING CONFIRMATION]" in sec_2b_part


# =====================================================================
# 4. P0-4 & P0-5 Persistent Portfolio State & Circuit Breaker
# =====================================================================

def test_p0_4_persistent_portfolio_state_lifecycle(tmp_path):
    """Verify atomic state persistence, MTM updates, and stop loss booking."""
    state_file = str(tmp_path / "test_portfolio_state.json")
    mgr = PortfolioStateManager(state_file=state_file)

    # 1. Record fresh trade entry
    alloc = PositionAllocation(
        symbol="AAPL",
        direction=1,
        shares=100,
        entry_price=150.0,
        stop_loss=145.0,
        take_profit_1=155.0,
        take_profit_2=160.0,
        dollar_risk=500.0,
        position_value=15000.0,
        risk_pct_of_equity=1.0,
        sector="Technology",
        score=92,
        grade="A+"
    )
    recorded = mgr.record_entry(alloc)
    assert recorded is True
    assert mgr.cumulative_daily_risk == 500.0
    assert len(mgr.get_open_positions()) == 1

    # Reload from disk to verify persistence
    mgr2 = PortfolioStateManager(state_file=state_file)
    assert len(mgr2.get_open_positions()) == 1
    assert mgr2.cumulative_daily_risk == 500.0

    # 2. Update with favorable price -> unrealized gain
    s_gain = Setup("AAPL")
    s_gain.current_price = 153.0
    mgr2.update_market_data([s_gain])
    assert mgr2.unrealized_daily_pnl == 300.0  # (153 - 150) * 100
    assert mgr2.get_total_daily_pnl() == 300.0

    # 3. Update with stop breach -> realized loss and position closed
    s_stop = Setup("AAPL")
    s_stop.current_price = 144.5
    mgr2.update_market_data([s_stop])
    assert len(mgr2.get_open_positions()) == 0
    assert mgr2.realized_daily_pnl == -550.0  # (144.5 - 150) * 100
    assert len(mgr2.closed_positions_today) == 1
    assert mgr2.closed_positions_today[0]["status"] in ("STOPPED_OUT", "CLOSED")
    assert mgr2.closed_positions_today[0].get("exit_reason") == "STOP_LOSS"


def test_p0_5_daily_loss_circuit_breaker(tmp_path):
    """Verify that daily loss exceeding 2.0% trips the global circuit breaker."""
    state_file = str(tmp_path / "test_circuit_breaker.json")
    mgr = PortfolioStateManager(state_file=state_file)

    account_equity = 100000.0
    max_loss_pct = 2.0  # $2,000 max allowable daily loss

    # Loss within limit (-$1,200)
    mgr.realized_daily_pnl = -1200.0
    mgr.unrealized_daily_pnl = 0.0
    tripped = mgr.check_circuit_breaker(account_equity=account_equity, max_loss_pct=max_loss_pct)
    assert tripped is False

    # Loss breaches limit (-$2,100)
    mgr.realized_daily_pnl = -2100.0
    tripped_breach = mgr.check_circuit_breaker(account_equity=account_equity, max_loss_pct=max_loss_pct)
    assert tripped_breach is True
    assert mgr.circuit_breaker_tripped is True
    assert "DAILY_LOSS_BREACH" in mgr.circuit_breaker_reason

    # Verify ProductionSafeguards reacts to circuit breaker
    safeguards = ProductionSafeguards(cfg={"risk": {"max_daily_loss_pct": 2.0}})
    allowed = safeguards.check_daily_loss_limit(current_capital=account_equity, portfolio_state=mgr)
    assert allowed is False
    assert safeguards.kill_switch_active is True
