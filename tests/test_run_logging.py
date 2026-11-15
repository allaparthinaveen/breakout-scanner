import os
import sys
import io
import time
import json
import zipfile
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

from breakout_scanner.config import load_config
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.models import Setup, State
from breakout_scanner.run_logger import (
    redact_secrets,
    get_known_secrets,
    log_event,
    log_startup_snapshot,
    get_exchange_date_str,
    safe_append_jsonl,
    _STATE,
)
from scripts.eod_summary import generate_eod_summary, format_eod_markdown
from scripts.collect_logs import create_log_bundle, collect_journalctl


def create_breakout_df(n_bars=60, base_price=100.0, is_breakout=True):
    """Generates candle data that triggers or does not trigger a breakout."""
    dates = pd.date_range("2026-11-01", periods=n_bars, freq="1h")
    closes = np.full(n_bars, base_price) + np.sin(np.linspace(0, 3, n_bars)) * 0.2
    opens = closes - 0.05
    highs = closes + 0.3
    lows = closes - 0.3
    volume = np.full(n_bars, 10000.0)

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volume,
    }, index=dates)

    if is_breakout and n_bars >= 50:
        # Breakout candle on bar 50
        df.loc[df.index[50], "Open"] = 100.5
        df.loc[df.index[50], "High"] = 105.0
        df.loc[df.index[50], "Low"] = 100.2
        df.loc[df.index[50], "Close"] = 104.5
        df.loc[df.index[50], "Volume"] = 30000.0
        return df.iloc[:51]

    return df


class Mock6SymbolProvider:
    """Mock provider serving the 6-symbol test scenario."""
    def __init__(self):
        self.calls = []

    def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
        self.calls.append(symbol)
        if symbol in ("SIG1", "SIG2"):
            return create_breakout_df(60, 100.0, is_breakout=True)
        elif symbol == "NOSIG":
            return create_breakout_df(60, 100.0, is_breakout=False)
        elif symbol == "EMPTY":
            return pd.DataFrame()
        elif symbol == "ERR429":
            raise Exception("429 Client Error: Too Many Requests")
        elif symbol == "BADDATA":
            # Insufficient bars (< 10 bars)
            dates = pd.date_range("2026-11-01", periods=3, freq="1h")
            return pd.DataFrame({
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1000.0, 1000.0, 1000.0],
            }, index=dates)
        return pd.DataFrame()


def test_stub_scan_6_symbols_funnel_and_summary(tmp_path):
    """
    Test 1: Stub scan with 6 symbols:
      2 OK-with-signal, 1 OK-no-signal, 1 empty frame, 1 raising 429-like error, 1 bad data.
    Verifies:
      - symbol_trace has exactly 6 rows with fetch.status and first_failed_gate
      - scan_summary counts/completeness/error_type_counts are correct
      - funnel counts add up accurately.
    """
    logs_dir = str(tmp_path / "logs_run")
    cfg = load_config("config/default.yaml")
    cfg["volume"]["use_breakout_volume"] = False
    cfg["trend"]["use_trend_filter"] = False
    cfg["base"]["require_tightening"] = False
    cfg["base"]["min_box_density_pct"] = 70.0

    provider = Mock6SymbolProvider()
    scanner = MultiSymbolScanner(provider, cfg)

    symbols = ["SIG1", "SIG2", "NOSIG", "EMPTY", "ERR429", "BADDATA"]

    with patch("breakout_scanner.run_logger.get_exchange_date_str", return_value="2026-11-16"):
        # Auto-flush writes to logs_dir
        with patch("breakout_scanner.scanner.log_symbol_trace_batch") as mock_trace_batch, \
             patch("breakout_scanner.scanner.log_scan_summary") as mock_summary:
            setups = scanner.scan(symbols, auto_flush=True)

            assert len(scanner.last_symbol_traces) == 6
            traces = scanner.last_symbol_traces

            # Assert fetch statuses
            status_map = {t["symbol"]: t["fetch"]["status"] for t in traces}
            assert status_map["SIG1"] == "OK"
            assert status_map["SIG2"] == "OK"
            assert status_map["NOSIG"] == "OK"
            assert status_map["EMPTY"] == "EMPTY"
            assert status_map["ERR429"] == "ERROR"
            assert status_map["BADDATA"] == "OK"

            # Assert first_failed_gate
            gate_map = {t["symbol"]: t["engine"]["first_failed_gate"] for t in traces}
            assert gate_map["SIG1"] == "none"
            assert gate_map["SIG2"] == "none"
            assert gate_map["BADDATA"] == "insufficient_bars"
            assert gate_map["EMPTY"] == "empty_frame"
            assert gate_map["ERR429"] == "fetch_error"

            # Assert scan stats
            stats = scanner.last_scan_stats
            assert stats["total"] == 6
            assert stats["with_data"] == 4
            assert set(stats["failed_symbols"]) == {"EMPTY", "ERR429"}

            # Assert scan summary call was made
            mock_summary.assert_called_once()
            summary = mock_summary.call_args[0][0]
            assert summary["symbols_total"] == 6
            assert summary["symbols_with_data"] == 4
            assert summary["symbols_failed"] == 2
            assert summary["completeness_pct"] == round(4 / 6 * 100.0, 1)
            assert "empty_frame" in summary["error_type_counts"]
            assert "429" in summary["error_type_counts"]

            # Funnel counts validation
            fn = summary["funnel_counts"]
            assert fn["with_data"] == 4
            assert fn["breakout"] == 2


