"""
Phase 4 Verification Test Suite: Portfolio-Level Risk Budget & Governance
========================================================================
Verification Artifact for Milestone 4 (tests/test_portfolio_risk_budget.py)

Tests:
1. Portfolio-level risk budgeting (1.0% trade risk, 2.0% daily budget, 4 positions limit)
2. Sector concentration cap (25% max capital) and correlation stacking guard (<= 2 per sector)
3. Microstructure and realistic market frictions (NSE STT/GST/Turnover vs US SEC/TAF/Clearing)
4. Dynamic volatility slippage and overnight gap haircut
5. Multi-trade rejection gate (5% max risk, 0.15R friction, R:R >= 1.0, earnings proximity)
"""

import pytest
from breakout_scanner.models import Setup, State
from breakout_scanner.portfolio_guard import (
    PositionSizer,
    CorrelationStackingGuard,
    DailyRiskBudgetEngine,
    generate_actionable_slate,
    TradeSlate,
)
from breakout_scanner.friction import MarketFrictionEngine, FrictionBreakdown
from breakout_scanner.rejection_gate import TradeRejectionGate, RejectionDecision


# ─────────────────────────────────────────────────────────────────────────────
# 1. Portfolio-Level Risk Budgeting & Sizing Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_single_trade_one_percent_risk_sizing():
    """Verify exact 1.0% equity dollar risk sizing: Shares = floor((0.01 * Equity) / PerShareRisk)."""
    sizer = PositionSizer(account_equity=100000.0, risk_per_trade_pct=1.0)
    # Entry: 100, Stop: 98 -> Per share risk = $2.00
    # Expected target dollar risk = $1,000 -> Shares = 1000 // 2 = 500 shares ($50,000 position value)
    alloc = sizer.calculate_size(entry=100.0, stop=98.0, direction=1, symbol="AAPL")

    assert alloc.status == "APPROVED"
    assert alloc.shares == 200  # Capped by default max position size (20% of $100k = $20,000 -> 200 shares)
    assert alloc.dollar_risk == 400.0


def test_fractional_position_sizing():
    """Verify fractional position sizing when allow_fractional=True (e.g. for crypto)."""
    sizer = PositionSizer(account_equity=100000.0, risk_per_trade_pct=1.0, max_position_size_pct=50.0, allow_fractional=True)
    # Entry: 60000, Stop: 58500 -> Per share risk = $1500
    # Target dollar risk = $1,000 -> Shares = 1000 / 1500 = 0.6667 shares
    alloc = sizer.calculate_size(entry=60000.0, stop=58500.0, direction=1, symbol="BTC-USD")

    assert alloc.status == "APPROVED"
    assert alloc.shares == pytest.approx(0.6667, abs=1e-3)
    assert alloc.dollar_risk == pytest.approx(1000.0, abs=1e-2)


def test_daily_risk_budget_hard_cap():
    """Verify that DailyRiskBudgetEngine enforces a hard 2.0% daily dollar risk ceiling."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 1.0,  # $1,000 risk per trade
            "max_daily_risk_pct": 2.0,  # $2,000 daily risk ceiling
            "max_active_trades": 4,
            "max_sector_allocation_pct": 40.0,
            "max_trades_per_sector": 2,
        }
    }

    engine = DailyRiskBudgetEngine(cfg)
    s1 = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=100.0, stop=96.0, current_price=100.0, sector_name="Tech", composite_score=95)
    s2 = Setup(symbol="NVDA", state=State.BUY, direction=1, entry=100.0, stop=96.0, current_price=100.0, sector_name="Tech", composite_score=90)
    s3 = Setup(symbol="XOM", state=State.BUY, direction=1, entry=100.0, stop=96.0, current_price=100.0, sector_name="Energy", composite_score=85)

    slate = engine.allocate_slate([s1, s2, s3])

    # s1 ($1000) + s2 ($1000) = $2000 total daily risk. s3 must be suppressed
    assert len(slate.actionable_trades) == 2
    assert slate.actionable_trades[0].symbol == "AAPL"
    assert slate.actionable_trades[1].symbol == "NVDA"
    assert len(slate.suppressed_trades) == 1
    assert slate.suppressed_trades[0].symbol == "XOM"
    assert "DAILY_RISK_BUDGET_EXCEEDED" in slate.suppressed_trades[0].reason


# ─────────────────────────────────────────────────────────────────────────────
# 2. Sector Exposure & Correlation Clustering Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_sector_concentration_twenty_five_percent_cap():
    """Verify that portfolio capital allocated to any single industry sector cannot exceed 25%."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 0.5,
            "max_daily_risk_pct": 5.0,
            "max_active_trades": 5,
            "max_sector_allocation_pct": 25.0,  # Max $25,000 in single sector
            "max_position_size_pct": 20.0,      # Max $20,000 per trade
            "max_trades_per_sector": 2,
        }
    }

    engine = DailyRiskBudgetEngine(cfg)
    # Trade 1 in Tech takes $20,000
    s1 = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=100.0, stop=97.5, current_price=100.0, sector_name="Tech", composite_score=95)
    # Trade 2 in Tech also requests $20,000 -> Total $40,000 > $25,000 cap!
    s2 = Setup(symbol="MSFT", state=State.BUY, direction=1, entry=100.0, stop=97.5, current_price=100.0, sector_name="Tech", composite_score=90)

    slate = engine.allocate_slate([s1, s2])

    assert len(slate.actionable_trades) == 1
    assert slate.actionable_trades[0].symbol == "AAPL"
    assert len(slate.suppressed_trades) == 1
    assert slate.suppressed_trades[0].symbol == "MSFT"
    assert "SECTOR_CAP_EXCEEDED" in slate.suppressed_trades[0].reason


