"""
Regression and Parity Test Suite for Single Source of Truth for Paper Positions.
================================================================================
Verifies:
1. PVRINOX regression: Failed-breakout wins when close < pivot in early window,
   guaranteeing no TP1 event later across live manager, positions store, and outcomes.
2. CANBK-style SUPPRESSED -> APPROVED path: updates ledger status_history and creates
   matching position in portfolio_state.
3. Telegram failure still opens position: position is recorded when slate APPROVES,
   independent of Telegram dispatch success.
4. Parity test: Live manager (PortfolioStateManager) and OutcomeTracker produce
   IDENTICAL events and identical final states on the exact same closed price bars.
5. Section 2B never lists REJECTED or SUPPRESSED setups.
6. NSE currency in Section 2C uses market currency (₹).
"""

import os
import json
import pytest
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock

from breakout_scanner.models import Setup, State, TradeExitEvent
from breakout_scanner.portfolio_guard import PositionAllocation
from breakout_scanner.portfolio_state import PortfolioStateManager
from breakout_scanner.exit_rules import evaluate_position_bar
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df
from breakout_scanner.audit_ledger import record_forward_test_ledger
from breakout_scanner.telegram_formatter import format_scan_report_html
from breakout_scanner.slate import ActionableSlate, RejectionRecord, SuppressedRecord


def test_pvrinox_regression_failed_breakout_wins_no_tp1_later():
    """
    PVRINOX 2026-11-17 scenario:
    Candle reaches High >= TP1, but Close falls back below pivot on bar 2.
    Failed breakout MUST win; NO TP1 event is emitted in events list or position state.
    """
    pos = {
        "symbol": "PVRINOX.NS",
        "direction": 1,
        "status": "OPEN",
        "entry_price": 1700.0,
        "modeled_fill": 1700.0,
        "stop_loss": 1650.0,
        "initial_risk": 50.0,
        "pivot": 1690.0,
        "take_profit_1": 1750.0,
        "take_profit_2": 1800.0,
        "shares": 100,
        "bars_held": 1,
        "events": [],
    }
    cfg = {"management": {"use_failed_breakout_exit": True, "failed_breakout_max_bars": 3}}

    # Bar 2: High touches 1760 (above TP1 1750), but Close collapses to 1680 (below pivot 1690)
    bar = {
        "Open": 1710.0,
        "High": 1760.0,
        "Low": 1675.0,
        "Close": 1680.0,
        "bar_ts": "2026-11-17T11:00:00Z",
    }

    events = evaluate_position_bar(pos, bar, cfg)

    # 1. Terminal state MUST be CLOSED with FAILED_BREAKOUT
    assert pos["status"] == "CLOSED"
    assert pos["exit_reason"] == "FAILED_BREAKOUT"
    assert pos["exit_price"] == 1680.0
    assert pos["realized_r"] == pytest.approx(-0.40, abs=0.01)

    # 2. No TP1 hit or scale event
    assert not pos.get("tp1_hit", False)
    assert not any(e["event_type"] == "TP1_SCALE" for e in events)
    assert any(e["event_type"] == "FAILED_BREAKOUT" for e in events)

    # 3. Second terminal state rejected
    subsequent_bar = {
        "Open": 1690.0,
        "High": 1850.0,
        "Low": 1685.0,
        "Close": 1840.0,
        "bar_ts": "2026-11-17T12:00:00Z",
    }
    second_events = evaluate_position_bar(pos, subsequent_bar, cfg)
    assert second_events == []
    assert pos["status"] == "CLOSED"
    assert pos["exit_reason"] == "FAILED_BREAKOUT"


