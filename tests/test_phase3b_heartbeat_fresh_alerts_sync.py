import pytest
from unittest.mock import MagicMock
from breakout_scanner.models import Setup, State
from breakout_scanner.scheduler import SessionScheduler


def test_heartbeat_fresh_alerts_synced_with_scan_summary(tmp_path):
    """
    Heartbeat and run_scan_cycle result must sync 'fresh_alerts' with
    scan_summary.signals_fresh, instead of incrementing 1 on every telegram report.
    """
    cache_file = str(tmp_path / "test_dedup.json")
    scheduler = SessionScheduler(
        cfg_path="config/profiles/us_production.yaml",
        dedup_file=cache_file,
        market_id="US",
        enforce_market_hours=False
    )
    scheduler.send_telegram_alert = MagicMock(return_value=True)

    # Mock scanner with 0 fresh signals
    scheduler.scanner.scan = MagicMock(return_value=[])
    scheduler.scanner.flush_symbol_traces = MagicMock(side_effect=lambda **kwargs: setattr(
        scheduler.scanner, "last_scan_summary", {"signals_fresh": 0}
    ))
    scheduler.scanner.last_scan_summary = {"signals_fresh": 0}

    scheduler.get_watchlist = MagicMock(return_value=["AAPL"])

    # Run scan cycle with force=True so report is sent
    res = scheduler.scan_and_alert(force=True)

    # Even though telegram report was sent, 0 fresh signals were alerted
    assert res["alerts_sent"] == 0