def test_correlation_clustering_deduplication():
    """Verify that simultaneous breakouts in the same sector select the highest conviction setup."""
    guard = CorrelationStackingGuard(max_trades_per_sector=1)

    s_nvda = Setup(symbol="NVDA", state=State.BUY, direction=1, entry=120.0, stop=115.0, sector_name="Semis", composite_score=96, composite_grade="A+")
    s_amd = Setup(symbol="AMD", state=State.BUY, direction=1, entry=150.0, stop=145.0, sector_name="Semis", composite_score=84, composite_grade="B")
    s_qcom = Setup(symbol="QCOM", state=State.BUY, direction=1, entry=160.0, stop=155.0, sector_name="Semis", composite_score=78, composite_grade="C")

    approved, suppressed = guard.filter_correlations([s_nvda, s_amd, s_qcom])

    assert len(approved) == 1
    assert approved[0].symbol == "NVDA"
    assert len(suppressed) == 2
    sup_syms = [s.symbol for s, _ in suppressed]
    assert "AMD" in sup_syms
    assert "QCOM" in sup_syms
    assert "CORRELATION_STACKING" in suppressed[0][1]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Microstructure & Market-Specific Friction Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_nse_statutory_friction_breakdown():
    """Verify NSE statutory charges: STT (0.1%), Turnover (0.00345%), GST (18%), Stamp Duty (0.015%)."""
    engine = MarketFrictionEngine()
    # RELIANCE.NS, Entry: 2500, Stop: 2400 (Risk $100/share), Shares: 50 -> Turnover: $125,000 each leg ($250k total)
    breakdown = engine.calculate_round_trip_friction(
        symbol="RELIANCE.NS",
        entry=2500.0,
        stop=2400.0,
        shares=50.0,
        is_intraday=False
    )

    assert breakdown.market == "NSE"
    assert breakdown.stt_or_sec > 0.0  # STT charged
    assert breakdown.gst > 0.0         # 18% GST charged
    assert breakdown.stamp_duty > 0.0  # Stamp duty charged
    assert breakdown.total_dollar_friction > 0.0
    assert breakdown.friction_r < 0.15


def test_us_statutory_friction_breakdown():
    """Verify US statutory charges: SEC fee (0.00278% on sales), FINRA TAF, clearing fee."""
    engine = MarketFrictionEngine()
    breakdown = engine.calculate_round_trip_friction(
        symbol="AAPL",
        entry=150.0,
        stop=145.0,
        shares=100.0
    )

    assert breakdown.market == "US"
    assert breakdown.stt_or_sec > 0.0  # SEC fee on sales
    assert breakdown.exchange_and_regulatory > 0.0  # TAF + Clearing
    assert breakdown.gst == 0.0
    assert breakdown.stamp_duty == 0.0


