import pytest
import numpy as np
from breakout_scanner.models import Setup, State
from breakout_scanner.portfolio_guard import (
    PositionSizer,
    CorrelationStackingGuard,
    DailyRiskBudgetEngine,
    generate_actionable_slate,
    format_trade_slate_cli,
    format_trade_slate_html,
)


def test_position_sizer_risk_math():
    """Verify exact share calculation based on 1% equity risk and ATR structural stop."""
    sizer = PositionSizer(
        account_equity=100000.0,
        risk_per_trade_pct=1.0,  # $1,000 target risk
        max_position_size_pct=25.0,  # $25,000 max capital
    )
    # Long trade: Entry $100.0, Stop $95.0 -> $5.0 risk per share
    alloc = sizer.calculate_size(
        entry=100.0,
        stop=95.0,
        direction=1,
        symbol="AAPL",
        sector="Tech",
        score=92,
        grade="A+",
    )

    assert alloc.status == "APPROVED"
    assert alloc.shares == 200  # $1,000 // $5.0 = 200
    assert alloc.position_value == 20000.0  # 200 * $100
    assert alloc.dollar_risk == 1000.0      # 200 * $5
    assert alloc.risk_pct_of_equity == 1.0
    assert alloc.capital_pct_of_equity == 20.0
    assert alloc.take_profit_1 == 105.0     # 100 + 5 (+1.0R)
    assert alloc.take_profit_2 == 110.0     # 100 + 10 (+2.0R)


def test_position_sizer_capital_cap():
    """Verify position size is capped when tight stop would exceed max position capital."""
    sizer = PositionSizer(
        account_equity=100000.0,
        risk_per_trade_pct=1.0,      # $1,000 risk
        max_position_size_pct=10.0,  # Max $10,000 capital in single position
    )
    # Entry $100, Stop $99 (ultra tight $1.0 risk).
    # Risk-only sizing would take 1000 shares = $100,000 (100% equity).
    # Capital cap must clamp shares to $10,000 // 100 = 100 shares.
    alloc = sizer.calculate_size(entry=100.0, stop=99.0, direction=1, symbol="NVDA")

    assert alloc.status == "APPROVED"
    assert alloc.shares == 100
    assert alloc.position_value == 10000.0
    assert alloc.dollar_risk == 100.0  # 100 * $1
    assert alloc.capital_pct_of_equity == 10.0


def test_position_sizer_invalid_prices():
    """Verify graceful rejection for non-positive or equal entry/stop prices."""
    sizer = PositionSizer(account_equity=100000.0)
    
    # Zero or negative prices
    alloc1 = sizer.calculate_size(entry=0.0, stop=10.0)
    assert alloc1.status == "REJECTED"
    assert "INVALID_PRICES" in alloc1.reason

    # Equal entry and stop
    alloc2 = sizer.calculate_size(entry=50.0, stop=50.0)
    assert alloc2.status == "REJECTED"
    assert "INVALID_PRICES" in alloc2.reason


def test_correlation_stacking_guard():
    """Verify that multiple setups in the same sector basket elect only the highest-conviction setup."""
    guard = CorrelationStackingGuard(max_trades_per_sector=1)

    s1 = Setup(
        symbol="NVDA",
        state=State.BUY,
        direction=1,
        entry=140.0,
        stop=135.0,
        sector_name="Semis",
        composite_score=95,
        composite_grade="A+",
        sector_rs_pct=5.2,
        risk_pct=3.5,
    )
    s2 = Setup(
        symbol="AMD",
        state=State.BUY,
        direction=1,
        entry=160.0,
        stop=152.0,
        sector_name="Semis",
        composite_score=84,
        composite_grade="A",
        sector_rs_pct=2.1,
        risk_pct=5.0,
    )
    s3 = Setup(
        symbol="QCOM",
        state=State.BUY,
        direction=1,
        entry=175.0,
        stop=168.0,
        sector_name="Semis",
        composite_score=76,
        composite_grade="B",
        sector_rs_pct=0.5,
        risk_pct=4.0,
    )
    s4 = Setup(
        symbol="JNJ",
        state=State.BUY,
        direction=1,
        entry=160.0,
        stop=157.0,
        sector_name="Healthcare",
        composite_score=88,
        composite_grade="A",
        sector_rs_pct=1.8,
        risk_pct=1.9,
    )

    approved, suppressed = guard.filter_correlations([s1, s2, s3, s4])

    approved_symbols = [s.symbol for s in approved]
    assert "NVDA" in approved_symbols
    assert "JNJ" in approved_symbols
    assert "AMD" not in approved_symbols
    assert "QCOM" not in approved_symbols

    # Verify suppression reasons identify winner
    suppressed_map = {s.symbol: reason for s, reason in suppressed}
    assert "AMD" in suppressed_map
    assert "NVDA" in suppressed_map["AMD"]
    assert "CORRELATION_STACKING" in suppressed_map["AMD"]
    assert "QCOM" in suppressed_map


