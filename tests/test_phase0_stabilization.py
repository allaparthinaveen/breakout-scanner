import os
import json
import time
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from breakout_scanner.models import Setup, State
from breakout_scanner.safeguards import ProductionSafeguards
from breakout_scanner.paper_trading.audit import (
    IncidentRecord,
    IncidentLedger,
    GoNoGoAuditor
)
from breakout_scanner.paper_trading.daemon import PaperTradingDaemon
from breakout_scanner.scheduler import CandleAlertDeduplicator, SessionScheduler
from breakout_scanner.realtime import RealtimeFeed, PrototypeMockFeed
from breakout_scanner.paper_broker import PaperBroker
import live_scanner


# =====================================================================
# 0.1 Fix live_scanner.py Startup Scan Logic Tests
# =====================================================================

def test_live_scanner_cli_daemon_without_now():
    """Verify that --daemon without --now or --startup-scan does NOT run immediately."""
    with patch("live_scanner.LiveScanner") as mock_scanner_cls:
        mock_instance = MagicMock()
        mock_scanner_cls.return_value = mock_instance

        live_scanner.main(["--daemon"])

        mock_instance.schedule_hourly.assert_called_once_with(minute_offset=1)
        # Must be called with run_immediately=False!
        mock_instance.start.assert_called_once_with(run_immediately=False)


def test_live_scanner_cli_daemon_with_now():
    """Verify that --daemon --now sets run_immediately=True."""
    with patch("live_scanner.LiveScanner") as mock_scanner_cls:
        mock_instance = MagicMock()
        mock_scanner_cls.return_value = mock_instance

        live_scanner.main(["--daemon", "--now"])

        mock_instance.schedule_hourly.assert_called_once_with(minute_offset=1)
        mock_instance.start.assert_called_once_with(run_immediately=True)


def test_live_scanner_cli_daemon_with_startup_scan():
    """Verify that --daemon --startup-scan sets run_immediately=True."""
    with patch("live_scanner.LiveScanner") as mock_scanner_cls:
        mock_instance = MagicMock()
        mock_scanner_cls.return_value = mock_instance

        live_scanner.main(["--daemon", "--startup-scan"])

        mock_instance.schedule_hourly.assert_called_once_with(minute_offset=1)
        mock_instance.start.assert_called_once_with(run_immediately=True)


def test_live_scanner_cli_default_one_off_mode():
    """Verify that omitting --daemon runs an immediate one-off scan."""
    with patch("live_scanner.LiveScanner") as mock_scanner_cls:
        mock_instance = MagicMock()
        mock_instance.run_scan.return_value = {"scanned": 5, "alerts_sent": 1}
        mock_scanner_cls.return_value = mock_instance

        live_scanner.main([])

        mock_instance.run_scan.assert_called_once()
        mock_instance.start.assert_not_called()


# =====================================================================
# 0.2 Wire ProductionSafeguards into Live Alert/Scan Flow Tests
# =====================================================================

def test_safeguards_kill_switch_blocks_scheduler():
    """When kill-switch is active, scheduler aborts scan without querying symbols."""
    cfg = {"risk": {"max_daily_loss_pct": 2.0, "account_size": 100000.0}}
    scheduler = SessionScheduler(
        cfg_path="config/default.yaml",
        watchlist_files=[],
    )
    scheduler.scanner.scan = MagicMock()
    scheduler.safeguards.engage_kill_switch("Manual test trigger")

    result = scheduler.scan_and_alert()
    assert result["status"] == "HALTED_BY_KILL_SWITCH"
    scheduler.scanner.scan.assert_not_called()


def test_safeguards_env_var_kill_switch():
    """Setting KILL_SWITCH=1 in environment activates kill switch on init."""
    with patch.dict(os.environ, {"KILL_SWITCH": "1"}):
        safeguards = ProductionSafeguards({})
        assert safeguards.kill_switch_active is True


def test_safeguards_lockfile_trigger(tmp_path):
    """Creating lockfile triggers kill-switch engagement."""
    kill_file = str(tmp_path / "KILL_SWITCH")
    with open(kill_file, "w") as f:
        f.write("HALT")

    safeguards = ProductionSafeguards({"safeguards": {"kill_switch_file": kill_file}})
    assert safeguards.kill_switch_active is True


def test_safeguards_daily_loss_limit_circuit_breaker():
    """Breaching daily loss threshold engages kill switch and halts execution."""
    cfg = {"risk": {"max_daily_loss_pct": 2.0, "account_size": 100000.0}}
    safeguards = ProductionSafeguards(cfg)
    safeguards.daily_pnl = -2500.0  # -2.5% loss on 100k

    allowed = safeguards.check_daily_loss_limit(current_capital=100000.0)
    assert allowed is False
    assert safeguards.kill_switch_active is True


def test_safeguards_stale_data_rejection():
    """Candle data older than allowed threshold is rejected by safeguards."""
    safeguards = ProductionSafeguards({"safeguards": {"max_stale_data_seconds": 120}})
    stale_timestamp = time.time() - 300  # 300s old

    assert safeguards.check_stale_data("AAPL", stale_timestamp) is False

    fresh_timestamp = time.time() - 30  # 30s old
    assert safeguards.check_stale_data("AAPL", fresh_timestamp) is True