def test_dynamic_volatility_slippage():
    """Verify dynamic volatility slippage: max(Base Slippage, 0.05 * ATR / Price)."""
    engine = MarketFrictionEngine()

    # Low volatility: ATR = 0.50 on $100 stock -> 0.05 * 0.50% = 0.025% < 0.05% base -> Uses base 0.05%
    slip_low = engine.calculate_dynamic_slippage_pct(price=100.0, atr=0.50)
    assert slip_low == pytest.approx(0.05, abs=1e-4)

    # High volatility spike: ATR = 5.0 on $50 stock (10% ATR) -> 0.05 * 10% = 0.50% > 0.05% base -> Scales up!
    slip_high = engine.calculate_dynamic_slippage_pct(price=50.0, atr=5.0)
    assert slip_high == pytest.approx(0.50, abs=1e-4)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multi-Trade Rejection Gate Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_rejection_gate_structural_risk_exceeded():
    """Verify rejection when structural stop distance exceeds 5.0%."""
    cfg = {"rejection_gate": {"enabled": True, "max_stop_pct": 5.0}}
    gate = TradeRejectionGate(cfg)

    # Risk = (100 - 92) / 100 = 8.0% > 5.0%
    setup = Setup("WIDE_STOP", state=State.BUY, direction=1, entry=100.0, stop=92.0, tp1=108.0)
    decision = gate.evaluate_setup(setup)

    assert decision.approved is False
    assert decision.rejection_code == "STRUCTURAL_RISK_EXCEEDED"
    assert "Risk 8.00% exceeds maximum" in decision.reason


def test_rejection_gate_unfavorable_payoff_ratio():
    """Verify rejection when Reward:Risk to TP1 is less than 1.0."""
    cfg = {"rejection_gate": {"enabled": True, "min_rr_ratio": 1.0}}
    gate = TradeRejectionGate(cfg)

    # Entry 100, Stop 96 ($4 risk). TP1 102 ($2 reward) -> R:R = 0.5 < 1.0
    setup = Setup("POOR_RR", state=State.BUY, direction=1, entry=100.0, stop=96.0, tp1=102.0)
    decision = gate.evaluate_setup(setup)

    assert decision.approved is False
    assert decision.rejection_code == "UNFAVORABLE_PAYOFF"
    assert "below minimum 1.00R" in decision.reason


def test_rejection_gate_imminent_earnings():
    """Verify rejection when company earnings announcement is scheduled within 3 sessions."""
    cfg = {"rejection_gate": {"enabled": True, "earnings_buffer_days": 3}}
    gate = TradeRejectionGate(cfg)

    setup = Setup("EARNINGS_RISK", state=State.BUY, direction=1, entry=100.0, stop=97.0, tp1=106.0)
    setup.days_to_earnings = 2  # Earnings in 2 days!

    decision = gate.evaluate_setup(setup)
    assert decision.approved is False
    assert decision.rejection_code == "IMMINENT_EARNINGS"
    assert "earnings event within 2 sessions" in decision.reason


def test_rejection_gate_excessive_friction():
    """Verify rejection when friction exceeds 0.15R."""
    cfg = {"rejection_gate": {"enabled": True, "max_friction_r": 0.15}}
    gate = TradeRejectionGate(cfg)

    setup = Setup("PENNY_STOCK", state=State.BUY, direction=1, entry=1.00, stop=0.99, tp1=1.05)
    setup.friction_r = 0.22  # High friction relative to 1 cent stop

    decision = gate.evaluate_setup(setup)
    assert decision.approved is False
    assert decision.rejection_code == "FRICTION_EXCESSIVE"
    assert "0.220R exceeds maximum threshold 0.15R" in decision.reason


def test_rejection_gate_approved_setup():
    """Verify full approval when setup satisfies all institutional criteria."""
    cfg = {"rejection_gate": {"enabled": True}}
    gate = TradeRejectionGate(cfg)

    # Entry: 100.0, Stop: 97.0 (3.0% risk < 5.0%), TP1: 106.0 (R:R = 2.0 > 1.0), Earnings in 45 days
    setup = Setup("GRADE_A", state=State.BUY, direction=1, entry=100.0, stop=97.0, tp1=106.0)
    setup.days_to_earnings = 45
    setup.friction_r = 0.05

    decision = gate.evaluate_setup(setup)
    assert decision.approved is True
    assert decision.rejection_code is None