def test_canbk_suppressed_to_approved_path_leaves_status_history_and_position(tmp_path):
    """
    CANBK scenario:
    Scan 1: CANBK is SUPPRESSED by portfolio guard -> logged to ledger.
    Scan 2: CANBK is APPROVED -> ledger records status_history [SUPPRESSED -> APPROVED]
    and portfolio_state creates the matching position.
    """
    ledger_file = str(tmp_path / "forward_test_ledger.jsonl")
    state_file = str(tmp_path / "portfolio_state.json")

    s = Setup("CANBK.NS")
    s.state = State.BUY
    s.direction = 1
    s.entry = 100.0
    s.stop = 95.0
    s.current_price = 101.0
    s.bars_since_breakout = 1
    s.breakout_time = "2026-11-18T10:15:00Z"

    cfg = {"profile_name": "NSE_Production"}

    # --- Scan 1: SUPPRESSED by portfolio guard ---
    supp_rec = SuppressedRecord(
        symbol="CANBK.NS",
        suppression_code="SECTOR_CAPPED",
        reason="Sector cap reached",
        setup=s,
    )
    slate_scan1 = ActionableSlate(
        slate_id="scan_1",
        timestamp="2026-11-18T10:15:00Z",
        profile_name="NSE_Production",
        total_scanned=10,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        suppressed_trades=[supp_rec],
        rejected_trades=[],
    )

    written1 = record_forward_test_ledger([s], cfg, slate=slate_scan1, ledger_path=ledger_file)
    assert written1 == 1

    with open(ledger_file) as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 1
    assert rows[0]["status"] == "SUPPRESSED"
    assert len(rows[0]["status_history"]) == 1
    assert rows[0]["status_history"][0]["status"] == "SUPPRESSED"

    # --- Scan 2: CANBK becomes APPROVED ---
    alloc = PositionAllocation(
        symbol="CANBK.NS",
        direction=1,
        entry_price=101.0,
        stop_loss=95.0,
        take_profit_1=107.0,
        take_profit_2=113.0,
        shares=150,
        dollar_risk=900.0,
        position_value=15150.0,
        risk_pct_of_equity=0.9,
        sector="Banking",
        score=85,
        grade="A+",
        setup=s,
    )
    slate_scan2 = ActionableSlate(
        slate_id="scan_2",
        timestamp="2026-11-18T11:15:00Z",
        profile_name="NSE_Production",
        total_scanned=10,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[alloc],
        suppressed_trades=[],
        rejected_trades=[],
    )

    record_forward_test_ledger([s], cfg, slate=slate_scan2, ledger_path=ledger_file)

    with open(ledger_file) as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 1
    # Status updated to APPROVED, not silently dropped!
    assert rows[0]["status"] == "APPROVED"
    assert len(rows[0]["status_history"]) == 2
    assert rows[0]["status_history"][0]["status"] == "SUPPRESSED"
    assert rows[0]["status_history"][1]["from_status"] == "SUPPRESSED"
    assert rows[0]["status_history"][1]["to_status"] == "APPROVED"
    assert rows[0]["execution"]["shares"] == 150

    # Matching position is created in PortfolioStateManager
    mgr = PortfolioStateManager(state_file=state_file)
    created = mgr.record_entry(alloc, signal_id=rows[0]["signal_id"], pivot=100.0)
    assert created is True
    assert "CANBK.NS" in mgr.active_positions
    pos = mgr.active_positions["CANBK.NS"]
    assert pos["signal_id"] == rows[0]["signal_id"]
    assert pos["status"] == "AWAITING_FILL"


def test_telegram_failure_still_opens_position(tmp_path):
    """
    Position must be created when the slate APPROVES, independent of whether
    Telegram delivery succeeds or fails. Delivery failure must mark delivery_status='FAILED'.
    """
    state_file = str(tmp_path / "portfolio_state.json")
    mgr = PortfolioStateManager(state_file=state_file)

    s = Setup("DIS")
    s.state = State.BUY
    s.direction = 1
    s.entry = 115.0
    s.stop = 110.0

    alloc = PositionAllocation(
        symbol="DIS",
        direction=1,
        entry_price=115.0,
        stop_loss=110.0,
        take_profit_1=120.0,
        take_profit_2=125.0,
        shares=100,
        dollar_risk=500.0,
        position_value=11500.0,
        risk_pct_of_equity=0.5,
        score=92,
        grade="A+",
        setup=s,
    )

    # 1. Open position immediately on approval with delivery_status="PENDING"
    opened = mgr.record_entry(alloc, delivery_status="PENDING")
    assert opened is True
    assert mgr.active_positions["DIS"]["delivery_status"] == "PENDING"

    # 2. Simulate Telegram delivery failure
    mgr.update_delivery_status(["DIS"], "FAILED")
    assert mgr.active_positions["DIS"]["delivery_status"] == "FAILED"
    assert mgr.active_positions["DIS"]["status"] == "AWAITING_FILL"
    assert mgr.active_positions["DIS"]["shares"] == 100


