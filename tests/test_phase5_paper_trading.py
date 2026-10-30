"""
Phase 5 Verification Test Suite: Paper Trading Daemon & Go/No-Go Gate Auditor
=============================================================================
Verification Artifact for Milestone 5 (tests/test_phase5_paper_trading.py)

Validates:
1. Anti-wick closed candle ingestion and next-bar open fill mechanics
2. Execution variance and slippage audit recording (Delta_slip in dollars and R)
3. Active position scale-out (50% at TP1, trailing stop to breakeven, TP2 exit)
4. Telemetry and order ledger JSONL persistence
5. Pre-declared Go/No-Go release gate evaluation and report artifact generation
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

from breakout_scanner.paper_trading.models import PaperOrder, ExecutionVarianceRecord
from breakout_scanner.paper_trading.daemon import PaperTradingDaemon
from breakout_scanner.paper_trading.audit import GoNoGoAuditor, DEFAULT_REPORT_PATH
from breakout_scanner.models import Setup, State


def create_candle_series(open_p: float, high_p: float, low_p: float, close_p: float, ts_str: str = "2024-01-01 10:00:00") -> pd.Series:
    """Helper to construct a deterministic candle Series."""
    s = pd.Series({"Open": open_p, "High": high_p, "Low": low_p, "Close": close_p})
    s.name = pd.Timestamp(ts_str)
    return s


def test_daemon_initialization(tmp_path):
    """Verify daemon initializes with clean equity, empty active positions, and ledger paths."""
    order_file = str(tmp_path / "orders.jsonl")
    perf_file = str(tmp_path / "perf.jsonl")

    daemon = PaperTradingDaemon(initial_equity=50000.0, order_ledger_path=order_file, perf_ledger_path=perf_file)

    assert daemon.current_equity == 50000.0
    assert daemon.peak_equity == 50000.0
    assert len(daemon.pending_orders) == 0
    assert len(daemon.active_positions) == 0
    assert len(daemon.completed_trades) == 0


def test_daemon_order_fill_and_slippage_tracking(tmp_path):
    """Verify that a pending order fills at the next-bar Open with realistic slippage and variance audit."""
    order_file = str(tmp_path / "orders.jsonl")
    perf_file = str(tmp_path / "perf.jsonl")
    daemon = PaperTradingDaemon(order_ledger_path=order_file, perf_ledger_path=perf_file)

    # Manually queue a pending order from previous bar
    order = PaperOrder(
        order_id="TEST001",
        symbol="AAPL",
        direction=1,
        shares=100.0,
        theoretical_price=100.0,
        signal_timestamp="2024-01-01 10:00:00",
        stop_loss=96.0,
        take_profit_1=104.0,
        take_profit_2=108.0,
    )
    daemon.pending_orders["AAPL"] = order

    # Ingest next-bar open candle at 101.0
    next_candle = create_candle_series(open_p=101.0, high_p=103.0, low_p=100.8, close_p=102.5, ts_str="2024-01-01 11:00:00")
    dummy_hist = pd.DataFrame([next_candle])

    daemon.ingest_closed_candle("AAPL", next_candle, dummy_hist)

    # Order must be filled and active position opened
    assert "AAPL" in daemon.active_positions
    pos = daemon.active_positions["AAPL"]
    assert pos["shares_remaining"] == 100.0
    # Fill price should be >= 101.0 (open + slippage)
    assert pos["entry_price"] >= 101.0

    # Execution variance audit must be recorded
    assert len(daemon.variance_records) == 1
    v = daemon.variance_records[0]
    assert v.symbol == "AAPL"
    assert v.actual_fill_price >= 101.0
    assert v.slippage_abs > 0.0
    assert v.slippage_r > 0.0

    # JSONL order ledger written
    assert os.path.exists(order_file)
    with open(order_file, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["order_id"] == "TEST001"
        assert data["status"] == "FILLED"


def test_daemon_partial_tp1_and_breakeven_stop_exit(tmp_path):
    """Verify position scales out 50% at TP1, trails stop to breakeven, and exits at breakeven stop."""
    order_file = str(tmp_path / "orders.jsonl")
    perf_file = str(tmp_path / "perf.jsonl")
    daemon = PaperTradingDaemon(order_ledger_path=order_file, perf_ledger_path=perf_file)

    # Setup active position
    daemon.active_positions["AAPL"] = {
        "symbol": "AAPL",
        "direction": 1,
        "shares_total": 100.0,
        "shares_remaining": 100.0,
        "entry_price": 100.0,
        "current_stop": 96.0,
        "tp1": 104.0,
        "tp2": 108.0,
        "tp1_hit": False,
        "open_time": "2024-01-01 10:00:00",
        "bars_held": 0,
        "order_id": "P01",
        "risk_cash": 400.0,
        "realized_pnl": 0.0,
        "atr": 2.0
    }

    # Bar 1: Reaches High 105.0 -> Touches TP1 (104.0)
    bar1 = create_candle_series(open_p=101.0, high_p=105.0, low_p=100.5, close_p=103.5, ts_str="2024-01-01 11:00:00")
    daemon.ingest_closed_candle("AAPL", bar1, pd.DataFrame([bar1]))

    pos = daemon.active_positions["AAPL"]
    assert pos["tp1_hit"] is True
    assert pos["shares_remaining"] == 50.0  # Exactly 50% scale-out
    assert pos["current_stop"] == 100.0     # Stop trailed to breakeven!
    assert pos["realized_pnl"] == pytest.approx(200.0, abs=1e-2)  # 50 shares * $4 profit

    # Bar 2: Drops back to 99.0 -> Triggers Breakeven Stop at 100.0
    bar2 = create_candle_series(open_p=102.0, high_p=102.5, low_p=99.0, close_p=99.5, ts_str="2024-01-01 12:00:00")
    daemon.ingest_closed_candle("AAPL", bar2, pd.DataFrame([bar1, bar2]))

    # Position must be closed
    assert "AAPL" not in daemon.active_positions
    assert len(daemon.completed_trades) == 1
    closed = daemon.completed_trades[0]
    assert closed["exit_reason"] == "STOP_LOSS"
    assert closed["net_pnl"] == pytest.approx(200.0, abs=1e-2)
    assert closed["realized_r"] == pytest.approx(0.50, abs=1e-2)


def test_go_no_go_auditor_pass(tmp_path):
    """Verify that robust performance satisfying all 5 criteria results in GO verdict."""
    auditor = GoNoGoAuditor(min_expectancy_r=0.20, min_profit_factor=1.40, max_drawdown_pct=5.0, min_completed_trades=5)

    # 5 trades with solid positive expectancy
    trades = [
        {"net_pnl": 400.0, "realized_r": 1.00},
        {"net_pnl": 400.0, "realized_r": 1.00},
        {"net_pnl": 200.0, "realized_r": 0.50},
        {"net_pnl": -200.0, "realized_r": -0.50},
        {"net_pnl": 200.0, "realized_r": 0.50},
    ]

    report = auditor.evaluate_performance(trades, critical_defects_count=0)

    assert "GO" in report.overall_verdict
    assert "NO-GO" not in report.overall_verdict
    assert all(c.passed for c in report.checks)

    # Write report
    report_file = str(tmp_path / "paper_trading_audit.md")
    md = auditor.write_markdown_report(report, output_path=report_file)

    assert os.path.exists(report_file)
    assert "GO FOR MICRO-CAPITAL LIVE RELEASE" in md
    assert "✅ PASS" in md


def test_go_no_go_auditor_fail_due_to_drawdown(tmp_path):
    """Verify that excessive drawdown or negative expectancy trips the NO-GO gate."""
    auditor = GoNoGoAuditor(min_expectancy_r=0.20, max_drawdown_pct=5.0, min_completed_trades=5)

    # Trades with large losses creating > 5% drawdown
    trades = [
        {"net_pnl": 200.0, "realized_r": 0.50},
        {"net_pnl": -3000.0, "realized_r": -3.00},
        {"net_pnl": -4000.0, "realized_r": -4.00},
        {"net_pnl": 100.0, "realized_r": 0.25},
        {"net_pnl": 100.0, "realized_r": 0.25},
    ]

    report = auditor.evaluate_performance(trades, critical_defects_count=0)

    assert "NO-GO" in report.overall_verdict
    # Max DD check and Expectancy check must fail
    dd_check = [c for c in report.checks if c.criterion_name == "Max Paper Portfolio Drawdown"][0]
    assert dd_check.passed is False

