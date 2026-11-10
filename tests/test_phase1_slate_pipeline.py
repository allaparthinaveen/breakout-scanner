import os
import json
import pytest
from unittest.mock import MagicMock, patch

from breakout_scanner.models import Setup, State
from breakout_scanner.config import load_config
from breakout_scanner.slate import (
    ActionableSlate,
    SlatePipeline,
    EffectiveConfigSummary,
    RejectionRecord,
    SuppressedRecord,
)
from breakout_scanner.telegram_formatter import format_actionable_slate_html
from breakout_scanner.scheduler import SessionScheduler


# =====================================================================
# 1.1 ActionableSlate Domain Model & Pipeline Tests
# =====================================================================

def test_actionable_slate_domain_model_creation():
    """Verify ActionableSlate attributes and serialization."""
    slate = ActionableSlate(
        slate_id="test1234",
        timestamp="2026-11-09T10:00:00",
        profile_name="US Equities Production",
        total_scanned=30,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        suppressed_trades=[],
        rejected_trades=[],
        remaining_daily_risk_budget=2000.0,
        remaining_trade_slots=4,
        max_active_trades=4,
        is_illustrative_sizing=False
    )

    assert slate.slate_id == "test1234"
    assert slate.profile_name == "US Equities Production"
    assert slate.has_actionable_trades is False
    assert slate.actionable_count == 0

    data = slate.to_dict()
    assert data["slate_id"] == "test1234"
    assert data["portfolio_equity"] == 100000.0
    assert data["is_illustrative_sizing"] is False


def test_rejection_gate_in_slate_pipeline():
    """Verify that setups violating pre-trade gates are categorized into rejected_trades."""
    cfg = load_config("config/profiles/us_production.yaml")
    pipeline = SlatePipeline(cfg=cfg)

    # 1. Stop > 5.0% (Risk = 7.0%)
    s_wide_stop = Setup(
        symbol="WIDE",
        state=State.BUY,
        direction=1,
        entry=100.0,
        stop=93.0,
        risk_pct=7.0,
        current_price=100.10,
        composite_score=85,
        tp1=110.0,
        tp2=120.0
    )

    # 2. Unfavorable Payoff (R:R < 1.0 to TP1)
    s_bad_rr = Setup(
        symbol="BADRR",
        state=State.BUY,
        direction=1,
        entry=100.0,
        stop=97.0,   # Risk = $3.00
        current_price=100.10,
        risk_pct=3.0,
        composite_score=85,
        tp1=101.5,   # Reward = $1.50 -> R:R = 0.50
        tp2=106.0
    )

    # 3. Imminent Earnings (days_to_earnings = 1)
    s_earnings = Setup(
        symbol="EARN",
        state=State.BUY,
        direction=1,
        entry=100.0,
        stop=98.0,
        risk_pct=2.0,
        current_price=100.10,
        composite_score=85,
        tp1=104.0,
        tp2=108.0,
        days_to_earnings=1,
        earnings_warning=True
    )

    slate = pipeline.evaluate_setups([s_wide_stop, s_bad_rr, s_earnings])

    assert slate.actionable_count == 0
    assert slate.rejected_count == 3
    rej_codes = [r.rejection_code for r in slate.rejected_trades]
    assert "STRUCTURAL_RISK_EXCEEDED" in rej_codes
    assert "UNFAVORABLE_PAYOFF" in rej_codes
    assert "IMMINENT_EARNINGS" in rej_codes


