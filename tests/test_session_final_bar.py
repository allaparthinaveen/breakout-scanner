import pytest
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from breakout_scanner.market_session import MarketSessionManager, is_candle_closed


def test_us_final_bar_closed_at_1601_open_at_1545():
    """
    ITEM 4 Regression Test 1:
    US 15:30 ET bar (closing at 16:00 ET) is:
    - Open (not closed) at 15:45 ET.
    - Closed at 16:01 ET.
    """
    session = MarketSessionManager.from_market_id("US")
    ny_tz = ZoneInfo("America/New_York")

    # Regular trading day (Monday 2026-11-16)
    bar_start_1530 = pd.Timestamp("2026-11-16 15:30:00", tz=ny_tz)

    now_1545 = pd.Timestamp("2026-11-16 15:45:00", tz=ny_tz)
    now_1601 = pd.Timestamp("2026-11-16 16:01:00", tz=ny_tz)

    # At 15:45 ET, the 15:30 bar is still active/forming -> False
    assert is_candle_closed(bar_start_1530, now=now_1545, timeframe="1h", market_session=session) is False

    # At 16:01 ET, session has closed (16:00 ET) and 15:30 bar is fully closed -> True
    assert is_candle_closed(bar_start_1530, now=now_1601, timeframe="1h", market_session=session) is True


def test_nse_final_bar_closed_at_1531_open_at_1525():
    """
    ITEM 4 Regression Test 2:
    NSE 15:15 IST bar (closing at 15:30 IST) is:
    - Open (not closed) at 15:25 IST.
    - Closed at 15:31 IST.
    """
    session = MarketSessionManager.from_market_id("NSE")
    kol_tz = ZoneInfo("Asia/Kolkata")

    # Regular trading day (Monday 2026-11-16)
    bar_start_1515 = pd.Timestamp("2026-11-16 15:15:00", tz=kol_tz)

    now_1525 = pd.Timestamp("2026-11-16 15:25:00", tz=kol_tz)
    now_1531 = pd.Timestamp("2026-11-16 15:31:00", tz=kol_tz)

    # At 15:25 IST, 15:15 bar is still active/forming -> False
    assert is_candle_closed(bar_start_1515, now=now_1525, timeframe="1h", market_session=session) is False

    # At 15:31 IST, session closed at 15:30 IST -> True
    assert is_candle_closed(bar_start_1515, now=now_1531, timeframe="1h", market_session=session) is True


def test_us_early_close_day():
    """
    ITEM 4 Regression Test 3:
    On a US early close day (e.g. 13:00 ET close on 2026-11-27 Day after Thanksgiving),
    the final 12:30 ET bar closes at 13:00 ET:
    - Open at 12:45 ET.
    - Closed at 13:01 ET.
    """
    session = MarketSessionManager.from_market_id("US")
    ny_tz = ZoneInfo("America/New_York")

    # Black Friday 2026-11-27 is a known scheduled early close (13:00 ET) in XNYS calendar
    bar_start_1230 = pd.Timestamp("2026-11-27 12:30:00", tz=ny_tz)

    now_1245 = pd.Timestamp("2026-11-27 12:45:00", tz=ny_tz)
    now_1301 = pd.Timestamp("2026-11-27 13:01:00", tz=ny_tz)

    assert is_candle_closed(bar_start_1230, now=now_1245, timeframe="1h", market_session=session) is False
    assert is_candle_closed(bar_start_1230, now=now_1301, timeframe="1h", market_session=session) is True


def test_mid_session_bars_unchanged():
    """
    ITEM 4 Regression Test 4:
    Mid-session bars are byte-for-byte unchanged:
    - US 10:30 ET bar closes at 11:30 ET.
      Open at 11:15 ET.
      Closed at 11:31 ET.
    - NSE 11:15 IST bar closes at 12:15 IST.
      Open at 12:00 IST.
      Closed at 12:16 IST.
    """
    us_session = MarketSessionManager.from_market_id("US")
    ny_tz = ZoneInfo("America/New_York")
    bar_start_us_mid = pd.Timestamp("2026-11-16 10:30:00", tz=ny_tz)

    assert is_candle_closed(bar_start_us_mid, now=pd.Timestamp("2026-11-16 11:15:00", tz=ny_tz), timeframe="1h", market_session=us_session) is False
    assert is_candle_closed(bar_start_us_mid, now=pd.Timestamp("2026-11-16 11:31:00", tz=ny_tz), timeframe="1h", market_session=us_session) is True

    nse_session = MarketSessionManager.from_market_id("NSE")
    kol_tz = ZoneInfo("Asia/Kolkata")
    bar_start_nse_mid = pd.Timestamp("2026-11-16 11:15:00", tz=kol_tz)

    assert is_candle_closed(bar_start_nse_mid, now=pd.Timestamp("2026-11-16 12:00:00", tz=kol_tz), timeframe="1h", market_session=nse_session) is False
    assert is_candle_closed(bar_start_nse_mid, now=pd.Timestamp("2026-11-16 12:16:00", tz=kol_tz), timeframe="1h", market_session=nse_session) is True


def test_final_bar_marked_as_next_session_candidate():
    """
    ITEM 4 Regression Test 5:
    Setups on the final bar of the session are marked as next-session candidates,
    and Telegram formatter reflects [NEXT-SESSION CANDIDATE] instead of [ENTER NOW].
    """
    from breakout_scanner.models import Setup, State
    from breakout_scanner.slate import ActionableSlate, PositionAllocation
    from breakout_scanner.telegram_formatter import format_scan_report_html, format_instant_alert_html

    setup = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=150.0,
        current_price=150.5,
        stop=147.0,
        risk_pct=2.0,
        tp1=153.0,
        tp2=156.0,
        composite_score=85,
        composite_grade="A",
        breakout_time="2026-11-16T15:30:00Z",
    )
    setup.is_next_session_candidate = True

    slate = ActionableSlate(
        slate_id="slate_final_bar",
        timestamp="2026-11-16T16:01:00Z",
        profile_name="US_Production",
        total_scanned=1,
        portfolio_equity=100000.0,
        max_daily_risk_pct=2.0,
        max_daily_dollar_risk=2000.0,
        selected_trades=[
            PositionAllocation(
                symbol="AAPL",
                shares=100,
                dollar_risk=300.0,
                entry_price=150.0,
                stop_loss=147.0,
                take_profit_1=153.0,
                take_profit_2=156.0,
                setup=setup,
            )
        ],
    )

    report_html = format_scan_report_html([setup], market="US", slate=slate)
    assert "NEXT-SESSION CANDIDATE" in report_html
    assert "ENTER NOW" not in report_html

    instant_alert_html = format_instant_alert_html(setup, timeframe="1h")
    assert "NEXT-SESSION CANDIDATE" in instant_alert_html
    assert "ENTER NOW — FRESH" not in instant_alert_html