def test_daily_risk_budget_cap():
    """Verify that daily risk budget stops allocating trades once risk budget is exhausted."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 1.0,      # $1,000 risk per trade
            "max_daily_risk_pct": 2.0,      # $2,000 max daily risk
            "max_active_trades": 5,
            "max_sector_allocation_pct": 50.0,
            "max_position_size_pct": 20.0,
            "max_trades_per_sector": 1,
        }
    }

    # Setup 1: $1,000 risk (AAPL)
    s1 = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=100.0, stop=90.0, sector_name="Tech", composite_score=92)
    # Setup 2: $1,000 risk (XOM)
    s2 = Setup(symbol="XOM", state=State.BUY, direction=1, entry=100.0, stop=90.0, sector_name="Energy", composite_score=90)
    # Setup 3: $1,000 risk (JNJ) -> Exceeds $2,000 daily budget!
    s3 = Setup(symbol="JNJ", state=State.BUY, direction=1, entry=100.0, stop=90.0, sector_name="Health", composite_score=85)

    slate = generate_actionable_slate([s1, s2, s3], config=cfg)

    assert len(slate.actionable_trades) == 2
    actionable_symbols = [t.symbol for t in slate.actionable_trades]
    assert "AAPL" in actionable_symbols
    assert "XOM" in actionable_symbols
    assert "JNJ" not in actionable_symbols

    # Verify JNJ suppression reason
    suppressed_symbols = [t.symbol for t in slate.suppressed_trades]
    assert "JNJ" in suppressed_symbols
    jnj_alloc = [t for t in slate.suppressed_trades if t.symbol == "JNJ"][0]
    assert "DAILY_RISK_BUDGET_EXCEEDED" in jnj_alloc.reason

    assert slate.total_daily_risk_allocated == pytest.approx(2000.0, abs=1e-2)
    assert slate.remaining_daily_risk_budget == pytest.approx(0.0, abs=1e-2)


def test_max_active_trades_limit():
    """Verify that concurrent position slots limit blocks excessive simultaneous exposure."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 0.5,      # $500 risk per trade
            "max_daily_risk_pct": 5.0,      # $5,000 risk cap (ample risk)
            "max_active_trades": 2,         # Only 2 concurrent trades allowed
            "max_sector_allocation_pct": 50.0,
            "max_position_size_pct": 20.0,
            "max_trades_per_sector": 1,
        }
    }

    s1 = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=100.0, stop=95.0, sector_name="Tech", composite_score=92)
    s2 = Setup(symbol="XOM", state=State.BUY, direction=1, entry=100.0, stop=95.0, sector_name="Energy", composite_score=88)
    s3 = Setup(symbol="JNJ", state=State.BUY, direction=1, entry=100.0, stop=95.0, sector_name="Health", composite_score=85)

    slate = generate_actionable_slate([s1, s2, s3], config=cfg)

    assert len(slate.actionable_trades) == 2
    assert slate.remaining_trade_slots == 0
    suppressed_symbols = [t.symbol for t in slate.suppressed_trades]
    assert "JNJ" in suppressed_symbols
    jnj_alloc = [t for t in slate.suppressed_trades if t.symbol == "JNJ"][0]
    assert "MAX_ACTIVE_TRADES_EXCEEDED" in jnj_alloc.reason


def test_sector_concentration_cap():
    """Verify that sector capital cap blocks overallocation to a single sector."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 0.5,
            "max_daily_risk_pct": 5.0,
            "max_active_trades": 5,
            "max_sector_allocation_pct": 20.0,  # Max $20,000 in any single sector
            "max_position_size_pct": 15.0,      # $15,000 per trade
            "max_trades_per_sector": 2,         # Allow 2 trades in sector
        }
    }

    # Trade 1 in Tech: $15,000 position value
    # With entry 100, stop 96.7 ($3.33 risk per share), shares = 500/3.33 = 150 shares -> $15,000 value
    s1 = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=100.0, stop=96.7, sector_name="Tech", composite_score=95)
    # Trade 2 in Tech: also requests $15,000 value. $15k + $15k = $30k > $20k cap!
    s2 = Setup(symbol="MSFT", state=State.BUY, direction=1, entry=100.0, stop=96.7, sector_name="Tech", composite_score=85)

    slate = generate_actionable_slate([s1, s2], config=cfg)

    assert len(slate.actionable_trades) == 1
    assert slate.actionable_trades[0].symbol == "AAPL"
    assert len(slate.suppressed_trades) == 1
    assert slate.suppressed_trades[0].symbol == "MSFT"
    assert "SECTOR_CAP_EXCEEDED" in slate.suppressed_trades[0].reason


def test_trade_slate_formatters():
    """Verify ASCII and HTML renderers produce rich institutional reports."""
    cfg = {
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 1.0,
            "max_daily_risk_pct": 2.0,
            "max_active_trades": 4,
            "max_trades_per_sector": 1,
        }
    }
    s1 = Setup(symbol="NVDA", state=State.BUY, direction=1, entry=140.0, stop=135.0, sector_name="Semis", composite_score=94, composite_grade="A+")
    s2 = Setup(symbol="AMD", state=State.BUY, direction=1, entry=160.0, stop=152.0, sector_name="Semis", composite_score=80, composite_grade="A")

    slate = generate_actionable_slate([s1, s2], config=cfg)

    cli_text = format_trade_slate_cli(slate)
    assert "SECTION 3: 🛡️ INSTITUTIONAL ACTIONABLE TRADE SLATE" in cli_text
    assert "NVDA" in cli_text
    assert "AMD" in cli_text
    assert "CORRELATION_STACKING" in cli_text

    html_text = format_trade_slate_html(slate)
    assert "ACTIONABLE TRADE SLATE" in html_text
    assert "NVDA" in html_text
    assert "AMD" in html_text
