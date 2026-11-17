import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch

from breakout_scanner.config import load_config
from breakout_scanner.models import Setup, State
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.confluence import check_earnings_proximity, clear_earnings_cache, _EARNINGS_CACHE
from breakout_scanner.audit_ledger import record_forward_test_ledger
from breakout_scanner.outcome_tracker import evaluate_setup_outcome_from_df, update_ledger_outcomes


def test_circuit_breaker_does_not_abort_scan_universe():
    """
    Defect 1 Regression Test:
    Verifies that when 3 symbols fail or are delisted, the scanner and provider
    do NOT trip a global breaker that aborts remaining valid symbols.
    """
    class MockUniverseProvider:
        def __init__(self):
            self.daily_cache = {}
            self.daily_cache_ts = {}
            self.market_cache = {}
            self.market_cache_ts = {}
            self.calls = []

        def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
            self.calls.append((symbol, interval))
            if symbol.startswith("BAD"):
                return pd.DataFrame()  # Simulate bad/delisted ticker
            # Valid ticker
            dates = pd.date_range("2026-11-01", periods=60, freq="1h")
            return pd.DataFrame({
                "Open": np.linspace(100, 110, 60),
                "High": np.linspace(101, 111, 60),
                "Low": np.linspace(99, 109, 60),
                "Close": np.linspace(100.5, 110.5, 60),
                "Volume": [1000] * 60,
            }, index=dates)

    provider = MockUniverseProvider()
    cfg = load_config("config/default.yaml")
    scanner = MultiSymbolScanner(provider, cfg=cfg)

    # Universe with 3 bad tickers followed by 2 good tickers
    symbols = ["BAD1", "BAD2", "BAD3", "GOOD1", "GOOD2"]
    setups = scanner.scan(symbols)

    # All primary universe symbols must have been attempted for 1H candles!
    universe_1h_calls = [c[0] for c in provider.calls if c[1] == "1h" and c[0] in symbols]
    assert universe_1h_calls == ["BAD1", "BAD2", "BAD3", "GOOD1", "GOOD2"]
    # Good tickers must have generated setups, not been dropped
    assert len(setups) == 2
    assert [s.symbol for s in setups] == ["GOOD1", "GOOD2"]


def test_auxiliary_circuit_breaker_marks_setups_provisional():
    """
    Defect 1 Auxiliary Breaker Test:
    When auxiliary benchmark downloads fail 3 times, auxiliary circuit breaker trips,
    bypassing subsequent benchmark downloads while allowing setups to exist as PROVISIONAL.
    """
    class MockAuxFailingProvider:
        def __init__(self):
            self.daily_cache = {}
            self.daily_cache_ts = {}
            self.market_cache = {}
            self.market_cache_ts = {}

        def candles(self, symbol, period="60d", interval="1h", enforce_closed_candle=None):
            if interval == "1d":
                raise RuntimeError("429 Rate Limited on Benchmark")
            dates = pd.date_range("2026-11-01", periods=60, freq="1h")
            return pd.DataFrame({
                "Open": np.linspace(100, 110, 60),
                "High": np.linspace(101, 111, 60),
                "Low": np.linspace(99, 109, 60),
                "Close": np.linspace(100.5, 110.5, 60),
                "Volume": [1000] * 60,
            }, index=dates)

    provider = MockAuxFailingProvider()
    cfg = load_config("config/default.yaml")
    scanner = MultiSymbolScanner(provider, cfg=cfg)

    dates = pd.date_range("2026-11-01", periods=60, freq="1h")
    df = pd.DataFrame({
        "Open": np.linspace(100, 110, 60),
        "High": np.linspace(101, 111, 60),
        "Low": np.linspace(99, 109, 60),
        "Close": np.linspace(100.5, 110.5, 60),
        "Volume": [1000] * 60,
    }, index=dates)

    # Force 3 auxiliary failures
    scanner.consecutive_aux_failures = 3

    setup = Setup(
        symbol="AAPL",
        state=State.BUY,
        direction=1,
        entry=110.5,
        current_price=110.5,
        breakout_level=110.0,
        resistance=110.0,
        stop=108.0,
        trend=1,
    )

    scanner.enrich_bias(setup, df, period="60d", interval="1h")

    # Setup must NOT disappear! It must be flagged provisional.
    assert setup.htf_data_status == "FAILED"
    assert setup.provisional_data_incomplete is True
    assert setup.confirmation_status == "PROVISIONAL - DATA INCOMPLETE"


