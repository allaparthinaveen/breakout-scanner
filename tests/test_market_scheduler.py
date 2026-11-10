"""
Acceptance Test Suite for Phase 2: Multi-Market Session Orchestration Engine.

Validates:
1. Exchange Calendar & IANA Timezone Integration (XNYS, BSE/NSE, XTKS, XHKG, 24/7 Crypto).
2. DST Transition Verification (Spring forward EST -> EDT & Fall back EDT -> EST).
3. Exchange Holidays & Half-Days (Thanksgiving full close, Black Friday half-day).
4. Tokyo & Hong Kong Lunch Break Safety & Scan Suspension.
5. Candle Boundary Verification (Fresh bar, Stale Feed > 120s, Polling timeout fail-closed).
6. Deterministic Missed-Job Recovery Drills.
"""

import pytest
from datetime import datetime, timedelta
import pandas as pd

from breakout_scanner.market_session import (
    MarketSessionConfig,
    MarketSessionManager,
    CandleBoundaryVerifier,
    MultiMarketOrchestrator,
    STANDARD_MARKET_CONFIGS,
)


def test_market_session_initialization_all_targets():
    """Verifies that all 5 target markets initialize correctly with valid calendars."""
    for market_id in ["US", "NSE", "TSE", "HKEX", "CRYPTO"]:
        mgr = MarketSessionManager.from_market_id(market_id)
        assert mgr is not None
        assert mgr.calendar is not None
        assert mgr.tz is not None
        assert mgr.config.market_id == market_id


def test_dst_spring_forward_transition():
    """
    US clocks spring forward Sunday, March 8, 2026 (EST -> EDT).
    Verifies that local scan times remain invariant (08:30 pre-open, 10:31..16:01 candle closes)
    while UTC trigger times shift by exactly 1 hour with zero skipped or double scans.
    """
    mgr = MarketSessionManager.from_market_id("US")

    # Friday before DST (EST, UTC-5)
    friday = "2026-03-06"
    assert mgr.is_trading_day(friday)
    fri_open_local, fri_close_local = mgr.get_session_bounds(friday)
    assert fri_open_local.strftime("%H:%M") == "09:30"
    assert fri_close_local.strftime("%H:%M") == "16:00"
    assert fri_open_local.tz_convert("UTC").strftime("%H:%M") == "14:30"

    fri_events = mgr.get_scheduled_events(friday)
    fri_times = [ev[1].strftime("%H:%M") for ev in fri_events]
    assert fri_times == ["08:30", "10:31", "11:31", "12:31", "13:31", "14:31", "15:31", "16:01"]

    # Monday after DST (EDT, UTC-4)
    monday = "2026-03-09"
    assert mgr.is_trading_day(monday)
    mon_open_local, mon_close_local = mgr.get_session_bounds(monday)
    assert mon_open_local.strftime("%H:%M") == "09:30"
    assert mon_close_local.strftime("%H:%M") == "16:00"
    assert mon_open_local.tz_convert("UTC").strftime("%H:%M") == "13:30"

    mon_events = mgr.get_scheduled_events(monday)
    mon_times = [ev[1].strftime("%H:%M") for ev in mon_events]
    # Local scan times are identical; clock shift is transparent to strategy logic
    assert mon_times == ["08:30", "10:31", "11:31", "12:31", "13:31", "14:31", "15:31", "16:01"]
    assert len(mon_events) == len(fri_events)


def test_dst_fall_back_transition():
    """
    US clocks fall back Sunday, November 1, 2026 (EDT -> EST).
    Verifies UTC open shifts from 13:30 to 14:30 UTC without corrupting local schedules.
    """
    mgr = MarketSessionManager.from_market_id("US")

    # Friday before fall-back (EDT, UTC-4)
    fri_oct = "2026-10-30"
    assert mgr.is_trading_day(fri_oct)
    fri_open, _ = mgr.get_session_bounds(fri_oct)
    assert fri_open.tz_convert("UTC").strftime("%H:%M") == "13:30"

    # Monday after fall-back (EST, UTC-5)
    mon_nov = "2026-11-02"
    assert mgr.is_trading_day(mon_nov)
    mon_open, _ = mgr.get_session_bounds(mon_nov)
    assert mon_open.tz_convert("UTC").strftime("%H:%M") == "14:30"

    nov_events = mgr.get_scheduled_events(mon_nov)
    nov_times = [ev[1].strftime("%H:%M") for ev in nov_events]
    assert nov_times == ["08:30", "10:31", "11:31", "12:31", "13:31", "14:31", "15:31", "16:01"]


