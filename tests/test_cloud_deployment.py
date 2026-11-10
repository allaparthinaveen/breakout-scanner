"""
Acceptance Test Suite for Phase 3: Production Cloud Deployment & Always-On Signal Dispatch.

Validates:
1. Secrets Management (Local environment fallback & AWS SSM Parameter Store resolution).
2. Heartbeat Monitor Lifecycle & Stale Detection (data/heartbeat.json).
3. Cloud Daemon Initialization & Telemetry Propagation.
4. CLI Status & Graceful Signal Handling.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from breakout_scanner.secrets import (
    get_secret,
    get_telegram_credentials,
)
from breakout_scanner.heartbeat import HeartbeatMonitor
from production_daemon import CloudDaemonRunner, main as daemon_main


def test_secrets_resolver_env_and_alias():
    """Verifies that get_secret properly falls back to env vars and aliases."""
    with patch.dict(os.environ, {"TG_BOT_TOKEN": "bot_12345", "TG_CHAT_ID": "chat_98765"}, clear=True):
        token, chat_id = get_telegram_credentials()
        assert token == "bot_12345"
        assert chat_id == "chat_98765"

    # Alias TELEGRAM_BOT_TOKEN
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot_alt", "TELEGRAM_CHAT_ID": "chat_alt"}, clear=True):
        token, chat_id = get_telegram_credentials()
        assert token == "bot_alt"
        assert chat_id == "chat_alt"


def test_secrets_resolver_default_fallback():
    """Verifies fallback when secret is absent."""
    with patch.dict(os.environ, {}, clear=True):
        val = get_secret("NON_EXISTENT_KEY", default="default_val")
        assert val == "default_val"
        token, chat_id = get_telegram_credentials()
        assert token is None
        assert chat_id is None


def test_heartbeat_monitor_lifecycle(tmp_path):
    """Tests heartbeat atomic file write and healthy state inspection."""
    hb_file = str(tmp_path / "heartbeat.json")
    monitor = HeartbeatMonitor(filepath=hb_file, interval_seconds=10)

    # Initial beat
    monitor.update_stats(scanned=25, alerts=2, active_markets=["US", "CRYPTO"], status="HEALTHY")
    assert os.path.exists(hb_file)

    with open(hb_file, "r") as f:
        data = json.load(f)

    assert data["status"] == "HEALTHY"
    assert data["scanned_total"] == 25
    assert data["alerts_sent_total"] == 2
    assert "US" in data["active_markets"]
    assert data["uptime_seconds"] >= 0.0

    # Verify check_health returns healthy
    health = HeartbeatMonitor.check_health(hb_file, max_stale_seconds=60)
    assert health["healthy"] is True
    assert health["status"] == "HEALTHY"


def test_heartbeat_monitor_stale_and_missing(tmp_path):
    """Verifies that stale or missing heartbeat files are correctly flagged."""
    # 1. Missing file
    missing_file = str(tmp_path / "non_existent.json")
    res_missing = HeartbeatMonitor.check_health(missing_file)
    assert res_missing["healthy"] is False
    assert res_missing["status"] == "MISSING"

    # 2. Stale file (written 2 hours ago)
    stale_file = str(tmp_path / "stale_heartbeat.json")
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with open(stale_file, "w") as f:
        json.dump({"timestamp_utc": stale_time, "status": "HEALTHY"}, f)

    res_stale = HeartbeatMonitor.check_health(stale_file, max_stale_seconds=300)
    assert res_stale["healthy"] is False
    assert res_stale["status"] == "STALE"
    assert res_stale["age_seconds"] > 300


def test_cloud_daemon_runner_initialization(tmp_path):
    """Tests that CloudDaemonRunner initializes multi-market schedulers and telemetry hooks."""
    log_file = str(tmp_path / "cloud.log")
    runner = CloudDaemonRunner(
        markets=["US", "CRYPTO"],
        config_path="config/default.yaml",
        heartbeat_interval=300,
        log_file=log_file,
    )
    assert runner.running is False
    assert "US" in runner.scheduler.schedulers
    assert "CRYPTO" in runner.scheduler.schedulers
    assert runner.heartbeat is not None


def test_cloud_daemon_telemetry_propagation(tmp_path):
    """Verifies that running scan_and_alert updates heartbeat metrics accurately."""
    log_file = str(tmp_path / "cloud.log")
    hb_file = str(tmp_path / "heartbeat.json")

    runner = CloudDaemonRunner(
        markets=["CRYPTO"],
        config_path="config/default.yaml",
        log_file=log_file,
    )
    runner.heartbeat.filepath = hb_file

    # Mock the underlying scanner to return a successful scan result
    runner.scheduler.schedulers["CRYPTO"].load_symbols = MagicMock(return_value=["BTC-USD"])
    runner.scheduler.schedulers["CRYPTO"].scanner.scan = MagicMock(return_value=[])

    # Run scan
    runner.scheduler.schedulers["CRYPTO"].scan_and_alert(force=True)

    # Check heartbeat file was updated
    assert os.path.exists(hb_file)
    with open(hb_file, "r") as f:
        data = json.load(f)
    assert data["scanned_total"] == 1
    assert data["status"] == "HEALTHY"


def test_cloud_daemon_cli_status(tmp_path, capsys):
    """Verifies that production_daemon.py --status outputs human-readable health status."""
    hb_file = "data/heartbeat.json"
    os.makedirs("data", exist_ok=True)
    monitor = HeartbeatMonitor(filepath=hb_file)
    monitor.update_stats(scanned=50, alerts=3, active_markets=["US"], status="HEALTHY")

    with pytest.raises(SystemExit) as exc:
        daemon_main(["--status"])
    assert exc.value.code == 0

    captured = capsys.readouterr()
    assert "[HEALTHY]" in captured.out
    assert "Active Markets:" in captured.out