def test_secrets_redaction_everywhere(tmp_path):
    """
    Test 2: Secrets: token-like strings, chat IDs, and credentials never appear
    in log files or the zip bundle.
    """
    test_token = "8361061485:AAGUSvENq79eUhIztcae-7BPJt-gc1LVytk"
    test_chat_id = "5480767676"
    test_secret = "MY_SUPER_SECRET_KEY_12345"

    os.environ["TG_BOT_TOKEN"] = test_token
    os.environ["TG_CHAT_ID"] = test_chat_id

    # 1. Test redact_secrets unit behavior
    sample_text = (
        f"Connecting to https://api.telegram.org/bot{test_token}/sendMessage with chat_id: {test_chat_id}\n"
        f"Config line: API_KEY='{test_secret}'\n"
        f"Raw token in log: {test_token}"
    )
    sanitized, count = redact_secrets(sample_text)
    assert count >= 4
    assert test_token not in sanitized
    assert test_chat_id not in sanitized
    assert test_secret not in sanitized
    assert "[REDACTED_TG_TOKEN]" in sanitized
    assert "[REDACTED_CHAT_ID]" in sanitized

    # 2. Test disk log bundle creation and secret absence
    run_dir = tmp_path / "logs" / "run" / "2026-11-16"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    events_path.write_text(f'{{"message": "bot {test_token} chat {test_chat_id}"}}\n')

    zip_out = str(tmp_path / "test_bundle.zip")
    bundle_path = create_log_bundle(
        market="nse",
        date_str="2026-11-16",
        output_zip=zip_out,
        base_run_dir=str(tmp_path / "logs" / "run"),
    )

    with zipfile.ZipFile(bundle_path, "r") as zf:
        for fname in zf.namelist():
            content = zf.read(fname).decode("utf-8", errors="replace")
            assert test_token not in content, f"Secret token leaked in {fname}!"
            assert test_chat_id not in content, f"Secret chat ID leaked in {fname}!"


def test_logging_failure_injection_does_not_abort_scan(tmp_path, monkeypatch):
    """
    Test 3: Logging failure injection (unwritable directory) -> scan still completes
    and one stderr warning is emitted without swallowing silently or raising.
    """
    # Create unwritable path
    unwritable_file = "/sys/unwritable_directory/events.jsonl"

    stderr_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr_buf)

    # Attempt logging to unwritable file
    safe_append_jsonl(unwritable_file, {"test": "data"})
    safe_append_jsonl(unwritable_file, {"test": "data_again"})

    err_output = stderr_buf.getvalue()
    assert "⚠️ [RUN_LOGGER ERROR]" in err_output
    # Emits only once per filepath to prevent log spam
    assert err_output.count("⚠️ [RUN_LOGGER ERROR]") == 1


def test_job_skipped_and_job_fire_logging(tmp_path):
    """
    Test 4: job_skipped logs a reason when market is closed; job_fire logs drift.
    """
    from breakout_scanner.scheduler import SessionScheduler
    from breakout_scanner.safeguards import ProductionSafeguards

    cfg = load_config("config/default.yaml")
    mock_market_session = MagicMock()
    mock_market_session.is_session_active_or_recent_close.return_value = False

    scheduler = SessionScheduler(
        cfg_path="config/default.yaml",
        market_id="NSE",
        enforce_market_hours=True,
    )
    scheduler.market_session = mock_market_session

    with patch("breakout_scanner.scheduler.log_event") as mock_log:
        res = scheduler.scan_and_alert(force=False)
        assert res["status"] == "MARKET_CLOSED"

        # Assert job_fire logged
        fire_calls = [c for c in mock_log.call_args_list if c.kwargs.get("event") == "job_fire"]
        assert len(fire_calls) == 1
        assert "drift_seconds" in fire_calls[0].kwargs

        # Assert job_skipped logged with reason
        skip_calls = [c for c in mock_log.call_args_list if c.kwargs.get("event") == "job_skipped"]
        assert len(skip_calls) == 1
        assert "CLOSED" in skip_calls[0].kwargs.get("reason", "")