def test_holiday_full_day_closure():
    """
    Thanksgiving (2026-11-26) and Christmas (2026-12-25) must return zero scans
    and is_trading_day=False.
    """
    mgr = MarketSessionManager.from_market_id("US")

    thanksgiving = "2026-11-26"
    assert mgr.is_trading_day(thanksgiving) is False
    assert mgr.is_open(f"{thanksgiving} 10:30:00") is False
    assert mgr.get_scheduled_events(thanksgiving) == []

    christmas = "2026-12-25"
    assert mgr.is_trading_day(christmas) is False
    assert mgr.get_scheduled_events(christmas) == []


def test_half_day_early_close():
    """
    Black Friday (2026-11-27) is an early close session (13:00 ET).
    Scans must terminate at 13:01 ET without afternoon scans.
    """
    mgr = MarketSessionManager.from_market_id("US")
    black_friday = "2026-11-27"

    assert mgr.is_trading_day(black_friday) is True
    assert mgr.is_half_day(black_friday) is True

    open_dt, close_dt = mgr.get_session_bounds(black_friday)
    assert open_dt.strftime("%H:%M") == "09:30"
    assert close_dt.strftime("%H:%M") == "13:00"

    events = mgr.get_scheduled_events(black_friday)
    times = [ev[1].strftime("%H:%M") for ev in events]
    # Closes at 10:30, 11:30, 12:30, 13:00 (+1 min buffer: 10:31, 11:31, 12:31, 13:01)
    assert times == ["08:30", "10:31", "11:31", "12:31", "13:01"]
    # Verify no afternoon scans past 13:01
    assert "14:31" not in times
    assert "15:31" not in times
    assert "16:01" not in times


def test_tokyo_lunch_break_suspension():
    """
    Tokyo Stock Exchange (XTKS) halts trading 11:30 - 12:30 JST for lunch.
    Verifies that lunch time is detected as closed and no scans trigger between 11:31 and 13:31 JST.
    """
    mgr = MarketSessionManager.from_market_id("TSE")
    date_str = "2026-11-09"

    # Morning open: 10:00 JST is open
    assert mgr.is_open(f"{date_str} 10:00:00+09:00") is True
    assert mgr.is_lunch_break(f"{date_str} 10:00:00+09:00") is False

    # Lunch break: 12:00 JST is closed
    assert mgr.is_open(f"{date_str} 12:00:00+09:00") is False
    assert mgr.is_lunch_break(f"{date_str} 12:00:00+09:00") is True

    # Afternoon session: 14:00 JST is open
    assert mgr.is_open(f"{date_str} 14:00:00+09:00") is True
    assert mgr.is_lunch_break(f"{date_str} 14:00:00+09:00") is False

    events = mgr.get_scheduled_events(date_str)
    times = [ev[1].strftime("%H:%M") for ev in events]
    # Expected: Pre-open 08:00, morning 10:01, 11:01, 11:31 (break), afternoon 13:31, 14:31, 15:31
    assert times == ["08:00", "10:01", "11:01", "11:31", "13:31", "14:31", "15:31"]
    assert "12:01" not in times
    assert "12:31" not in times


def test_hongkong_lunch_break_suspension():
    """
    Hong Kong Stock Exchange (XHKG) halts trading 12:00 - 13:00 HKT for lunch.
    Verifies that lunch time is detected as closed and no scans trigger between 12:01 and 14:01 HKT.
    """
    mgr = MarketSessionManager.from_market_id("HKEX")
    date_str = "2026-11-09"

    assert mgr.is_open(f"{date_str} 11:00:00+08:00") is True
    assert mgr.is_lunch_break(f"{date_str} 11:00:00+08:00") is False

    assert mgr.is_open(f"{date_str} 12:30:00+08:00") is False
    assert mgr.is_lunch_break(f"{date_str} 12:30:00+08:00") is True

    assert mgr.is_open(f"{date_str} 14:00:00+08:00") is True
    assert mgr.is_lunch_break(f"{date_str} 14:00:00+08:00") is False

    events = mgr.get_scheduled_events(date_str)
    times = [ev[1].strftime("%H:%M") for ev in events]
    assert times == ["08:30", "10:31", "11:31", "12:01", "14:01", "15:01", "16:01"]
    assert "12:31" not in times
    assert "13:01" not in times


