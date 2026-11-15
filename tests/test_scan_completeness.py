import pytest
import pandas as pd
import numpy as np
import io
import sys
from unittest.mock import MagicMock

from breakout_scanner.models import Setup, State
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.telegram_formatter import format_scan_report_html
from breakout_scanner.config import load_config


class MockEmptyProvider:
    def __init__(self):
        self.calls = []

    def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
        self.calls.append(symbol)
        return pd.DataFrame()


class MockPatternProvider:
    def __init__(self, failure_symbols=None):
        self.failure_symbols = set(failure_symbols or [])
        self.calls = []

    def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
        self.calls.append(symbol)
        if symbol in self.failure_symbols:
            return pd.DataFrame()
        dates = pd.date_range("2026-11-01", periods=60, freq="1h")
        return pd.DataFrame({
            "Open": np.linspace(100, 110, 60),
            "High": np.linspace(101, 111, 60),
            "Low": np.linspace(99, 109, 60),
            "Close": np.linspace(100.5, 110.5, 60),
            "Volume": [1000] * 60,
        }, index=dates)


def test_all_symbols_fail_aborts_and_produces_banner():
    """
    FIX 1: All symbols fail -> setups == [], stats.aborted True,
    CLI output and Telegram HTML both contain 'SCAN INCOMPLETE'.
    """
    provider = MockEmptyProvider()
    cfg = load_config("config/default.yaml")
    scanner = MultiSymbolScanner(provider, cfg=cfg)

    symbols = [f"SYM{i}" for i in range(12)]
    setups = scanner.scan(symbols)

    assert setups == []
    stats = getattr(scanner, "last_scan_stats", None)
    assert stats is not None
    assert stats["aborted"] is True
    assert stats["with_data"] == 0
    assert stats["total"] == 12
    # All 12 symbols must be recorded as failed (not silently skipped)
    assert len(stats["failed_symbols"]) == 12

    # CLI report output must contain SCAN INCOMPLETE banner
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    try:
        scanner.report(setups)
    finally:
        sys.stdout = old_stdout

    cli_output = buffer.getvalue()
    assert "SCAN INCOMPLETE" in cli_output
    assert "Do NOT read 'no signals' as a quiet market" in cli_output

    # Telegram HTML output must contain SCAN INCOMPLETE banner
    tg_html = format_scan_report_html(setups, scan_stats=stats)
    assert "SCAN INCOMPLETE" in tg_html
    assert "Do NOT read 'no signals' as a quiet market" in tg_html


def test_streak_after_success_does_not_abort_and_respects_k8():
    """
    FIX 1: 3 bad tickers first then good ones -> NOT aborted (K=8).
    Streak of bad tickers after earlier success -> NOT aborted.
    """
    cfg = load_config("config/default.yaml")

    # Scenario A: 3 bad tickers at the start of a 20-symbol list with K=8
    bad_start = [f"BAD{i}" for i in range(3)]
    good_rest = [f"GOOD{i}" for i in range(17)]
    all_syms = bad_start + good_rest

    provider_a = MockPatternProvider(failure_symbols=bad_start)
    scanner_a = MultiSymbolScanner(provider_a, cfg=cfg)
    setups_a = scanner_a.scan(all_syms)

    stats_a = scanner_a.last_scan_stats
    assert stats_a["aborted"] is False
    assert stats_a["with_data"] == 17
    assert len(setups_a) == 17

    # Scenario B: 1 good symbol first, followed by 10 bad symbols -> must NOT abort mid-scan
    syms_b = ["GOOD0"] + [f"BAD{i}" for i in range(10)]
    provider_b = MockPatternProvider(failure_symbols=[f"BAD{i}" for i in range(10)])
    scanner_b = MultiSymbolScanner(provider_b, cfg=cfg)
    setups_b = scanner_b.scan(syms_b)

    stats_b = scanner_b.last_scan_stats
    assert stats_b["aborted"] is False
    assert stats_b["with_data"] == 1
    assert stats_b["total"] == 11
    # All symbols were attempted
    assert len(provider_b.calls) >= 11


def test_95_percent_coverage_no_banner():
    """
    FIX 1: When coverage is >= 90% (e.g. 19/20 = 95%), no incomplete banner is shown.
    """
    cfg = load_config("config/default.yaml")
    syms = [f"SYM{i}" for i in range(20)]
    # Only 1 fails -> 19/20 = 95% with data
    provider = MockPatternProvider(failure_symbols=["SYM0"])
    scanner = MultiSymbolScanner(provider, cfg=cfg)
    setups = scanner.scan(syms)

    stats = scanner.last_scan_stats
    assert stats["with_data"] == 19
    assert stats["total"] == 20
    assert stats["aborted"] is False

    # CLI report
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    try:
        scanner.report(setups)
    finally:
        sys.stdout = old_stdout

    cli_output = buffer.getvalue()
    assert "SCAN INCOMPLETE" not in cli_output

    # Telegram HTML
    tg_html = format_scan_report_html(setups, scan_stats=stats)
    assert "SCAN INCOMPLETE" not in tg_html


def test_formatter_works_unchanged_when_scan_stats_none():
    """
    FIX 1: Backward compatibility: format_scan_report_html works unchanged when scan_stats is None.
    """
    tg_html = format_scan_report_html([], scan_stats=None)
    assert "SCAN INCOMPLETE" not in tg_html
    assert "(None)" in tg_html
