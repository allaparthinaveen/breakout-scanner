import os
import json
import pytest
from unittest.mock import MagicMock, patch
from production_daemon import CloudDaemonRunner
from scripts.collect_logs import get_effective_config
from breakout_scanner.config import load_config
from breakout_scanner.audit_ledger import compute_config_hash


def test_daemon_startup_snapshot_per_market_profile(tmp_path):
    """
    ITEM 2 Regression Test:
    Verifies that CloudDaemonRunner startup snapshot:
    1. Emits one daemon_start snapshot per active market (e.g. US, NSE).
    2. Uses the resolved per-market profile (e.g. us_production.yaml, nse_production.yaml).
    3. Snapshot for US shows min_auto_entry_score == 80 and max_stale_data_seconds == 600.
    4. Snapshot config_hash equals the config_hash produced by collect_logs for that profile.
    """
    run_dir = str(tmp_path / "logs" / "run")

    with patch("breakout_scanner.run_logger.log_event") as mock_log_event, \
         patch("schedule.every") as mock_schedule_every, \
         patch("schedule.jobs", []):

        runner = CloudDaemonRunner(
            markets=["US", "NSE"],
            config_path="config/default.yaml",
            log_file=str(tmp_path / "daemon.log"),
        )
        # Mock scheduler schedule_all so it doesn't fail in test environment
        runner.scheduler.schedule_all = MagicMock()

        # Run startup snapshot logic (same as runner.run() startup block)
        # We invoke the startup snapshot block by calling the runner's startup routine or testing the runner method
        runner.emit_startup_snapshots(base_dir=run_dir)

        # Collect all daemon_start calls
        daemon_start_calls = [
            call for call in mock_log_event.call_args_list
            if call.kwargs.get("event") == "daemon_start"
        ]

        # Must emit one snapshot per active market (US and NSE)
        markets_logged = [call.kwargs.get("market") for call in daemon_start_calls]
        assert "US" in markets_logged
        assert "NSE" in markets_logged

        # Check US snapshot
        us_call = next(call for call in daemon_start_calls if call.kwargs.get("market") == "US")
        us_eff = us_call.kwargs.get("effective_config", {})
        us_hash = us_call.kwargs.get("config_hash")
        us_cfg_path = us_call.kwargs.get("config_file_path")

        assert us_cfg_path == "config/profiles/us_production.yaml"
        assert us_eff.get("min_auto_entry_score") == 80
        assert us_eff.get("max_stale_data_seconds") == 600

        # Verify hash matches collect_logs snapshot for US
        collect_us_snapshot = get_effective_config("config/profiles/us_production.yaml")
        assert us_hash == collect_us_snapshot["config_hash"]

        # Check NSE snapshot
        nse_call = next(call for call in daemon_start_calls if call.kwargs.get("market") == "NSE")
        nse_eff = nse_call.kwargs.get("effective_config", {})
        nse_hash = nse_call.kwargs.get("config_hash")
        nse_cfg_path = nse_call.kwargs.get("config_file_path")

        assert nse_cfg_path == "config/profiles/nse_production.yaml"
        assert nse_eff.get("min_auto_entry_score") == 80
        assert nse_eff.get("max_stale_data_seconds") == 600

        collect_nse_snapshot = get_effective_config("config/profiles/nse_production.yaml")
        assert nse_hash == collect_nse_snapshot["config_hash"]
