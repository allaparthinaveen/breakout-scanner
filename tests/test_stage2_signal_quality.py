"""
Stage 2: Signal Quality & Precision Test Suite (P1-1 through P1-4)
=================================================================
Validates:
1. P1-2 Session-Aligned & Non-Repainting 4H Context
2. P1-4 Conservative Same-Candle Ambiguity Resolution
3. P1-3 Explicit Trade Management Exit Events & Section 2C Reporting
4. P1-1 Reliable Bid/Ask & Execution Price Validation
"""

import os
import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date

from breakout_scanner.models import Setup, State, TradeExitEvent
from breakout_scanner.confluence import compute_4h_confluence
from breakout_scanner.portfolio_state import PortfolioStateManager
from breakout_scanner.portfolio_guard import PositionAllocation
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.safeguards import ProductionSafeguards
from breakout_scanner.telegram_formatter import format_scan_report_html
from breakout_scanner.simulation import simulate_forward_trade
from breakout_scanner.backtester import Backtester


# =====================================================================
# 1. P1-2: Session-Aligned & Completed 4H Candles
# =====================================================================

def test_p1_2_us_session_aligned_4h_resampling():
    """Verify that US market 4H candles align to 09:30 session open with offset."""
    # 80 hourly bars starting at 09:30 EST
    dates = pd.date_range("2026-01-05 09:30:00", periods=80, freq="1h", tz="America/New_York")
    closes = np.linspace(100.0, 140.0, 80)
    df_us = pd.DataFrame({
        "Open": closes - 0.5,
        "High": closes + 1.0,
        "Low": closes - 1.0,
        "Close": closes,
        "Volume": [5000] * 80
    }, index=dates)

    trend, confirmed = compute_4h_confluence(df_us, direction=1, sma_len=10, market="US")
    assert trend == 1
    assert confirmed is True


def test_p1_2_completed_candle_drops_incomplete_open_bar():
    """Verify that a newly started 4H bar with only 1 hourly candle is dropped to prevent repainting."""
    # 81 hourly bars (80 full bars = 20 complete 4H bars, plus 1 stray bar into a new 4H window)
    dates = pd.date_range("2026-01-01 00:00:00", periods=81, freq="1h")
    closes = np.linspace(100.0, 140.0, 80).tolist()
    # 81st bar is a sharp down spike
    closes.append(80.0)

    df = pd.DataFrame({
        "Open": closes,
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1000] * 81
    }, index=dates)

    # With drop_incomplete=True, the 81st 1-bar blip is dropped from 4H SMA, preserving the established trend
    trend, confirmed = compute_4h_confluence(df, direction=1, sma_len=10, drop_incomplete=True)
    assert trend == 1
    assert confirmed is True


# =====================================================================
# 2. P1-4: Conservative Same-Candle Ambiguity Resolution
# =====================================================================

def test_p1_4_same_candle_conservative_stop_priority_in_simulation():
    """Verify that if a single candle penetrates both Stop Loss and TP, stop loss takes priority."""
    candidate = {
        "symbol": "TEST",
        "state": "BUY",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "requires_confirmation": False
    }

    # Bar 1: Normal open
    # Bar 2: Huge spike and dump: Open 101, High 106 (hits TP1), Low 94 (hits Stop 95)
    future = pd.DataFrame([
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
        {"Open": 101.0, "High": 106.0, "Low": 94.0, "Close": 95.5, "Volume": 5000},
    ])

    cfg = {
        "risk": {"slippage_pct": 0.0, "commission_pct": 0.0},
        "management": {"use_breakeven": True}
    }
    result = simulate_forward_trade(candidate, future, cfg=cfg)

    # Conservative invariant: Must be stopped out!
    assert result["outcome"] == "STOPPED_OUT"
    assert result["stopped_out"] is True
    assert result["t1_hit"] is False


def test_p1_4_same_candle_breakeven_stop_after_tp1():
    """Verify that if candle hits TP1 but its low also reaches the new breakeven stop, remainder exits at breakeven."""
    candidate = {
        "symbol": "TEST",
        "state": "BUY",
        "direction": 1,
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "requires_confirmation": False
    }

    # Bar 1 enters at 100.0
    # Bar 2 opens at 102.0, spikes to High 106.0 (hits TP1 105.0), but Low crashes to 99.5 (breaches breakeven stop ~100.10)
    future = pd.DataFrame([
        {"Open": 100.0, "High": 101.0, "Low": 99.5, "Close": 100.2, "Volume": 1000},
        {"Open": 102.0, "High": 106.0, "Low": 99.5, "Close": 99.8, "Volume": 5000},
        {"Open": 104.0, "High": 112.0, "Low": 103.0, "Close": 111.0, "Volume": 5000},  # Bar 3 reaches TP2
    ])

    cfg = {
        "risk": {"slippage_pct": 0.0, "commission_pct": 0.0},
        "management": {"use_breakeven": True, "breakeven_offset_pct": 0.10}
    }
    result = simulate_forward_trade(candidate, future, cfg=cfg, use_partial_exits=True)

    # TP1 should be hit, but remainder must be stopped out at BREAKEVEN on Bar 2 (never reaching Bar 3's TP2!)
    assert result["t1_hit"] is True
    assert result["exit_reason"] == "BREAKEVEN_STOP"
    assert result["t2_hit"] is False


