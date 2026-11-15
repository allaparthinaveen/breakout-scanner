import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch

from breakout_scanner.models import Setup, State
from breakout_scanner.confluence import check_earnings_proximity, enrich_confluence_and_context
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df
from breakout_scanner.audit_ledger import record_forward_test_ledger
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.telegram_formatter import format_setup_telegram_line


# =========================================================================
# FIX 2: Stop must win inside a bar
# =========================================================================

def test_tracker_stop_wins_inside_bar_when_wicking_below_stop():
    """
    FIX 2: A bar that wicks through the stop but closes above it (and below resistance)
    must be booked as STOP_LOSS at -1.0R, NOT as FAILED_BREAKOUT at close.
    """
    record = {
        "signal_id": "TEST_STOP_WINS",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-16 09:30:00",
        "breakout_time": "2026-11-16 09:30:00",
        "price": {
            "entry": 100.0,
            "stop": 98.0,      # Initial risk = 2.0 (entry ~ 100.05)
            "tp1": 103.0,
            "tp2": 106.0,
            "resistance": 100.0,
        },
    }

    # Bar 0: breakout bar (9:30)
    # Bar 1: fill bar (10:30) fills at Open=100.0 -> entry=100.05. Low drops to 97.5 (< stop 98.0),
    # but close recovers to 99.0 (< resistance 100.0).
    dates = pd.date_range("2026-11-16 09:30:00", periods=2, freq="1h")
    df = pd.DataFrame({
        "Open": [99.0, 100.0],
        "High": [100.0, 100.5],
        "Low": [98.5, 97.5],    # Wicks through stop (98.0)!
        "Close": [100.0, 99.0],  # Closes between stop (98.0) and resistance (100.0)
        "Volume": [1000, 1000],
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(record, df)
    # Stop loss must WIN!
    assert outcome["status"] == "EVALUATED"
    assert outcome["evaluated"] is True
    assert outcome["exit_reason"] == "STOP_LOSS"
    assert outcome["hypothetical_r"] == -1.00


def test_tracker_gap_down_open_below_stop_exits_at_open():
    """
    FIX 2: If the bar Open is already below the active stop, exit at the Open
    with R worse than -1.0R (consistent with backtester GAP_STOP).
    """
    record = {
        "signal_id": "TEST_GAP_STOP",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-16 09:30:00",
        "breakout_time": "2026-11-16 09:30:00",
        "price": {
            "entry": 100.0,
            "stop": 98.0,      # Initial risk ~ 2.05 (entry ~ 100.05)
            "tp1": 103.0,
            "tp2": 106.0,
            "resistance": 100.0,
        },
    }

    # Bar 1 fills normally. Bar 2 opens with a huge gap-down at 95.0 (< stop 98.0)
    dates = pd.date_range("2026-11-16 09:30:00", periods=3, freq="1h")
    df = pd.DataFrame({
        "Open": [99.0, 100.0, 95.0],  # Bar 2 opens at 95.0
        "High": [100.0, 100.5, 96.0],
        "Low": [98.5, 99.5, 94.5],
        "Close": [100.0, 100.0, 95.5],
        "Volume": [1000] * 3,
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(record, df)
    assert outcome["status"] == "EVALUATED"
    assert outcome["exit_reason"] == "STOP_LOSS"
    # Exit at Open (95.0): (95.0 - 100.05) / 2.05 = -2.46R (worse than -1.0R)
    assert outcome["hypothetical_r"] < -1.00
    assert outcome["hypothetical_r"] == pytest.approx(-2.46, abs=0.05)


# =========================================================================
# FIX 3: Realistic fill for non-fresh / late-scanned signals
# =========================================================================

def test_tracker_fresh_signal_fill_basis_breakout_next_open():
    """
    FIX 3: Breakout at 09:30, scan at 10:31 (within 10m tolerance of breakout_bar_end 10:30)
    -> fresh: fill = 10:30 bar Open, fill_basis = 'BREAKOUT_NEXT_OPEN'.
    """
    record = {
        "signal_id": "TEST_FRESH",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-16 10:31:00",
        "breakout_time": "2026-11-16 09:30:00",
        "price": {"entry": 100.5, "stop": 98.0, "tp1": 103.0, "tp2": 105.0},
    }

    dates = pd.date_range("2026-11-16 09:30:00", periods=4, freq="1h")
    df = pd.DataFrame({
        "Open": [99.0, 101.0, 102.0, 103.0],
        "High": [100.0, 101.5, 102.5, 103.5],
        "Low": [98.5, 100.8, 101.8, 102.8],
        "Close": [100.0, 101.2, 102.2, 103.2],
        "Volume": [1000] * 4,
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(record, df)
    assert outcome["fill_basis"] == "BREAKOUT_NEXT_OPEN"
    # Fill bar is 10:30 bar (Open=101.0 -> 101.0505)
    assert outcome["actual_fill"] == pytest.approx(101.0505, abs=0.001)


def test_tracker_late_signal_fill_basis_scan_next_open():
    """
    FIX 3: Breakout at 09:30, scan at 14:05 (late signal, hours after breakout bar ended)
    -> late: fill = first bar whose start is strictly > 14:05 (e.g. 14:30 bar), fill_basis = 'SCAN_NEXT_OPEN'.
    """
    record = {
        "signal_id": "TEST_LATE",
        "symbol": "TEST",
        "direction": "BUY",
        "timestamp": "2026-11-16 14:05:00",
        "breakout_time": "2026-11-16 09:30:00",
        "price": {"entry": 100.5, "stop": 98.0, "tp1": 108.0, "tp2": 110.0},
    }

    # 6 bars: 09:30, 10:30, 11:30, 12:30, 13:30, 14:30
    dates = pd.date_range("2026-11-16 09:30:00", periods=6, freq="1h")
    opens = [99.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    df = pd.DataFrame({
        "Open": opens,
        "High": [x + 0.5 for x in opens],
        "Low": [x - 0.5 for x in opens],
        "Close": [x + 0.2 for x in opens],
        "Volume": [1000] * 6,
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(record, df)
    assert outcome["fill_basis"] == "SCAN_NEXT_OPEN"
    # First bar starting > 14:05 is index 5 (14:30), Open=105.0 -> actual_fill=105.0525
    assert outcome["actual_fill"] == pytest.approx(105.0525, abs=0.001)

    # If the bar starting after 14:05 is NOT in df yet (only bars up to 13:30 exist)
    df_early = df.iloc[:5]
    outcome_pending = evaluate_setup_outcome_from_df(record, df_early)
    assert outcome_pending["status"] == "IN_PROGRESS"
    assert outcome_pending["reason"] == "AWAITING_NEXT_OPEN_FILL"
    assert outcome_pending["fill_basis"] == "SCAN_NEXT_OPEN"


# =========================================================================
# FIX 4: Earnings unverified must be visible, not silent
# =========================================================================

def test_earnings_unverified_flag_and_tags_warn_policy(tmp_path):
    """
    FIX 4: When earnings lookup fails/is unknown, Setup.earnings_unverified is True.
    With default 'warn' policy:
    1. Setup stays tradeable (earnings_warning is False).
    2. '⚠️ EARNINGS UNVERIFIED — check broker calendar' tag appears in CLI output.
    3. Tag appears in Telegram card.
    4. Ledger row records data_health.earnings_unverified = True.
    """
    setup = Setup(
        symbol="UNVERIF",
        state=State.BUY,
        direction=1,
        entry=100.0,
        stop=98.0,
        composite_score=80,
        composite_grade="A",
        breakout_time="2026-11-16 09:30:00",
    )
    df = pd.DataFrame({
        "Open": [99.0, 100.0],
        "High": [100.0, 101.0],
        "Low": [98.5, 99.5],
        "Close": [100.0, 100.5],
        "Volume": [1000, 1000],
    }, index=pd.date_range("2026-11-16 09:30:00", periods=2, freq="1h"))

    # Mock yfinance calendar raising exception (throttled/error)
    with patch("yfinance.Ticker") as mock_ticker:
        mock_instance = MagicMock()
        mock_instance.calendar = None  # Returns UNKNOWN
        mock_ticker.return_value = mock_instance

        enrich_confluence_and_context(setup, df, cfg={"earnings": {"unverified_policy": "warn"}})

    assert setup.earnings_unverified is True
    assert setup.earnings_warning is False  # 'warn' policy does not block

    # Verify Telegram card formatting contains the tag
    tg_line = format_setup_telegram_line(setup, is_fresh=True)
    assert "⚠️ EARNINGS UNVERIFIED — check broker calendar" in tg_line

    # Verify CLI formatting contains the tag
    scanner = MultiSymbolScanner(MagicMock(), cfg={})
    cli_line = scanner.format_active_setup(setup, is_fresh=True)
    assert "⚠️ EARNINGS UNVERIFIED — check broker calendar" in cli_line

    # Verify ledger row records earnings_unverified in data_health
    ledger_file = str(tmp_path / "test_unverif_ledger.jsonl")
    count = record_forward_test_ledger([setup], cfg={"profile_name": "Standard"}, ledger_path=ledger_file)
    assert count == 1
    with open(ledger_file, "r") as f:
        rec = json.loads(f.readline())
    assert rec["data_health"]["earnings_unverified"] is True


def test_earnings_unverified_block_policy_suppresses():
    """
    FIX 4: When unverified_policy is 'block', earnings_unverified sets earnings_warning=True,
    triggering risk suppression path.
    """
    setup = Setup(
        symbol="BLOCKME",
        state=State.BUY,
        direction=1,
        entry=100.0,
        stop=98.0,
        composite_score=80,
    )
    df = pd.DataFrame({
        "Open": [99.0, 100.0],
        "High": [100.0, 101.0],
        "Low": [98.5, 99.5],
        "Close": [100.0, 100.5],
        "Volume": [1000, 1000],
    }, index=pd.date_range("2026-11-16 09:30:00", periods=2, freq="1h"))

    with patch("yfinance.Ticker") as mock_ticker:
        mock_instance = MagicMock()
        mock_instance.calendar = None
        mock_ticker.return_value = mock_instance

        enrich_confluence_and_context(setup, df, cfg={"earnings": {"unverified_policy": "block"}})

    assert setup.earnings_unverified is True
    assert setup.earnings_warning is True  # 'block' policy sets earnings_warning!


def test_earnings_healthy_lookup_flag_false():
    """
    FIX 4: Healthy earnings lookup has earnings_unverified = False.
    """
    setup = Setup(symbol="HEALTHY", state=State.BUY, direction=1, entry=100.0, stop=98.0)
    df = pd.DataFrame({
        "Open": [99.0, 100.0], "High": [100.0, 101.0], "Low": [98.5, 99.5], "Close": [100.0, 100.5], "Volume": [1000, 1000]
    }, index=pd.date_range("2026-11-16 09:30:00", periods=2, freq="1h"))

    with patch("yfinance.Ticker") as mock_ticker:
        mock_instance = MagicMock()
        mock_instance.calendar = {"Earnings Date": [date(2026, 12, 1)]}
        mock_ticker.return_value = mock_instance

        enrich_confluence_and_context(setup, df, cfg={})

    assert setup.earnings_unverified is False


def test_earnings_crypto_flag_false():
    """
    FIX 4: Crypto/non-equity symbols must NEVER be marked earnings_unverified.
    """
    setup = Setup(symbol="BTC-USD", state=State.BUY, direction=1, entry=50000.0, stop=48000.0)
    df = pd.DataFrame({
        "Open": [49000, 50000], "High": [50500, 51000], "Low": [48500, 49500], "Close": [50000, 50500], "Volume": [1000, 1000]
    }, index=pd.date_range("2026-11-16 09:30:00", periods=2, freq="1h"))

    enrich_confluence_and_context(setup, df, cfg={})
    assert setup.earnings_unverified is False