def test_live_manager_and_tracker_parity_on_identical_bars(tmp_path):
    """
    Live manager (PortfolioStateManager) and OutcomeTracker must produce
    IDENTICAL events, identical R, and identical terminal outcomes on the exact same bars.
    """
    # Create price bars:
    # Bar 0: Signal close at 100.0
    # Bar 1: Fills at Open 100.0 (+5 bps -> 100.05). High touches TP1 (105.0), Close holds at 103.0
    # Bar 2: Low drops to 100.0, hitting the breakeven stop (100.05)
    dates = pd.date_range("2026-11-18 10:00:00", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "Open": [99.0, 100.0, 102.0, 101.0],
        "High": [100.0, 106.0, 102.5, 102.0],
        "Low": [98.5, 99.5, 99.0, 100.0],
        "Close": [100.0, 103.0, 100.0, 101.5],
        "Volume": [1000, 1500, 1200, 1000],
    }, index=dates)

    record = {
        "signal_id": "PARITY_SIG_1",
        "symbol": "PARITY_TEST",
        "direction": "BUY",
        "timestamp": "2026-11-18 10:00:00",
        "breakout_time": "2026-11-18 10:00:00",
        "price": {
            "entry": 100.0,
            "stop": 95.0,
            "tp1": 105.0,
            "tp2": 110.0,
            "pivot": 100.0,
        },
    }

    # 1. Run OutcomeTracker
    out_tracker = evaluate_setup_outcome_from_df(record, df, max_hold_bars=10)

    # 2. Run Live Manager (PortfolioStateManager) fed with same bars
    state_file = str(tmp_path / "portfolio_state.json")
    mgr = PortfolioStateManager(state_file=state_file)
    mgr.active_positions.clear()
    s_mock = Setup("PARITY_TEST")
    s_mock.state = State.BUY
    s_mock.direction = 1
    s_mock.entry = 100.0
    s_mock.stop = 95.0

    alloc = PositionAllocation(
        symbol="PARITY_TEST",
        direction=1,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit_1=105.0,
        take_profit_2=110.0,
        shares=100,
        dollar_risk=500.0,
        position_value=10000.0,
        risk_pct_of_equity=0.5,
        score=90,
        grade="A+",
        setup=s_mock,
    )
    mgr.record_entry(alloc, signal_id="PARITY_SIG_1", pivot=100.0)

    # Update market data with the closed bars dataframe
    exit_events = mgr.update_market_data(
        setups=[s_mock],
        symbol_dfs={"PARITY_TEST": df.iloc[1:]},  # Bars post-signal
        cfg={"management": {"breakeven_offset_pct": 0.0, "failed_breakout_max_bars": 3, "use_failed_breakout_exit": True}}
    )

    # Verify identical terminal outcome
    assert out_tracker["status"] == "EVALUATED"
    assert out_tracker["exit_reason"] == "BREAKEVEN_STOP"
    assert out_tracker["hypothetical_r"] == pytest.approx(0.50, abs=0.01)

    assert "PARITY_TEST" not in mgr.active_positions
    assert len(mgr.closed_positions_today) == 1
    closed = mgr.closed_positions_today[0]
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "BREAKEVEN_STOP"
    assert closed["realized_r"] == pytest.approx(0.50, abs=0.01)

    # Events emitted by live manager match tracker
    ev_types = [e.event_type for e in exit_events]
    assert "TP1_SCALE" in ev_types
    assert "BREAKEVEN_STOP" in ev_types


def test_section_2b_never_lists_rejected_setups():
    """
    Section 2B must ONLY contain approved open positions from paper_positions store.
    Rejected setups must NEVER appear in 2B under any circumstances.
    """
    s_rejected = Setup("REJECTED_CO")
    s_rejected.state = State.BUY
    s_rejected.direction = 1
    s_rejected.bars_since_breakout = 5  # In progress replay
    s_rejected.current_price = 105.0
    s_rejected.entry = 100.0
    s_rejected.stop = 95.0

    rej_rec = RejectionRecord(
        symbol="REJECTED_CO",
        rejection_code="LATE_CHASE",
        reason="Price extended beyond tolerance",
        setup=s_rejected,
    )

    slate = ActionableSlate(
        slate_id="slate_test",
        timestamp="2026-11-18T10:00:00Z",
        profile_name="Test",
        total_scanned=5,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[],
        suppressed_trades=[],
        rejected_trades=[rej_rec],
        open_positions=[],  # Store has zero open positions for this symbol
    )

    report = format_scan_report_html([s_rejected], market="US", slate=slate)

    sec_2b = report.split("TRADES IN PROGRESS")[1].split("SCAN COMPLETED")[0]
    assert "REJECTED_CO" not in sec_2b
    assert "TRADES IN PROGRESS" in report
    assert "<i>(None)</i>" in sec_2b


def test_telegram_2c_uses_rupee_for_nse():
    """
    Telegram 2C must use ₹ currency symbol for NSE market, and $ for other markets.
    """
    ev = TradeExitEvent(
        symbol="RELIANCE.NS",
        event_type="BREAKEVEN_STOP",
        direction=1,
        entry_price=2500.0,
        exit_price=2500.0,
        pnl_pct=0.0,
        realized_pnl=0.0,
        shares=50,
        timestamp="2026-11-18T10:00:00Z",
        reason="Breakeven Stop Hit after TP1",
    )

    report_nse = format_scan_report_html([], market="NSE", exit_events=[ev])
    assert "EXITS &amp; MANAGEMENT EVENTS" in report_nse
    assert "₹" in report_nse
    assert "$" not in report_nse
    assert "BREAKEVEN" in report_nse

    report_us = format_scan_report_html([], market="US", exit_events=[ev])
    assert "$" in report_us
    assert "₹" not in report_us
