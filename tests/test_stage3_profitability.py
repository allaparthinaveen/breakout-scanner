"""
Stage 3: Prove Profitability Verification Test Suite
====================================================
Tests:
1. Ablation expectancy monotonicity (Grade A/A+ positive E(R), Grade B/C filtered).
2. Chronological Walk-Forward OOS anti-overfitting bounds (<= 20% degradation).
3. PaperTradingDaemon anti-chase extension cancellation.
4. PaperTradingDaemon same-candle breakeven collapse exit invariant.
5. Go/No-Go release gate evaluation logic.
"""

import os
import pytest
import pandas as pd
import numpy as np

from breakout_scanner.paper_trading.daemon import PaperTradingDaemon
from breakout_scanner.paper_trading.models import PaperOrder
from breakout_scanner.paper_trading.audit import GoNoGoAuditor
from tools.run_ablation_study import (
    calculate_trade_metrics,
    run_ablation_matrix,
    run_walk_forward_holdout_split,
    run_regime_and_tier_decomposition,
)


def create_candle(open_p, high_p, low_p, close_p, ts_str="2024-01-01 10:00:00"):
    s = pd.Series({"Open": open_p, "High": high_p, "Low": low_p, "Close": close_p})
    s.name = pd.Timestamp(ts_str)
    return s


def test_score_tier_expectancy_monotonicity_logic():
    """Verify that tier decomposition confirms higher expectancy for higher conviction tiers."""
    trades = [
        # Grade A+ setups
        {"net_pnl": 500.0, "realized_r": 2.0, "composite_score": 95, "bars_held": 4, "exit_reason": "TP2_HIT"},
        {"net_pnl": 250.0, "realized_r": 1.0, "composite_score": 92, "bars_held": 2, "exit_reason": "TP1_HIT"},
        # Grade A setups
        {"net_pnl": 200.0, "realized_r": 1.0, "composite_score": 85, "bars_held": 3, "exit_reason": "TP1_HIT"},
        {"net_pnl": 100.0, "realized_r": 0.5, "composite_score": 82, "bars_held": 3, "exit_reason": "TIME_EXIT"},
        # Grade B setups (unconfirmed)
        {"net_pnl": -200.0, "realized_r": -1.0, "composite_score": 75, "bars_held": 2, "exit_reason": "STOP_LOSS"},
        {"net_pnl": -200.0, "realized_r": -1.0, "composite_score": 72, "bars_held": 1, "exit_reason": "STOP_LOSS"},
        # Grade C setups
        {"net_pnl": -250.0, "realized_r": -1.0, "composite_score": 60, "bars_held": 1, "exit_reason": "STOP_LOSS"},
    ]

    decomp = run_regime_and_tier_decomposition(trades)
    tier_aplus = decomp["tiers"]["Grade A+ (90-100)"]
    tier_a = decomp["tiers"]["Grade A (80-89)"]
    tier_b = decomp["tiers"]["Grade B (70-79)"]
    tier_c = decomp["tiers"]["Grade C (< 70)"]

    assert tier_aplus["expectancy_r"] > 0
    assert tier_a["expectancy_r"] > 0
    assert tier_b["expectancy_r"] < 0
    assert tier_c["expectancy_r"] < 0
    assert tier_aplus["expectancy_r"] >= tier_a["expectancy_r"]


def test_walk_forward_anti_overfitting_bound():
    """Verify that chronological walk-forward evaluates OOS degradation within 20% threshold."""
    # Deterministic simulation with matching IS and OOS
    dates = pd.date_range("2024-01-01", periods=150, freq="h")
    prices = [100.0 + (0.5 * np.sin(i / 5.0)) for i in range(100)]
    prices.extend([102.0 + (i - 100) * 0.4 for i in range(100, 120)])
    prices.extend([108.0 - (i - 120) * 0.2 for i in range(120, 150)])
    df = pd.DataFrame({
        "Open": prices,
        "High": [p + 0.8 for p in prices],
        "Low": [p - 0.8 for p in prices],
        "Close": prices,
        "Volume": [1000.0 if i < 100 else 5000.0 for i in range(150)]
    }, index=dates)

    mock_data = {"SYM": df}
    split_res = run_walk_forward_holdout_split(mock_data)

    assert "degradation_pct" in split_res
    assert "anti_overfit_passed" in split_res
    assert split_res["degradation_pct"] <= 20.0