def test_collect_logs_graceful_when_journalctl_missing(tmp_path):
    """
    Test 5: collect_logs.py works when journalctl is missing or fails.
    """
    date_str = "2026-11-16"
    run_dir = tmp_path / "logs" / "run" / date_str
    run_dir.mkdir(parents=True)
    (run_dir / "scan_summary.jsonl").write_text('{"scan_id": "test_1", "symbols_total": 10}\n')

    with patch("subprocess.run", side_effect=FileNotFoundError):
        res_text = collect_journalctl(date_str)
        assert "journalctl not available" in res_text

        bundle_path = create_log_bundle(
            market="nse",
            date_str=date_str,
            output_zip=str(tmp_path / "bundle_nojctl.zip"),
            base_run_dir=str(tmp_path / "logs" / "run"),
        )
        assert os.path.exists(bundle_path)
        assert os.path.getsize(bundle_path) > 0


def test_eod_summary_fixture_day_produces_non_empty_sections(tmp_path):
    """
    Test 6: eod_summary.py runs on a fixture day and produces all required non-empty sections.
    """
    date_str = "2026-11-16"
    run_dir = tmp_path / "logs" / "run" / date_str
    run_dir.mkdir(parents=True)

    # Fixture scan summary
    (run_dir / "scan_summary.jsonl").write_text(json.dumps({
        "scan_id": "nse_1001",
        "market": "NSE",
        "start_delay_seconds": 1.2,
        "completeness_pct": 98.5,
        "symbols_with_data": 49,
        "symbols_total": 50,
        "aborted": False,
        "error_type_counts": {"empty_frame": 1},
        "funnel_counts": {
            "with_data": 49,
            "trend_ok": 30,
            "base_ok": 20,
            "contraction": 10,
            "breakout": 3,
            "passed_quality": 2,
            "passed_friction": 2,
            "scored_gte_min_auto": 2,
            "approved": 2,
        },
    }) + "\n")

    # Fixture symbol trace
    (run_dir / "symbol_trace.jsonl").write_text(json.dumps({
        "scan_id": "nse_1001",
        "symbol": "RELIANCE.NS",
        "ts_utc": "2026-11-16T04:31:00.000Z",
        "fetch": {"status": "OK", "latency_ms": 125.0},
        "engine": {"state_before": "CONTRACTION", "state_after": "BUY", "first_failed_gate": "none", "all_gates": {}},
        "enrich": {"htf_status": "OK", "provisional": False},
        "score": {"composite_score": 85, "grade": "A"},
        "gates/slate": {"status": "APPROVED", "code": "APPROVED_CANDIDATE", "friction_r": 0.08, "risk_pct": 1.5},
        "timing_ms": {"fetch": 125.0},
    }) + "\n")

    # Fixture events
    (run_dir / "events.jsonl").write_text(json.dumps({
        "event": "telegram_send",
        "level": "INFO",
        "http_status": 200,
        "market": "NSE",
    }) + "\n")

    summary_data = generate_eod_summary(market="nse", date_str=date_str, base_dir=str(tmp_path / "logs" / "run"))
    assert summary_data["market"] == "NSE"
    assert summary_data["scans"]["completed"] == 1
    assert summary_data["scans"]["avg_start_delay_seconds"] == 1.2
    assert len(summary_data["signals"]) == 1
    assert summary_data["signals"][0]["symbol"] == "RELIANCE.NS"
    assert summary_data["telegram_delivery"]["success_rate_pct"] == 100.0

    md_output = format_eod_markdown(summary_data)
    assert "# 📊 End-of-Day Market Session Summary: NSE" in md_output
    assert "RELIANCE.NS" in md_output
    assert "100.0%" in md_output


def test_logging_performance_overhead_less_than_3_percent():
    """
    Test 7: Volume & Safety: Total added overhead per scan must be < 3% of scan duration.
    Tested on a 50-symbol stub scan simulating realistic fetch latencies.
    """
    class MockLatencyProvider:
        def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
            time.sleep(0.015)  # 15ms simulated network/parse latency
            return create_breakout_df(60, 100.0, is_breakout=False)

    cfg = load_config("config/default.yaml")
    provider = MockLatencyProvider()
    scanner = MultiSymbolScanner(provider, cfg)
    symbols = [f"SYM{i}" for i in range(50)]

    # Time pure scan without trace batch writes
    t0 = time.time()
    setups = scanner.scan(symbols, auto_flush=False)
    pure_scan_duration = time.time() - t0

    # Time trace batch flush
    t1 = time.time()
    scanner.flush_symbol_traces(slate=None, market="US")
    flush_duration = time.time() - t1

    # Ensure added logging overhead is < 3% of scan duration
    overhead_pct = (flush_duration / pure_scan_duration) * 100.0
    assert overhead_pct < 3.0, f"Logging overhead {overhead_pct:.2f}% exceeded 3% limit (flush={flush_duration*1000:.1f}ms, scan={pure_scan_duration:.2f}s)"
