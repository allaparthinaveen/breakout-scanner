import pytest
from breakout_scanner.models import Setup, State
from breakout_scanner.slate import ActionableSlate, RejectionRecord, PositionAllocation
from breakout_scanner.telegram_formatter import format_scan_report_html


def test_empty_slate_excludes_late_chase_from_section_2a():
    """
    ITEM 1 Regression Test:
    When a slate is provided but selected_trades is empty (0 trades approved),
    and a fresh confirmed setup was rejected for LATE_CHASE:
    1. The setup MUST NOT appear in Section 2A (Playable Now).
    2. Section 2A MUST show (None).
    3. The setup MUST appear in the 'Fresh but rejected' section with its reason.
    """
    s_pg = Setup(
        symbol="PG",
        state=State.BUY,
        direction=1,
        entry=100.0,
        current_price=101.99,
        stop=98.0,
        tp1=102.5,
        tp2=105.0,
        bars_since_breakout=0,
        composite_grade="A+",
        composite_score=100,
        risk_pct=2.0,
    )
    s_pg.requires_confirmation = False

    slate = ActionableSlate(
        slate_id="slate_test_1",
        timestamp="2026-11-16T15:31:00Z",
        profile_name="us_production",
        total_scanned=215,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        rejected_trades=[
            RejectionRecord(
                symbol="PG",
                rejection_code="LATE_CHASE",
                reason="LATE_CHASE: ext 1.99% vs tol 0.32%",
                setup=s_pg,
            )
        ],
    )

    # Test US Market formatter
    html_us = format_scan_report_html([s_pg], market="US", slate=slate)
    
    # 2A header must be followed by (None), and PG must NOT be in 2A
    sec_2a = html_us.split("SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")[1].split("SECTION 2B:")[0]
    assert "<i>(None)</i>" in sec_2a
    assert "• <b>PG</b>" not in sec_2a or "Fresh but rejected" in sec_2a

    # Must be in "Fresh but rejected" section
    assert "Fresh but rejected" in html_us
    assert "PG" in html_us
    assert "LATE_CHASE: ext 1.99% vs tol 0.32%" in html_us


def test_slate_with_one_approved_includes_only_approved_in_2a():
    """
    When slate approves 1 symbol and rejects another, only the approved symbol
    is in 2A.
    """
    s_app = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=150.0,
        current_price=150.2,
        stop=147.0,
        tp1=153.0,
        tp2=156.0,
        bars_since_breakout=0,
        composite_grade="A",
        composite_score=85,
        risk_pct=2.0,
    )
    s_app.requires_confirmation = False

    s_rej = Setup(
        symbol="MSFT",
        state=State.BUY,
        direction=1,
        entry=300.0,
        current_price=305.0,
        stop=295.0,
        tp1=306.0,
        tp2=312.0,
        bars_since_breakout=0,
        composite_grade="A+",
        composite_score=95,
        risk_pct=1.6,
    )
    s_rej.requires_confirmation = False

    alloc_app = PositionAllocation(
        symbol="AAPL",
        direction=1,
        entry_price=150.0,
        stop_loss=147.0,
        shares=100,
        status="APPROVED",
        setup=s_app,
    )

    slate = ActionableSlate(
        slate_id="slate_test_2",
        timestamp="2026-11-16T15:31:00Z",
        profile_name="us_production",
        total_scanned=215,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[alloc_app],
        rejected_trades=[
            RejectionRecord(
                symbol="MSFT",
                rejection_code="LATE_CHASE",
                reason="LATE_CHASE: ext 1.67% vs tol 0.40%",
                setup=s_rej,
            )
        ],
    )

    html = format_scan_report_html([s_app, s_rej], market="US", slate=slate)
    sec_2a = html.split("SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")[1].split("SECTION 2B:")[0]
    assert "AAPL" in sec_2a
    assert "MSFT" not in sec_2a


def test_legacy_fallback_when_slate_is_none():
    """
    When slate is None, legacy fallback behavior applies.
    """
    s_fresh = Setup(
        symbol="TSLA",
        state=State.BUY,
        direction=1,
        entry=200.0,
        current_price=200.5,
        stop=195.0,
        tp1=205.0,
        tp2=210.0,
        bars_since_breakout=0,
        composite_grade="A",
        composite_score=85,
        risk_pct=2.5,
    )
    s_fresh.requires_confirmation = False

    html = format_scan_report_html([s_fresh], market="US", slate=None)
    assert "TSLA" in html
    assert "🟢 <b>SECTION 2A: FRESH ACTIONABLE ENTRIES" in html
    sec_2a = html.split("SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")[1].split("SECTION 2B:")[0]
    assert "TSLA" in sec_2a


def test_nse_and_us_formatter_same_behavior():
    """
    Both NSE and US formatters follow the exact same rejected candidate guard.
    """
    s_nse = Setup(
        symbol="RELIANCE.NS",
        state=State.BUY,
        direction=1,
        entry=2500.0,
        current_price=2550.0,
        stop=2450.0,
        tp1=2560.0,
        tp2=2600.0,
        bars_since_breakout=0,
        composite_grade="A+",
        composite_score=92,
        risk_pct=2.0,
    )
    s_nse.requires_confirmation = False

    slate = ActionableSlate(
        slate_id="slate_nse_test",
        timestamp="2026-11-16T10:16:00Z",
        profile_name="nse_production",
        total_scanned=80,
        portfolio_equity=1000000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=20000.0,
        selected_trades=[],
        rejected_trades=[
            RejectionRecord(
                symbol="RELIANCE.NS",
                rejection_code="LATE_CHASE",
                reason="LATE_CHASE: Price extension 2.0% exceeds tolerance 0.5%",
                setup=s_nse,
            )
        ],
    )

    html_nse = format_scan_report_html([s_nse], market="NSE", slate=slate)
    sec_2a = html_nse.split("SECTION 2A: FRESH ACTIONABLE ENTRIES (Playable Now)</b>")[1].split("SECTION 2B:")[0]
    assert "<i>(None)</i>" in sec_2a
    assert "Fresh but rejected" in html_nse
    assert "RELIANCE.NS" in html_nse