def test_paper_trading_daemon_cancels_extended_chase(tmp_path):
    """Verify that PaperTradingDaemon cancels pending orders when next-bar open gaps beyond chase tolerance."""
    daemon = PaperTradingDaemon(
        cfg={"breakout": {"use_extension_filter": True, "max_chase_pct": 2.0}},
        order_ledger_path=str(tmp_path / "orders.jsonl"),
        perf_ledger_path=str(tmp_path / "perf.jsonl"),
    )

    # Queue an order with resistance = 100.0
    order = PaperOrder(
        order_id="CHASE01",
        symbol="XYZ",
        direction=1,
        shares=100.0,
        theoretical_price=100.5,
        signal_timestamp="2024-01-01 10:00:00",
        stop_loss=98.0,
        take_profit_1=103.0,
        take_profit_2=105.0,
        metadata={"resistance": 100.0, "atr": 1.0}
    )
    daemon.pending_orders["XYZ"] = order

    # Next candle opens at 103.5 (> 3.5% above resistance, exceeding 2.0% max chase)
    next_candle = create_candle(open_p=103.5, high_p=104.0, low_p=103.0, close_p=103.8, ts_str="2024-01-01 11:00:00")
    dummy_hist = pd.DataFrame([next_candle])

    daemon.ingest_closed_candle("XYZ", next_candle, dummy_hist)

    # Order must be CANCELLED, not filled
    assert "XYZ" not in daemon.active_positions
    assert "XYZ" not in daemon.pending_orders
    assert len(daemon.variance_records) == 0  # No fill variance recorded because order was rejected


def test_paper_trading_daemon_same_candle_breakeven_collapse(tmp_path):
    """Verify that in PaperTradingDaemon, if TP1 is hit and bar collapses below breakeven stop, remainder exits immediately."""
    daemon = PaperTradingDaemon(
        order_ledger_path=str(tmp_path / "orders.jsonl"),
        perf_ledger_path=str(tmp_path / "perf.jsonl"),
    )

    # Pre-populate active position
    daemon.active_positions["ABC"] = {
        "symbol": "ABC",
        "direction": 1,
        "shares_total": 100.0,
        "shares_remaining": 100.0,
        "entry_price": 100.0,
        "current_stop": 97.0,
        "tp1": 103.0,
        "tp2": 106.0,
        "tp1_hit": False,
        "open_time": "2024-01-01 10:00:00",
        "bars_held": 1,
        "order_id": "ORD001",
        "risk_cash": 300.0,
        "realized_pnl": 0.0,
        "atr": 1.5
    }

    # Candle hits TP1 (High >= 103.0), moves stop to 100.0, but closes at 99.5 (collapsed below breakeven!)
    candle = create_candle(open_p=101.0, high_p=103.5, low_p=99.0, close_p=99.5, ts_str="2024-01-01 11:00:00")
    dummy_hist = pd.DataFrame([candle])

    daemon.ingest_closed_candle("ABC", candle, dummy_hist)

    # Position must be closed at BREAKEVEN_STOP, not left open
    assert "ABC" not in daemon.active_positions
    assert len(daemon.completed_trades) == 1
    assert daemon.completed_trades[0]["exit_reason"] == "BREAKEVEN_STOP"
    assert daemon.completed_trades[0]["net_pnl"] > 0  # TP1 50% profit retained!


def test_go_no_go_gate_auditor_evaluation():
    """Verify GoNoGoAuditor checks all 5 operational criteria."""
    auditor = GoNoGoAuditor(
        min_expectancy_r=0.20,
        min_profit_factor=1.40,
        max_drawdown_pct=5.0,
        max_slippage_r=0.25,
        min_completed_trades=3
    )

    completed_trades = [
        {"net_pnl": 500.0, "realized_r": 1.5},
        {"net_pnl": 200.0, "realized_r": 0.8},
        {"net_pnl": -150.0, "realized_r": -0.6},
    ]
    variance_records = [
        {"slippage_r": 0.05},
        {"slippage_r": 0.08},
        {"slippage_r": 0.06},
    ]

    report = auditor.evaluate_performance(completed_trades, variance_records, critical_defects_count=0)
    assert all(c.passed for c in report.checks) is True
    assert "GO" in report.overall_verdict