def test_portfolio_guard_correlation_stacking_and_budget_caps():
    """Verify that multiple setups from same sector are de-duplicated, selecting highest score."""
    cfg = load_config("config/profiles/us_production.yaml")
    pipeline = SlatePipeline(cfg=cfg)

    # Two Tech setups: NVDA (Score 92) and AMD (Score 80)
    nvda = Setup(
        symbol="NVDA",
        state=State.BUY,
        direction=1,
        entry=140.0,
        stop=136.0,
        current_price=140.20,
        risk_pct=2.85,
        composite_grade="A+",
        composite_score=92,
        tp1=148.0,
        tp2=156.0,
        sector_name="Tech",
        friction_r=0.08
    )

    amd = Setup(
        symbol="AMD",
        state=State.BUY,
        direction=1,
        entry=160.0,
        stop=155.0,
        current_price=160.30,
        risk_pct=3.12,
        composite_grade="B",
        composite_score=80,
        tp1=170.0,
        tp2=180.0,
        sector_name="Tech",
        friction_r=0.08
    )

    # Financial setup: JPM (Score 86)
    jpm = Setup(
        symbol="JPM",
        state=State.BUY,
        direction=1,
        entry=220.0,
        stop=215.0,
        current_price=220.50,
        risk_pct=2.27,
        composite_grade="A",
        composite_score=86,
        tp1=230.0,
        tp2=240.0,
        sector_name="Financials",
        friction_r=0.08
    )

    slate = pipeline.evaluate_setups([nvda, amd, jpm])

    # NVDA and JPM must be selected (NVDA wins Tech basket, JPM wins Financials)
    selected_symbols = [t.symbol for t in slate.selected_trades]
    assert "NVDA" in selected_symbols
    assert "JPM" in selected_symbols
    assert "AMD" not in selected_symbols

    # AMD must be in suppressed_trades due to correlation stacking
    suppressed_symbols = [s.symbol for s in slate.suppressed_trades]
    assert "AMD" in suppressed_symbols
    amd_record = next(s for s in slate.suppressed_trades if s.symbol == "AMD")
    assert "CORRELATION_STACKING" in amd_record.reason


def test_illustrative_sizing_flag_when_portfolio_guard_disabled():
    """Verify that when portfolio_guard.enabled is False, sizing is marked illustrative."""
    cfg = load_config("config/default.yaml")
    # default.yaml has portfolio_guard.enabled = False
    assert cfg.get("portfolio_guard", {}).get("enabled", False) is False

    pipeline = SlatePipeline(cfg=cfg)
    s = Setup(
        symbol="MSFT",
        state=State.BUY,
        direction=1,
        entry=400.0,
        stop=392.0,
        current_price=400.50,
        risk_pct=2.0,
        composite_grade="A",
        composite_score=85,
        tp1=416.0,
        tp2=432.0,
        friction_r=0.06
    )

    slate = pipeline.evaluate_setups([s])
    assert slate.is_illustrative_sizing is True
    assert "NOTE: Portfolio Guard disabled" in slate.format_cli()


# =====================================================================
# 1.2 Named Production Profiles & Effective Config Summary Tests
# =====================================================================

def test_us_production_profile_loading():
    """Verify config/profiles/us_production.yaml loads correctly with all guards active."""
    profile_path = "config/profiles/us_production.yaml"
    assert os.path.exists(profile_path)
    cfg = load_config(profile_path)

    assert cfg["market"] == "US"
    assert cfg["currency"] == "USD"
    assert cfg["portfolio_guard"]["enabled"] is True
    assert cfg["portfolio_guard"]["account_equity"] == 100000.0
    assert cfg["portfolio_guard"]["max_trades_per_sector"] == 1
    assert cfg["rejection_gate"]["enabled"] is True
    assert cfg["rejection_gate"]["max_stop_pct"] == 5.0
    assert cfg["safeguards"]["enforce_stale_data"] is True


def test_crypto_production_profile_loading():
    """Verify config/profiles/crypto_production.yaml loads with 24/7 fractional settings."""
    profile_path = "config/profiles/crypto_production.yaml"
    assert os.path.exists(profile_path)
    cfg = load_config(profile_path)

    assert cfg["market"] == "CRYPTO"
    assert cfg["currency"] == "USDT"
    assert cfg["portfolio_guard"]["enabled"] is True
    assert cfg["portfolio_guard"]["allow_fractional"] is True
    assert cfg["rejection_gate"]["enforce_earnings_check"] is False


