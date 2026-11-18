import pytest
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock
from breakout_scanner.scanner import MultiSymbolScanner


def test_data_age_in_bar_is_zero():
    """
    If we are currently within the bar duration (bar has not closed),
    data_age_minutes must be 0.0 (not negative).
    """
    provider = MagicMock()
    # Create a 1h bar starting 15 minutes ago
    now = datetime.now(timezone.utc)
    bar_start = now - pd.Timedelta(minutes=15)
    dates = [bar_start]
    df = pd.DataFrame({
        "Open": [100.0],
        "High": [101.0],
        "Low": [99.0],
        "Close": [100.5],
        "Volume": [1000.0],
    }, index=dates)

    provider.candles.return_value = df
    provider.fetch_history.return_value = df
    scanner = MultiSymbolScanner(provider, cfg={"data_health": {}})
    scanner.scan(["TEST"], period="1d", interval="1h", market="US")

    trace = scanner.last_symbol_traces[0]
    fetch = trace["fetch"]
    assert fetch["data_age_minutes"] == 0.0


def test_data_age_closed_bar_reflects_time_since_close():
    """
    If a 1h bar closed 33 minutes ago (started 93 minutes ago),
    data_age_minutes must be ~33.0 minutes.
    """
    provider = MagicMock()
    now = datetime.now(timezone.utc)
    bar_start = now - pd.Timedelta(minutes=93)
    dates = [bar_start]
    df = pd.DataFrame({
        "Open": [100.0],
        "High": [101.0],
        "Low": [99.0],
        "Close": [100.5],
        "Volume": [1000.0],
    }, index=dates)

    provider.candles.return_value = df
    provider.fetch_history.return_value = df
    scanner = MultiSymbolScanner(provider, cfg={"data_health": {}})
    scanner.scan(["TEST"], period="1d", interval="1h", market="US")

    trace = scanner.last_symbol_traces[0]
    fetch = trace["fetch"]
    # 93 min since start - 60 min bar = 33 min since close
    assert 32.5 <= fetch["data_age_minutes"] <= 33.5


def test_last_bar_close_capped_at_session_close_for_nse():
    """
    For an NSE 1h candle starting at 15:00 IST on a normal trading session (e.g. 2026-11-17),
    session close is 15:30 IST. The bar close must be capped at 15:30 IST, not 16:00 IST.
    """
    provider = MagicMock()
    # 2026-11-17 is a Tuesday (active trading day)
    bar_start = pd.Timestamp("2026-11-17 15:00:00", tz="Asia/Kolkata")
    df = pd.DataFrame({
        "Open": [100.0],
        "High": [101.0],
        "Low": [99.0],
        "Close": [100.5],
        "Volume": [1000.0],
    }, index=[bar_start])

    provider.candles.return_value = df
    provider.fetch_history.return_value = df
    scanner = MultiSymbolScanner(provider, cfg={"data_health": {}})
    scanner.scan(["RELIANCE.NS"], period="1d", interval="1h", market="NSE")

    trace = scanner.last_symbol_traces[0]
    fetch = trace["fetch"]
    assert fetch["last_bar_close_time"] is not None
    close_ts = pd.Timestamp(fetch["last_bar_close_time"])
    # Localized to Kolkata: should be 15:30:00
    assert close_ts.hour == 15
    assert close_ts.minute == 30
