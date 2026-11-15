import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch

from breakout_scanner.models import Setup, State
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.scorer import calculate_composite_score
from breakout_scanner.telegram_formatter import format_setup_telegram_line
from breakout_scanner.slate import SlatePipeline


class MockFailingProvider:
    def __init__(self):
        self.daily_cache = {}
        self.daily_cache_ts = {}
        self.market_cache = {}
        self.market_cache_ts = {}

    def candles(self, symbol, period, interval):
        # Simulate Yahoo Finance outage / throttling
        raise RuntimeError("429 Client Error: Too Many Requests for url: https://query1.finance.yahoo.com")


def test_failing_provider_triggers_provisional_exemption():
    """
    Verifies that when external provider fails, the setup:
    1. Sets htf_data_status and sector_data_status to 'FAILED'.
    2. Flags provisional_data_incomplete as True.
    3. Is NOT suppressed as AWAITING_CONFIRMATION in slate/portfolio_guard.
    4. Has proper PROVISIONAL badge in telegram formatting.
    """
    failing_provider = MockFailingProvider()
    cfg = {
        "confirmation_policy": {"min_auto_entry_score": 70},
        "portfolio_guard": {"enabled": True},
    }
    scanner = MultiSymbolScanner(failing_provider, cfg)

    # Synthetic 1H candle series for setup
    dates = pd.date_range("2026-11-01", periods=30, freq="1h")
    df = pd.DataFrame({
        "Open": np.linspace(100, 105, 30),
        "High": np.linspace(101, 106, 30),
        "Low": np.linspace(99, 104, 30),
        "Close": np.linspace(100.5, 105.5, 30),
        "Volume": [1000] * 30,
    }, index=dates)

    setup = Setup(
        symbol="TEST",
        state=State.BUY,
        direction=1,
        entry=105.5,
        current_price=105.5,
        breakout_level=105.0,
        resistance=105.0,
        bars_since_breakout=1,
        stop=104.0,
        risk_pct=1.42,
        trend=1,
        base_width_pct=3.5,
        tightening_confirmed=True,
        decay_ratio=0.5,
        vdu_confirmed=True,
        vdu_ratio=0.6,
        rvol=2.5,
        tp1=107.0,
        tp2=108.5,
    )

    # Enrich bias with failing external provider
    scanner.enrich_bias(setup, df, period="60d", interval="1h")

    assert setup.htf_data_status == "FAILED"
    assert setup.sector_data_status == "FAILED"
    assert setup.regime_data_status == "FAILED"
    assert setup.provisional_data_incomplete is True
    assert setup.requires_confirmation is False
    assert setup.confirmation_status == "PROVISIONAL - DATA INCOMPLETE"

    # Verify Terminal formatting includes PROVISIONAL badge
    term_line = scanner.format_active_setup(setup, is_fresh=True)
    assert "[PROVISIONAL]" in term_line

    # Verify Telegram formatting includes PROVISIONAL badge
    tg_line = format_setup_telegram_line(setup, is_fresh=True)
    assert "[⚠️ PROVISIONAL: DATA INCOMPLETE]" in tg_line

    # Verify SlatePipeline does NOT suppress it and actively approves it
    pipeline = SlatePipeline(cfg=cfg, provider=failing_provider)
    slate = pipeline.evaluate_setups([setup])
    assert setup.symbol not in [r.symbol for r in slate.suppressed_trades if r.suppression_code == "AWAITING_CONFIRMATION"]
    assert setup.symbol in [alloc.symbol for alloc in slate.selected_trades]


def test_flat_daily_trend_is_not_marked_as_failed():
    """
    Verifies that a legitimately flat daily trend (htf_trend == 0) with successful fetch:
    1. Sets htf_data_status to 'OK'.
    2. Does NOT mark provisional_data_incomplete as True.
    3. Scorer does not flag 'Unconfirmed HTF (Failed Fetch)'.
    """
    class MockFlatProvider:
        def __init__(self):
            self.daily_cache = {}
            self.daily_cache_ts = {}
            self.market_cache = {}
            self.market_cache_ts = {}

        def candles(self, symbol, period, interval):
            dates = pd.date_range("2026-01-01", periods=60, freq="1d")
            # Completely flat series -> slope is 0 -> htf_trend == 0
            return pd.DataFrame({
                "Open": [100.0] * 60,
                "High": [101.0] * 60,
                "Low": [99.0] * 60,
                "Close": [100.0] * 60,
                "Volume": [50000] * 60,
            }, index=dates)

    flat_provider = MockFlatProvider()
    scanner = MultiSymbolScanner(flat_provider, {})
    
    dates = pd.date_range("2026-11-01", periods=30, freq="1h")
    df = pd.DataFrame({
        "Open": [100.0] * 30,
        "High": [101.0] * 30,
        "Low": [99.0] * 30,
        "Close": [100.0] * 30,
        "Volume": [1000] * 30,
    }, index=dates)

    setup = Setup(symbol="FLAT", state=State.BUY, direction=1, trend=1)
    scanner.enrich_bias(setup, df, period="60d", interval="1h")

    assert setup.htf_data_status == "OK"
    assert setup.htf_trend == 0
    assert setup.provisional_data_incomplete is False

    score, grade = calculate_composite_score(setup)
    assert "Unconfirmed HTF (Failed Fetch)" not in setup.evidence_status
