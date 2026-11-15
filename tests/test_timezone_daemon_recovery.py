import pytest
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from breakout_scanner.market_session import (
    MarketSessionManager,
    MultiMarketOrchestrator,
    STANDARD_MARKET_CONFIGS,
)
from breakout_scanner.scheduler import MultiMarketScheduler
from production_daemon import CloudDaemonRunner


def test_is_open_timezone_resilience():
    us_mgr = MarketSessionManager.from_market_id("US")
    nse_mgr = MarketSessionManager.from_market_id("NSE")
    crypto_mgr = MarketSessionManager.from_market_id("CRYPTO")

    # 1. 2026-11-11 16:00:00 UTC corresponds to:
    # 11:00 AM EST (US is OPEN)
    # 21:30 IST (NSE is CLOSED)
    # Crypto (always OPEN)
    utc_dt = pd.Timestamp("2026-11-11 16:00:00", tz="UTC")
    assert us_mgr.is_open(utc_dt) is True
    assert nse_mgr.is_open(utc_dt) is False
    assert crypto_mgr.is_open(utc_dt) is True

    # 2. Test naive datetime handling
    # If caller passes naive datetime without timezone, it should not crash
    naive_dt = datetime(2026, 11, 11, 16, 0, 0)
    # Regardless of host machine timezone, is_open accepts it safely
    res_us = us_mgr.is_open(naive_dt)
    assert isinstance(res_us, bool)

    # 3. 2026-11-11 05:00:00 UTC corresponds to:
    # 10:30 AM IST (NSE is OPEN)
    # 00:00 EST midnight (US is CLOSED)
    utc_nse_peak = pd.Timestamp("2026-11-11 05:00:00", tz="UTC")
    assert nse_mgr.is_open(utc_nse_peak) is True
    assert us_mgr.is_open(utc_nse_peak) is False


def test_is_session_active_or_recent_close():
    us_mgr = MarketSessionManager.from_market_id("US")
    # NYSE closes at 16:00 EST (21:00 UTC)
    # At 21:01 UTC, is_open is False, but is_session_active_or_recent_close should be True!
    just_after_close = pd.Timestamp("2026-11-11 21:01:00", tz="UTC")
    assert us_mgr.is_open(just_after_close) is False
    assert us_mgr.is_session_active_or_recent_close(just_after_close, grace_minutes=15) is True

    # 30 minutes after close should be False
    well_after_close = pd.Timestamp("2026-11-11 21:35:00", tz="UTC")
    assert us_mgr.is_session_active_or_recent_close(well_after_close, grace_minutes=15) is False


def test_orchestrator_get_active_markets_includes_recent_close():
    orch = MultiMarketOrchestrator(["US", "NSE", "CRYPTO"])
    
    # Peak US hours (16:00 UTC)
    utc_midday = pd.Timestamp("2026-11-11 16:00:00", tz="UTC")
    active = orch.get_active_markets(dt=utc_midday, include_recent_close=False)
    assert "US" in active
    assert "CRYPTO" in active
    assert "NSE" not in active

    # Right after US close (21:01 UTC)
    utc_after_us = pd.Timestamp("2026-11-11 21:01:00", tz="UTC")
    active_recent = orch.get_active_markets(dt=utc_after_us, include_recent_close=True)
    assert "US" in active_recent
    assert "CRYPTO" in active_recent


def test_cloud_daemon_heartbeat_dispatch():
    runner = CloudDaemonRunner(markets=["US", "NSE", "CRYPTO"])
    # Verify method exists and runs without throwing syntax/runtime error
    # It will either return True (if valid credentials) or False (if invalid), but not raise
    result = runner.dispatch_telegram_heartbeat()
    assert isinstance(result, bool)


def test_hourly_scan_signature_changes_per_hour():
    from breakout_scanner.scheduler import SessionScheduler
    s = SessionScheduler(market_id="US")
    
    # Hour 1 (10:00 EST)
    t1 = pd.Timestamp("2026-11-12 15:00:00", tz="UTC")
    mkt_t1 = s.market_session.to_market_tz(t1)
    sig1 = f"SCAN_REPORT:US:{mkt_t1.strftime('%Y-%m-%d_%H:00')}"

    # Hour 2 (11:00 EST)
    t2 = pd.Timestamp("2026-11-12 16:00:00", tz="UTC")
    mkt_t2 = s.market_session.to_market_tz(t2)
    sig2 = f"SCAN_REPORT:US:{mkt_t2.strftime('%Y-%m-%d_%H:00')}"

    # Signatures must be distinct so Hour 2 is NEVER dropped as a duplicate!
    assert sig1 == "SCAN_REPORT:US:2026-11-12_10:00"
    assert sig2 == "SCAN_REPORT:US:2026-11-12_11:00"
    assert sig1 != sig2