def test_safeguards_liquidity_spread_filter():
    """Wide spreads or illiquid volumes are rejected."""
    safeguards = ProductionSafeguards({
        "risk": {"max_spread_pct": 0.5, "min_liquidity_volume": 10000}
    })

    # 1.0% spread (too wide)
    assert safeguards.check_liquidity_spread("ILLIQ", bid=99.0, ask=100.0, volume=50000) is False

    # Low volume
    assert safeguards.check_liquidity_spread("LOWVOL", bid=100.0, ask=100.1, volume=500) is False

    # Acceptable spread (0.1%) & volume
    assert safeguards.check_liquidity_spread("GOOD", bid=100.0, ask=100.1, volume=25000) is True


def test_safeguards_max_structural_stop_cap():
    """Setups with risk percentage greater than max allowable stop are rejected."""
    safeguards = ProductionSafeguards({
        "rejection_gate": {"max_stop_pct": 5.0}
    })

    setup_wide = Setup(symbol="WIDE", risk_pct=6.5)
    setup_normal = Setup(symbol="OK", risk_pct=2.5)

    assert safeguards.pre_trade_validation(setup_wide, current_capital=100000.0) is False
    assert safeguards.pre_trade_validation(setup_normal, current_capital=100000.0) is True


# =====================================================================
# 0.3 Dynamic Incident Ledger & Go/No-Go Audit Tests
# =====================================================================

def test_incident_ledger_recording_and_persistence(tmp_path):
    ledger_path = str(tmp_path / "incidents.jsonl")
    ledger = IncidentLedger(ledger_path=ledger_path)

    ledger.record_incident(
        category="EXCEPTION",
        message="ZeroDivisionError in stop calculation",
        severity="CRITICAL",
        symbol="XYZ"
    )
    ledger.record_incident(
        category="DATA_GAP",
        message="Missing 1 bar",
        severity="WARNING",
        symbol="ABC"
    )

    assert len(ledger.incidents) == 2
    assert ledger.critical_count == 1
    assert len(ledger.get_critical_incidents()) == 1

    # Reload ledger from file
    loaded_ledger = IncidentLedger(ledger_path=ledger_path)
    assert len(loaded_ledger.incidents) == 2
    assert loaded_ledger.critical_count == 1
    assert loaded_ledger.incidents[0].category == "EXCEPTION"


def test_gonogo_auditor_rejects_on_critical_defects(tmp_path):
    """When incident ledger has critical defects, Go/No-Go audit verdict MUST be NO-GO."""
    ledger = IncidentLedger()
    ledger.record_incident("STATE_DESYNC", "Broker position out of sync with ledger", "CRITICAL")

    auditor = GoNoGoAuditor(min_completed_trades=2)
    trades = [
        {"net_pnl": 500.0, "realized_r": 1.5},
        {"net_pnl": 400.0, "realized_r": 1.2},
    ]

    report = auditor.evaluate_performance(
        completed_trades=trades,
        incident_ledger=ledger
    )

    assert "NO-GO" in report.overall_verdict
    defect_check = next(c for c in report.checks if c.criterion_name == "Critical System Defects")
    assert defect_check.passed is False
    assert defect_check.actual_value_str == "1 defects"

    md_report = auditor.write_markdown_report(
        report,
        output_path=str(tmp_path / "audit.md"),
        incident_ledger=ledger
    )
    assert "Operational Incident & Defect Ledger" in md_report
    assert "STATE_DESYNC" in md_report


# =====================================================================
# 0.4 Atomic Persistence & SQLite Deduplicator Tests
# =====================================================================

def test_atomic_json_deduplicator(tmp_path):
    cache_path = str(tmp_path / "dedup.json")
    dedup = CandleAlertDeduplicator(cache_path)

    sig = dedup.build_signature("AAPL", "BREAKOUT", "2026-11-09 09:30:00")
    assert dedup.is_duplicate(sig) is False

    dedup.record_alert(sig, {"symbol": "AAPL", "entry": 220.0})
    assert dedup.is_duplicate(sig) is True

    # Ensure file exists and contains valid JSON
    assert os.path.exists(cache_path)
    with open(cache_path, "r") as f:
        data = json.load(f)
        assert sig in data


def test_sqlite_deduplicator(tmp_path):
    db_path = str(tmp_path / "dedup.sqlite")
    dedup = CandleAlertDeduplicator(db_path)

    sig1 = dedup.build_signature("NVDA", "BREAKOUT", "2026-11-09 10:00:00")
    sig2 = dedup.build_signature("MSFT", "RETEST", "2026-11-09 10:00:00")

    assert dedup.is_duplicate(sig1) is False
    dedup.record_alert(sig1, {"price": 140.0})
    assert dedup.is_duplicate(sig1) is True
    assert dedup.is_duplicate(sig2) is False

    # Test clear
    dedup.clear()
    assert dedup.is_duplicate(sig1) is False


# =====================================================================
# 0.5 Realtime Mock & Simulator Boundary Demarcation Tests
# =====================================================================

def test_realtime_prototype_mock_alias():
    """Verify PrototypeMockFeed alias exists and references RealtimeFeed."""
    assert PrototypeMockFeed is RealtimeFeed


def test_paper_broker_research_warning_docstring():
    """Verify PaperBroker docstring explicitly identifies itself as research simulator only."""
    assert "RESEARCH & PROTOTYPE SIMULATOR ONLY" in PaperBroker.__doc__
    assert "Not an exchange execution venue" in PaperBroker.__doc__