def test_candle_boundary_verification_fresh_stale_empty():
    """Tests CandleBoundaryVerifier for fresh bar, stale feed (>120s), and empty dataframe."""
    # 1. Fresh candle
    df_fresh = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-11-09 14:30:00+00:00", "2026-11-09 15:30:00+00:00"]),
    )
    res_fresh = CandleBoundaryVerifier.verify_dataframe(df_fresh, "2026-11-09 15:30:00+00:00")
    assert res_fresh["valid"] is True
    assert res_fresh["status"] == "FRESH"
    assert res_fresh["lag_seconds"] <= 120.0

    # 2. Stale candle (> 120s lag)
    df_stale = pd.DataFrame(
        {"close": [100.0]},
        index=pd.to_datetime(["2026-11-09 12:00:00+00:00"]),
    )
    res_stale = CandleBoundaryVerifier.verify_dataframe(df_stale, "2026-11-09 15:30:00+00:00")
    assert res_stale["valid"] is False
    assert res_stale["status"] == "STALE_FEED"
    assert res_stale["lag_seconds"] > 120.0

    # 3. Empty dataframe
    res_empty = CandleBoundaryVerifier.verify_dataframe(pd.DataFrame(), "2026-11-09 15:30:00+00:00")
    assert res_empty["valid"] is False
    assert res_empty["status"] == "EMPTY_DATA"


def test_candle_boundary_wait_and_verify():
    """Tests wait_and_verify polling with backoff and timeout fail-closed."""
    calls = 0

    def mock_fetch_success_on_second_call():
        nonlocal calls
        calls += 1
        if calls < 2:
            # First attempt: old bar (13:00 when waiting for 15:00 close)
            return pd.DataFrame({"c": [1.0]}, index=pd.to_datetime(["2026-11-09 13:00:00+00:00"]))
        # Second attempt: completed bar
        return pd.DataFrame({"c": [1.0]}, index=pd.to_datetime(["2026-11-09 15:00:00+00:00"]))

    res = CandleBoundaryVerifier.wait_and_verify(
        mock_fetch_success_on_second_call,
        expected_close_time="2026-11-09 15:00:00+00:00",
        max_wait_seconds=2.0,
        poll_interval=0.1,
    )
    assert res["valid"] is True
    assert res["status"] == "FRESH"
    assert res["attempts"] == 2

    # Timeout fail-closed
    def mock_fetch_always_stale():
        return pd.DataFrame({"c": [1.0]}, index=pd.to_datetime(["2026-11-09 10:00:00+00:00"]))

    res_timeout = CandleBoundaryVerifier.wait_and_verify(
        mock_fetch_always_stale,
        expected_close_time="2026-11-09 15:00:00+00:00",
        max_wait_seconds=0.3,
        poll_interval=0.1,
    )
    assert res_timeout["valid"] is False
    assert res_timeout["status"] == "STALE_FEED"


def test_missed_job_recovery_deterministic():
    """
    Simulates host reboot recovery:
    Daemon offline between 11:00 EST and 14:00 EST on 2026-11-09.
    calculate_missed_scans must cleanly identify the 3 missed hourly scans.
    """
    orch = MultiMarketOrchestrator()
    last_scan = "2026-11-09 16:00:00+00:00"  # 11:00 EST
    current_time = "2026-11-09 19:00:00+00:00"  # 14:00 EST

    missed = orch.calculate_missed_scans("US", last_scan, current_time, max_age_hours=4.0)
    assert len(missed) == 3
    times_local = [m.strftime("%H:%M") for m in missed]
    assert times_local == ["11:31", "12:31", "13:31"]


def test_multi_market_orchestrator_global_flow():
    """Validates global active market filtering and next event determination."""
    orch = MultiMarketOrchestrator()

    # When UTC is Sunday 2026-11-08 15:00, US/NSE/TSE/HKEX are all closed, only CRYPTO is open
    active_sunday = orch.get_active_markets("2026-11-08 15:00:00+00:00")
    assert "CRYPTO" in active_sunday
    assert "US" not in active_sunday
    assert "NSE" not in active_sunday
    assert "TSE" not in active_sunday
    assert "HKEX" not in active_sunday

    # Next event calculation
    next_ev = orch.get_next_global_event("2026-11-08 23:00:00+00:00")
    assert next_ev is not None
    market_id, event_type, event_time_utc = next_ev
    assert market_id in ["TSE", "HKEX", "CRYPTO", "NSE", "US"]
    assert event_time_utc > pd.Timestamp("2026-11-08 23:00:00+00:00")