# =====================================================================
# 3. P1-3: Explicit Trade Management Exit Events & Section 2C
# =====================================================================

def test_p1_3_portfolio_state_produces_explicit_exit_events(tmp_path):
    """Verify that PortfolioStateManager produces TradeExitEvent on stop and target breaches."""
    state_file = str(tmp_path / "exit_events_state.json")
    mgr = PortfolioStateManager(state_file=state_file)

    # Record trade: AAPL long @ 150, stop 145, TP1 155, TP2 160
    alloc = PositionAllocation(
        symbol="AAPL",
        direction=1,
        shares=100,
        entry_price=150.0,
        stop_loss=145.0,
        take_profit_1=155.0,
        take_profit_2=160.0,
        dollar_risk=500.0,
        position_value=15000.0,
        risk_pct_of_equity=1.0,
        sector="Tech"
    )
    mgr.record_entry(alloc)

    # 1. Update with TP1 reached: Should produce TP1_SCALE event and trail stop to breakeven
    s_tp1 = Setup("AAPL")
    s_tp1.current_price = 156.0
    events1 = mgr.update_market_data([s_tp1])
    assert len(events1) == 1
    assert events1[0].event_type == "TP1_SCALE"
    assert events1[0].symbol == "AAPL"
    assert events1[0].shares == 50  # Scaled 50%
    assert mgr.active_positions["AAPL"]["shares"] == 50
    assert mgr.active_positions["AAPL"]["stop_loss"] > 150.0  # Trailed to BE

    # 2. Update with Stop Loss breached: Should produce STOP_LOSS event and close remaining shares
    s_stop = Setup("AAPL")
    s_stop.current_price = 149.0  # Below new BE stop
    events2 = mgr.update_market_data([s_stop])
    assert len(events2) == 1
    assert events2[0].event_type == "STOP_LOSS"
    assert len(mgr.get_open_positions()) == 0


def test_p1_3_section_2c_telegram_rendering():
    """Verify Section 2C renders trade exits and scale-outs in HTML report."""
    exit_events = [
        TradeExitEvent(
            symbol="NVDA",
            event_type="TP2_EXIT",
            direction=1,
            entry_price=140.0,
            exit_price=155.0,
            pnl_pct=10.71,
            realized_pnl=1500.0,
            shares=100,
            reason="TP2 Hit"
        ),
        TradeExitEvent(
            symbol="AMD",
            event_type="STOP_LOSS",
            direction=1,
            entry_price=160.0,
            exit_price=152.0,
            pnl_pct=-5.0,
            realized_pnl=-800.0,
            shares=100,
            reason="Stop Loss Hit"
        )
    ]

    report = format_scan_report_html([], market="US", exit_events=exit_events)
    assert "SECTION 2C: TRADE EXITS & MANAGEMENT EVENTS" in report
    assert "NVDA" in report
    assert "TP2 HIT — FULL EXIT" in report
    assert "AMD" in report
    assert "STOP LOSS HIT" in report


# =====================================================================
# 4. P1-1: Reliable Bid/Ask & Execution Price Validation
# =====================================================================

def test_p1_1_quote_provider_synthetic_spread_calculation():
    """Verify YFinanceProvider generates synthetic spread from volatility microstructure when Level 1 book is missing."""
    provider = YFinanceProvider()
    quote = provider.get_ticker_quote("SYNTH_TEST", fallback_price=150.0, atr=3.0)

    assert quote["last_price"] == 150.0
    assert quote["bid"] is not None
    assert quote["ask"] is not None
    assert quote["bid"] < quote["ask"]
    assert quote["spread_pct"] > 0.0


def test_p1_1_pre_trade_safeguard_spread_rejection():
    """Verify pre_trade_validation rejects wide bid/ask spreads."""
    safeguards = ProductionSafeguards(cfg={
        "risk": {"max_spread_pct": 0.50, "min_liquidity_volume": 10000}
    })

    s = Setup("ILLIQ")
    s.state = State.BUY
    s.risk_pct = 2.0

    # Normal spread (0.10%) passes
    ok = safeguards.pre_trade_validation(s, current_capital=100000, bid=99.9, ask=100.0, volume=50000)
    assert ok is True

    # Blown out spread (1.5%) is rejected
    rejected = safeguards.pre_trade_validation(s, current_capital=100000, bid=98.5, ask=100.0, volume=50000)
    assert rejected is False