def test_earnings_cache_ttl_and_dynamic_recalculation():
    """
    Defect 2 Regression Test:
    Verifies that:
    1. Days until earnings recalculates dynamically within cache TTL as calendar days advance.
    2. Cached earnings expire after 12h TTL and fetch fresh updates.
    3. Failed/Unknown earnings lookups expire within 15 minutes TTL rather than persisting indefinitely.
    """
    clear_earnings_cache()

    base_time = datetime(2026, 11, 16, 20, 0, 0, tzinfo=timezone.utc)
    target_earnings_date = date(2026, 11, 21)  # 5 days in future

    # 1. Directly inject cached entry created at 8 PM on Nov 16
    _EARNINGS_CACHE["ACME"] = {
        "earning_date": target_earnings_date,
        "status": "OK",
        "cached_at": base_time,
    }

    # 10 hours later (6 AM on Nov 17): Age is 10 hours (< 12h TTL -> FRESH cache hit!)
    later_time = datetime(2026, 11, 17, 6, 0, 0, tzinfo=timezone.utc)
    with patch("breakout_scanner.confluence.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = later_time

        days, imminent, status = check_earnings_proximity("ACME", return_status=True)
        assert status == "OK"
        # Dynamic recalculation: Nov 21 - Nov 17 = 4 days! (Adjusted dynamically from 5 days)
        assert days == 4
        assert imminent is False

    # 2. 3 days later (Nov 19): Age is 72 hours (> 12h TTL -> EXPIRED!)
    # Should re-query yfinance and refresh cache
    expired_time = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    with patch("breakout_scanner.confluence.datetime.datetime") as mock_dt, \
         patch("yfinance.Ticker") as mock_ticker:
        mock_dt.now.return_value = expired_time
        mock_instance = MagicMock()
        mock_instance.calendar = {"Earnings Date": [target_earnings_date]}
        mock_ticker.return_value = mock_instance

        days, imminent, status = check_earnings_proximity("ACME", return_status=True)
        assert status == "OK"
        # Nov 21 - Nov 19 = 2 days -> imminent!
        assert days == 2
        assert imminent is True
        assert mock_ticker.called

    # 3. Verify 15-minute TTL on FAILED lookups
    clear_earnings_cache()
    _EARNINGS_CACHE["THROTTLED"] = {
        "earning_date": None,
        "status": "FAILED",
        "cached_at": datetime.now(timezone.utc) - timedelta(minutes=20),  # 20 minutes old (> 15m TTL)
    }

    with patch("yfinance.Ticker") as mock_ticker:
        mock_instance = MagicMock()
        mock_instance.calendar = {"Earnings Date": [target_earnings_date]}
        mock_ticker.return_value = mock_instance

        days_retry, _, status_retry = check_earnings_proximity("THROTTLED", return_status=True)
        assert status_retry == "OK"
        assert mock_ticker.called


def test_tracker_fills_at_exact_next_open():
    """
    Defect 3 Regression Test:
    Simulates probe: Breakout candle at 09:30 ET, scan runs at 10:31 ET.
    Verifies that outcome tracker fills at Bar 10:30 Open (101.0), NOT two bars late at 12:30 (103.05).
    """
    record = {
        "signal_id": "TEST_PROBE_1",
        "symbol": "PROBE",
        "direction": "BUY",
        "timestamp": "2026-11-16 10:31:00-05:00",
        "breakout_time": "2026-11-16 09:30:00-05:00",
        "price": {
            "entry": 100.5,
            "stop": 99.0,
            "tp1": 103.0,
            "tp2": 105.0,
            "resistance": 100.0,
        },
    }

    # 4 hourly candles starting at 09:30 ET
    dates = pd.date_range("2026-11-16 09:30:00-05:00", periods=4, freq="1h")
    df = pd.DataFrame({
        "Open": [99.5, 101.0, 102.0, 103.05],
        "High": [100.5, 101.8, 102.5, 103.50],
        "Low": [99.0, 100.8, 101.8, 102.80],
        "Close": [100.5, 101.5, 102.2, 103.20],
        "Volume": [5000] * 4,
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(record, df)

    # Must fill at 10:30 bar Open (101.0 * 1.0005 = 101.0505)
    # NOT two bars late at 12:30 (103.05)!
    assert outcome["actual_fill"] == pytest.approx(101.0505, abs=0.001)
    assert outcome["status"] == "IN_PROGRESS"
    assert outcome["bars_elapsed"] == 3  # Bars 10:30, 11:30, 12:30


def test_failed_breakout_exit_fires_correctly(tmp_path):
    """
    Defect 4 Regression Test:
    Verifies that resistance and support written to ledger are ingested by outcome tracker
    and successfully trigger FAILED_BREAKOUT early exit within the first 3 bars post-fill.
    """
    ledger_file = str(tmp_path / "test_failed_bo_ledger.jsonl")
    cfg = {"profile_name": "Standard"}

    now_ts = pd.Timestamp.now(tz="UTC").floor("1h")
    now_str = str(now_ts)

    setup = Setup(
        symbol="FAIL_BO",
        state=State.BUY,
        direction=1,
        entry=105.0,
        stop=102.0,
        resistance=104.5,
        support=101.0,
        breakout_level=104.5,
        breakout_time=now_str,
    )

    count = record_forward_test_ledger([setup], cfg, ledger_path=ledger_file)
    assert count == 1

    with open(ledger_file, "r") as f:
        rec = json.loads(f.readline())

    # Ledger must write resistance, support, and breakout_level into price!
    assert rec["price"]["resistance"] == 104.5
    assert rec["price"]["support"] == 101.0
    assert rec["price"]["breakout_level"] == 104.5
    assert rec["breakout_time"] == now_str

    # Candle series where price falls back below resistance (104.5) on Bar 2
    dates = pd.date_range(now_ts, periods=4, freq="1h")
    df = pd.DataFrame({
        "Open": [104.0, 105.0, 104.4, 103.5],
        "High": [105.0, 105.5, 104.6, 103.8],
        "Low": [103.8, 104.8, 103.8, 103.0],
        "Close": [104.9, 105.1, 104.0, 103.2],  # Bar 2 closes at 104.0 (< resistance 104.5)
        "Volume": [1000] * 4,
    }, index=dates)

    outcome = evaluate_setup_outcome_from_df(rec, df)

    # Must terminate as FAILED_BREAKOUT!
    assert outcome["status"] == "EVALUATED"
    assert outcome["evaluated"] is True
    assert outcome["exit_reason"] == "FAILED_BREAKOUT"
    assert outcome["hypothetical_r"] < 0  # Exited at loss before full stop hit
