import pytest
from unittest.mock import MagicMock
import pandas as pd
from breakout_scanner.models import Setup, State
from breakout_scanner.scheduler import SessionScheduler


def test_empty_slate_suppressed_mid_session(tmp_path):
    """
    Empty slate mid-session with no actionable trades, no in-progress trades,
    and no fresh rejections must NOT send a Telegram report.
    """
    cache_file = str(tmp_path / "test_dedup.json")
    scheduler = SessionScheduler(
        cfg_path="config/profiles/us_production.yaml",
        dedup_file=cache_file,
        market_id="US",
        enforce_market_hours=False,
    )
    scheduler.send_telegram_alert = MagicMock(return_value=True)

    # Empty setups (or only IDLE setups)
    scheduler.scanner.scan = MagicMock(return_value=[])
    scheduler.get_watchlist = MagicMock(return_value=["AAPL"])

    # Mock market_session so it is NOT a session boundary
    mock_session = MagicMock()
    mock_session.get_session_bounds.return_value = (
        pd.Timestamp("2026-11-17 09:30:00", tz="UTC"),
        pd.Timestamp("2026-11-17 16:00:00", tz="UTC"),
    )
    mock_session.get_candle_close_schedule.return_value = [
        pd.Timestamp("2026-11-17 10:30:00", tz="UTC"),
        pd.Timestamp("2026-11-17 11:30:00", tz="UTC"),
        pd.Timestamp("2026-11-17 16:00:00", tz="UTC"),
    ]
    # Mid-session candle: 11:30 (not 10:30 and not 16:00)
    mock_session.get_last_closed_candle.return_value = pd.Timestamp("2026-11-17 11:30:00", tz="UTC")
    mock_session.is_final_bar_of_session.return_value = False
    mock_session.to_market_tz.side_effect = lambda ts: ts
    scheduler.market_session = mock_session

    res = scheduler.scan_and_alert(force=False)

    # Must NOT dispatch Telegram alert!
    assert scheduler.send_telegram_alert.call_count == 0


def test_empty_slate_sent_with_actionable_trades(tmp_path):
    """
    If slate has an actionable trade, it MUST send Telegram alert.
    """
    cache_file = str(tmp_path / "test_dedup.json")
    scheduler = SessionScheduler(
        cfg_path="config/profiles/us_production.yaml",
        dedup_file=cache_file,
        market_id="US",
        enforce_market_hours=False,
    )
    scheduler.send_telegram_alert = MagicMock(return_value=True)

    s1 = Setup("AAPL", state=State.BUY, direction=1, entry=220.0, stop=215.0, tp2=230.0, risk_pct=2.0)
    scheduler.scanner.scan = MagicMock(return_value=[s1])
    scheduler.get_watchlist = MagicMock(return_value=["AAPL"])

    res = scheduler.scan_and_alert(force=False)
    assert scheduler.send_telegram_alert.call_count == 1


def test_empty_slate_sent_when_force_is_true(tmp_path):
    """
    If force=True, report must be sent even if empty.
    """
    cache_file = str(tmp_path / "test_dedup.json")
    scheduler = SessionScheduler(
        cfg_path="config/profiles/us_production.yaml",
        dedup_file=cache_file,
        market_id="US",
        enforce_market_hours=False,
    )
    scheduler.send_telegram_alert = MagicMock(return_value=True)

    scheduler.scanner.scan = MagicMock(return_value=[])
    scheduler.get_watchlist = MagicMock(return_value=["AAPL"])

    res = scheduler.scan_and_alert(force=True)
    assert scheduler.send_telegram_alert.call_count == 1