def test_nse_production_profile_loading():
    """Verify config/profiles/nse_production.yaml loads with INR capital and statutory friction."""
    profile_path = "config/profiles/nse_production.yaml"
    assert os.path.exists(profile_path)
    cfg = load_config(profile_path)

    assert cfg["market"] == "NSE"
    assert cfg["currency"] == "INR"
    assert cfg["portfolio_guard"]["account_equity"] == 1000000.0
    assert cfg["rejection_gate"]["max_stop_pct"] == 4.5
    assert cfg["friction"]["commission_pct"] == 0.10


def test_effective_config_summary_matrix():
    """Verify EffectiveConfigSummary matrix output formatting."""
    cfg = load_config("config/profiles/us_production.yaml")
    matrix = EffectiveConfigSummary.generate_matrix(cfg)

    assert "BREAKOUT SCANNER EFFECTIVE CONFIGURATION SAFETY MATRIX" in matrix
    assert "US Equities Production" in matrix
    assert "Portfolio Guard:        🟢 ACTIVE" in matrix
    assert "Pre-Trade Rejection:    🟢 ACTIVE" in matrix
    assert "Production Safeguards:  🟢 ACTIVE" in matrix


# =====================================================================
# 1.3 Telegram Decoupling & Slate Formatter Tests
# =====================================================================

def test_telegram_delivery_failure_decoupled_from_state():
    """A Telegram network timeout must not raise exception or break deduplication."""
    scheduler = SessionScheduler(cfg_path="config/profiles/us_production.yaml")
    # Simulate failed Telegram delivery
    scheduler.send_telegram_alert = MagicMock(return_value=False)

    s = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=220.0,
        stop=215.0,
        current_price=220.20,
        risk_pct=2.27,
        composite_grade="A",
        composite_score=88,
        tp1=230.0,
        tp2=240.0,
        bars_since_breakout=0,
        friction_r=0.08
    )

    scheduler.load_symbols = MagicMock(return_value=["AAPL"])
    scheduler.scanner.scan = MagicMock(return_value=[s])

    result = scheduler.scan_and_alert()
    # Alert was attempted, sent failed, but method finishes cleanly
    assert result["scanned"] == 1
    assert result["alerts_sent"] == 0
    assert result["actionable_trades"] == 1

    # Deduplication cache should still record the trade to prevent alert storms
    sig = scheduler.deduplicator.build_signature("AAPL", "BREAKOUT", s.breakout_time or "LATEST_CLOSE")
    assert scheduler.deduplicator.is_duplicate(sig) is True


def test_telegram_actionable_slate_html_formatting():
    """Verify format_actionable_slate_html renders all sections."""
    cfg = load_config("config/profiles/us_production.yaml")
    pipeline = SlatePipeline(cfg=cfg)

    s_good = Setup(
        symbol="GOOGL",
        state=State.BUY,
        direction=1,
        entry=175.0,
        stop=170.0,
        current_price=175.20,
        risk_pct=2.85,
        composite_grade="A",
        composite_score=87,
        tp1=185.0,
        tp2=195.0,
        friction_r=0.08
    )

    s_rej = Setup(
        symbol="REJ",
        state=State.BUY,
        direction=1,
        entry=50.0,
        stop=45.0,
        risk_pct=10.0,  # > 5.0%
        current_price=50.10,
        tp1=60.0
    )

    slate = pipeline.evaluate_setups([s_good, s_rej])
    html_msg = format_actionable_slate_html(slate)

    assert "INSTITUTIONAL ACTIONABLE SLATE" in html_msg
    assert "GOOGL" in html_msg
    assert "TOP-N APPROVED TRADES" in html_msg
    assert "DISQUALIFIED" in html_msg
    assert "REJ" in html_msg
