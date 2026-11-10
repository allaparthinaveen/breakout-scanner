"""
Multi-Market Session Orchestration Engine.

Provides timezone-aware exchange scheduling, holiday/half-day handling,
lunch-break suspension, candle close boundary verification, and multi-market
orchestration across US, India (NSE/BSE), Japan (TSE), Hong Kong (HKEX), and Crypto (24/7).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, time as dtime
from typing import Dict, List, Optional, Tuple, Union, Any, Callable
from zoneinfo import ZoneInfo
import time

import pandas as pd
import exchange_calendars as xcals

logger = logging.getLogger("market_session")


# Calendar alias mapping
# BSE calendar has identical trading hours (09:15 - 15:30 IST) and identical trading holidays to NSE,
# and safely avoids a pandas 3.1.x OrthodoxEaster NotImplementedError present in the XBSE calendar.
CALENDAR_ALIASES = {
    "XNSE": "BSE",
    "NSE": "BSE",
    "XBSE": "BSE",
    "INDIA": "BSE",
    "NYSE": "XNYS",
    "NASDAQ": "XNYS",
    "US": "XNYS",
    "TSE": "XTKS",
    "TOKYO": "XTKS",
    "JAPAN": "XTKS",
    "HKEX": "XHKG",
    "HK": "XHKG",
    "HONGKONG": "XHKG",
    "CRYPTO": "24/7",
}


@dataclass
class MarketSessionConfig:
    market_id: str
    calendar_code: str
    timezone: str
    pre_open_offset_minutes: int = 60
    minute_offset: int = 1  # Grace period buffer past candle close (e.g. :01 past the hour)
    watchlist_file: Optional[str] = None
    watchlist_files: Optional[List[str]] = None
    profile_path: Optional[str] = None
    allow_fractional: bool = False


STANDARD_MARKET_CONFIGS: Dict[str, MarketSessionConfig] = {
    "US": MarketSessionConfig(
        market_id="US",
        calendar_code="XNYS",
        timezone="America/New_York",
        pre_open_offset_minutes=60,
        minute_offset=1,
        watchlist_file="config/watchlist.txt",
        watchlist_files=[
            "config/watchlist.txt",
            "config/us_today_watchlist.txt",
            "config/nasdaq_50_watchlist.txt",
            "config/sp_50_watchlist.txt",
            "config/top30_watchlist.txt",
        ],
        profile_path="config/profiles/us_production.yaml",
        allow_fractional=False,
    ),
    "NSE": MarketSessionConfig(
        market_id="NSE",
        calendar_code="BSE",
        timezone="Asia/Kolkata",
        pre_open_offset_minutes=60,
        minute_offset=1,
        watchlist_file="config/nsetop10_watchlist.txt",
        watchlist_files=[
            "config/nsetop10_watchlist.txt",
            "config/nifty_50_watchlist.txt",
            "config/nseliqtop10_watchlist.txt",
        ],
        profile_path="config/profiles/nse_production.yaml",
        allow_fractional=False,
    ),
    "TSE": MarketSessionConfig(
        market_id="TSE",
        calendar_code="XTKS",
        timezone="Asia/Tokyo",
        pre_open_offset_minutes=60,
        minute_offset=1,
        watchlist_file="config/japan_top30_watchlist.txt",
        watchlist_files=["config/japan_top30_watchlist.txt"],
        profile_path=None,
        allow_fractional=False,
    ),
    "HKEX": MarketSessionConfig(
        market_id="HKEX",
        calendar_code="XHKG",
        timezone="Asia/Hong_Kong",
        pre_open_offset_minutes=60,
        minute_offset=1,
        watchlist_file="config/hk_top30_watchlist.txt",
        watchlist_files=["config/hk_top30_watchlist.txt"],
        profile_path=None,
        allow_fractional=False,
    ),
    "CRYPTO": MarketSessionConfig(
        market_id="CRYPTO",
        calendar_code="24/7",
        timezone="UTC",
        pre_open_offset_minutes=0,
        minute_offset=1,
        watchlist_file="config/crypto_watchlist.txt",
        watchlist_files=["config/crypto_watchlist.txt"],
        profile_path="config/profiles/crypto_production.yaml",
        allow_fractional=True,
    ),
}


class MarketSessionManager:
    """
    Manages exchange calendar logic, session boundaries, lunch breaks,
    and exact candle close schedules for a specific market venue.
    """

    _calendar_cache: Dict[str, Any] = {}

    def __init__(self, config: MarketSessionConfig):
        self.config = config
        self.tz = ZoneInfo(config.timezone)
        self.calendar_name = self.resolve_calendar_code(config.calendar_code)
        self.calendar = self._get_cached_calendar(self.calendar_name)

    @classmethod
    def resolve_calendar_code(cls, code: str) -> str:
        upper = code.strip().upper()
        return CALENDAR_ALIASES.get(upper, upper)

    @classmethod
    def _get_cached_calendar(cls, name: str):
        if name not in cls._calendar_cache:
            cls._calendar_cache[name] = xcals.get_calendar(name)
        return cls._calendar_cache[name]

    @classmethod
    def from_market_id(cls, market_id: str) -> "MarketSessionManager":
        upper = market_id.strip().upper()
        if upper in STANDARD_MARKET_CONFIGS:
            return cls(STANDARD_MARKET_CONFIGS[upper])
        # Fallback dynamic config
        cal_name = cls.resolve_calendar_code(upper)
        cal = cls._get_cached_calendar(cal_name)
        tz_name = str(cal.tz)
        cfg = MarketSessionConfig(market_id=upper, calendar_code=cal_name, timezone=tz_name)
        return cls(cfg)

    def to_market_tz(self, dt: Union[str, datetime, pd.Timestamp]) -> pd.Timestamp:
        """Converts any timestamp to the market's local timezone."""
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize(self.tz)
        else:
            ts = ts.tz_convert(self.tz)
        return ts

    def is_trading_day(self, dt: Union[str, date, datetime, pd.Timestamp]) -> bool:
        """Checks if the given calendar date is an active exchange session."""
        ts = pd.Timestamp(dt)
        session_label = ts.strftime("%Y-%m-%d")
        return bool(self.calendar.is_session(session_label))

    def is_open(self, dt: Union[str, datetime, pd.Timestamp]) -> bool:
        """
        Returns True if the exchange is currently open (and not in lunch break).
        """
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize(self.tz)
        ts_utc = ts.tz_convert("UTC")
        return bool(self.calendar.is_open_on_minute(ts_utc))

    def is_lunch_break(self, dt: Union[str, datetime, pd.Timestamp]) -> bool:
        """
        Returns True if the exchange has a scheduled lunch break and dt falls within it.
        """
        ts = self.to_market_tz(dt)
        session_label = ts.strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return False

        b_start, b_end = self.get_break_bounds(session_label)
        if b_start is None or b_end is None:
            return False
        return b_start <= ts < b_end

    def get_session_bounds(
        self, dt: Union[str, date, datetime, pd.Timestamp]
    ) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        """
        Returns (open_time, close_time) localized to the market's timezone.
        Returns (None, None) if the date is not a trading session.
        """
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return None, None

        open_utc = self.calendar.session_open(session_label)
        close_utc = self.calendar.session_close(session_label)
        return open_utc.tz_convert(self.tz), close_utc.tz_convert(self.tz)

    def get_break_bounds(
        self, dt: Union[str, date, datetime, pd.Timestamp]
    ) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        """
        Returns (break_start, break_end) localized to the market's timezone if any.
        """
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return None, None

        try:
            b_start = self.calendar.session_break_start(session_label)
            b_end = self.calendar.session_break_end(session_label)
            if b_start is not None and not pd.isna(b_start):
                b_start = b_start.tz_convert(self.tz)
            else:
                b_start = None
            if b_end is not None and not pd.isna(b_end):
                b_end = b_end.tz_convert(self.tz)
            else:
                b_end = None
            return b_start, b_end
        except Exception:
            return None, None

    def is_half_day(self, dt: Union[str, date, datetime, pd.Timestamp]) -> bool:
        """Detects early close sessions (e.g. US Black Friday, Christmas Eve)."""
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return False
        try:
            return bool(self.calendar.is_early_close(session_label))
        except Exception:
            # Fallback: compare session duration with normal close
            open_dt, close_dt = self.get_session_bounds(session_label)
            if open_dt and close_dt:
                duration_hours = (close_dt - open_dt).total_seconds() / 3600.0
                return duration_hours < 5.0
            return False

    def get_candle_close_schedule(
        self, dt: Union[str, date, datetime, pd.Timestamp], timeframe: str = "1h"
    ) -> List[pd.Timestamp]:
        """
        Calculates exact candle close timestamps for the session.
        Respects lunch breaks (e.g. TSE 11:30-12:30, HKEX 12:00-13:00) and early closes.
        """
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return []

        open_dt, close_dt = self.get_session_bounds(session_label)
        if open_dt is None or close_dt is None:
            return []

        b_start, b_end = self.get_break_bounds(session_label)

        def _generate_subsession_closes(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
            sub_closes = []
            cur = start
            step = timedelta(hours=1) if timeframe.lower() == "1h" else timedelta(minutes=30)
            while cur + step < end:
                cur += step
                sub_closes.append(cur)
            if cur < end:
                sub_closes.append(end)
            return sub_closes

        closes: List[pd.Timestamp] = []
        if b_start is not None and b_end is not None and b_start > open_dt and b_end < close_dt:
            # Morning subsession closes
            closes.extend(_generate_subsession_closes(open_dt, b_start))
            # Afternoon subsession closes
            closes.extend(_generate_subsession_closes(b_end, close_dt))
        else:
            closes.extend(_generate_subsession_closes(open_dt, close_dt))

        return closes

    def get_pre_open_time(
        self, dt: Union[str, date, datetime, pd.Timestamp]
    ) -> Optional[pd.Timestamp]:
        """Returns the pre-open universe refresh timestamp (T - pre_open_offset_minutes)."""
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return None
        open_dt, _ = self.get_session_bounds(session_label)
        if open_dt is None:
            return None
        if self.config.pre_open_offset_minutes <= 0:
            return None
        return open_dt - timedelta(minutes=self.config.pre_open_offset_minutes)

    def get_scheduled_events(
        self, dt: Union[str, date, datetime, pd.Timestamp], timeframe: str = "1h"
    ) -> List[Tuple[str, pd.Timestamp]]:
        """
        Returns an ordered list of (event_type, trigger_time) for the session:
        - ('PRE_OPEN_REFRESH', trigger_time)
        - ('CANDLE_SCAN', trigger_time) [includes minute_offset buffer]
        """
        session_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        if not self.is_trading_day(session_label):
            return []

        events = []
        pre_open = self.get_pre_open_time(session_label)
        if pre_open is not None:
            events.append(("PRE_OPEN_REFRESH", pre_open))

        closes = self.get_candle_close_schedule(session_label, timeframe=timeframe)
        buffer = timedelta(minutes=self.config.minute_offset)
        for cl in closes:
            events.append(("CANDLE_SCAN", cl + buffer))

        events.sort(key=lambda x: x[1])
        return events


class CandleBoundaryVerifier:
    """
    Verifies that a provider's latest candle has genuinely closed before triggering
    the breakout scan. Protects against premature scans, missing data, and stale feeds.
    """

    @staticmethod
    def verify_dataframe(
        df: pd.DataFrame,
        expected_close_time: Union[str, datetime, pd.Timestamp],
        tolerance_seconds: float = 120.0,
    ) -> dict:
        """
        Checks df.index[-1] timestamp against expected_close_time.
        """
        if df is None or df.empty:
            return {
                "valid": False,
                "status": "EMPTY_DATA",
                "latest_bar_time": None,
                "lag_seconds": None,
                "message": "Dataframe is empty or None",
            }

        expected_ts = pd.Timestamp(expected_close_time)
        latest_bar = pd.Timestamp(df.index[-1])

        # Normalize timezones to UTC for deterministic comparison
        if expected_ts.tzinfo is not None:
            expected_utc = expected_ts.tz_convert("UTC")
        else:
            expected_utc = expected_ts.tz_localize("UTC")

        if latest_bar.tzinfo is not None:
            latest_utc = latest_bar.tz_convert("UTC")
        else:
            latest_utc = latest_bar.tz_localize("UTC")

        # In 1H bars, df.index may represent the bar open or bar close
        # If df.index is bar open (expected_utc - 1 hour <= latest_utc <= expected_utc)
        diff_sec = (expected_utc - latest_utc).total_seconds()

        # If latest bar matches expected close or expected bar open (diff 0 or 3600 within tolerance)
        is_exact_close = abs(diff_sec) <= tolerance_seconds
        is_exact_open = abs(diff_sec - 3600.0) <= tolerance_seconds

        if is_exact_close or is_exact_open:
            return {
                "valid": True,
                "status": "FRESH",
                "latest_bar_time": latest_bar,
                "lag_seconds": round(abs(diff_sec if is_exact_close else (diff_sec - 3600.0)), 2),
                "message": "Latest bar verified complete and fresh",
            }

        # If data is behind
        lag = diff_sec if diff_sec > 0 else (diff_sec - 3600.0)
        if lag > tolerance_seconds:
            return {
                "valid": False,
                "status": "STALE_FEED",
                "latest_bar_time": latest_bar,
                "lag_seconds": round(lag, 2),
                "message": f"Provider feed is stale by {round(lag, 1)}s (> {tolerance_seconds}s limit)",
            }

        # If latest bar is ahead or waiting
        return {
            "valid": False,
            "status": "WAITING_FOR_CLOSE",
            "latest_bar_time": latest_bar,
            "lag_seconds": round(diff_sec, 2),
            "message": "Candle close not yet reflected in provider index",
        }

    @classmethod
    def wait_and_verify(
        cls,
        fetch_fn: Callable[[], pd.DataFrame],
        expected_close_time: Union[str, datetime, pd.Timestamp],
        max_wait_seconds: float = 90.0,
        poll_interval: float = 5.0,
        tolerance_seconds: float = 120.0,
    ) -> dict:
        """
        Polls fetch_fn() with backoff until candle close is verified or timeout is reached.
        If timeout is exceeded, fails closed with status STALE_FEED.
        """
        start = time.time()
        attempts = 0

        while True:
            attempts += 1
            try:
                df = fetch_fn()
                res = cls.verify_dataframe(df, expected_close_time, tolerance_seconds=tolerance_seconds)
                if res["valid"]:
                    res["attempts"] = attempts
                    res["wait_time_seconds"] = round(time.time() - start, 2)
                    return res
            except Exception as e:
                logger.warning(f"Error fetching candle bar in wait loop: {e}")

            elapsed = time.time() - start
            if elapsed >= max_wait_seconds:
                return {
                    "valid": False,
                    "status": "STALE_FEED",
                    "latest_bar_time": None,
                    "lag_seconds": elapsed,
                    "attempts": attempts,
                    "wait_time_seconds": round(elapsed, 2),
                    "message": f"Candle verification timed out after {round(elapsed, 1)}s. Failing closed.",
                }

            time.sleep(min(poll_interval, max_wait_seconds - elapsed))


class MultiMarketOrchestrator:
    """
    Coordinates session execution, holiday avoidance, and candle scan cadences
    across multiple global market venues (US, India, Japan, Hong Kong, Crypto).
    """

    def __init__(self, markets: Optional[List[str]] = None):
        target_markets = markets or ["US", "NSE", "TSE", "HKEX", "CRYPTO"]
        self.managers: Dict[str, MarketSessionManager] = {}
        for m in target_markets:
            upper = m.strip().upper()
            if upper in STANDARD_MARKET_CONFIGS:
                self.managers[upper] = MarketSessionManager(STANDARD_MARKET_CONFIGS[upper])
            else:
                self.managers[upper] = MarketSessionManager.from_market_id(upper)

    def get_manager(self, market_id: str) -> MarketSessionManager:
        upper = market_id.strip().upper()
        if upper not in self.managers:
            self.managers[upper] = MarketSessionManager.from_market_id(upper)
        return self.managers[upper]

    def get_active_markets(self, dt: Optional[Union[str, datetime, pd.Timestamp]] = None) -> List[str]:
        """Returns list of market IDs whose exchanges are currently open for trading."""
        check_dt = pd.Timestamp.now(tz="UTC") if dt is None else pd.Timestamp(dt)
        if check_dt.tzinfo is None:
            check_dt = check_dt.tz_localize("UTC")
        active = []
        for mid, mgr in self.managers.items():
            if mgr.is_open(check_dt):
                active.append(mid)
        return active

    def get_next_global_event(
        self, now_dt: Optional[Union[str, datetime, pd.Timestamp]] = None
    ) -> Optional[Tuple[str, str, pd.Timestamp]]:
        """
        Finds the next upcoming event across all configured markets.
        Returns: (market_id, event_type, event_time_utc)
        """
        now = pd.Timestamp.now(tz="UTC") if now_dt is None else pd.Timestamp(now_dt)
        if now.tzinfo is None:
            now = now.tz_localize("UTC")

        candidate_events: List[Tuple[str, str, pd.Timestamp]] = []

        # Check today and tomorrow for all markets
        for mid, mgr in self.managers.items():
            now_local = mgr.to_market_tz(now)
            for day_offset in [0, 1, 2]:
                check_date = (now_local + timedelta(days=day_offset)).date()
                events = mgr.get_scheduled_events(check_date)
                for ev_type, ev_time in events:
                    ev_utc = ev_time.tz_convert("UTC")
                    if ev_utc > now:
                        candidate_events.append((mid, ev_type, ev_utc))

        if not candidate_events:
            return None

        candidate_events.sort(key=lambda x: x[2])
        return candidate_events[0]

    def calculate_missed_scans(
        self,
        market_id: str,
        last_scan_utc: Union[str, datetime, pd.Timestamp],
        current_utc: Union[str, datetime, pd.Timestamp],
        max_age_hours: float = 4.0,
    ) -> List[pd.Timestamp]:
        """
        Deterministic recovery: if the host reboots, computes which candle close
        scans were missed within max_age_hours. Protects against stale signal generation.
        """
        mgr = self.get_manager(market_id)
        last_ts = pd.Timestamp(last_scan_utc)
        curr_ts = pd.Timestamp(current_utc)

        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        if curr_ts.tzinfo is None:
            curr_ts = curr_ts.tz_localize("UTC")

        cutoff = curr_ts - timedelta(hours=max_age_hours)
        effective_start = max(last_ts, cutoff)

        missed = []
        # Check sessions from effective_start to curr_ts
        start_date = mgr.to_market_tz(effective_start).date()
        end_date = mgr.to_market_tz(curr_ts).date()

        cur_date = start_date
        while cur_date <= end_date:
            if mgr.is_trading_day(cur_date):
                events = mgr.get_scheduled_events(cur_date)
                for ev_type, ev_time in events:
                    if ev_type == "CANDLE_SCAN":
                        ev_utc = ev_time.tz_convert("UTC")
                        if effective_start < ev_utc <= curr_ts:
                            missed.append(ev_time)
            cur_date += timedelta(days=1)

        missed.sort()
        return missed
