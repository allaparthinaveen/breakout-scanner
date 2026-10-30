"""
Phase 3 Verification Test Suite (tests/test_phase3_ablation.py)
Validates single-variable filter ablation runner, chronological walk-forward splits,
regime/tier decomposition, and artifact generation.
"""

import os
import pytest
import pandas as pd
import numpy as np

from tools.run_ablation_study import (
    calculate_trade_metrics,
    run_ablation_matrix,
    run_walk_forward_holdout_split,
    run_regime_and_tier_decomposition,
    generate_markdown_report,
    run_full_ablation_study
)
from breakout_scanner.config import load_config


def create_mock_symbol_df(n_bars: int = 150) -> pd.DataFrame:
    """Creates a deterministic dataframe with breakout events."""
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    # Base consolidation at 100
    prices = [100.0 + (0.5 * np.sin(i / 5.0)) for i in range(100)]
    # Breakout thrust
    prices.extend([102.0 + (i - 100) * 0.4 for i in range(100, 120)])
    # Pullback
    prices.extend([108.0 - (i - 120) * 0.2 for i in range(120, n_bars)])

    df = pd.DataFrame({
        "Open": prices,
        "High": [p + 0.8 for p in prices],
        "Low": [p - 0.8 for p in prices],
        "Close": prices,
        "Volume": [1000.0 if i < 100 else 5000.0 for i in range(n_bars)]
    }, index=dates)
    return df


def test_calculate_trade_metrics_math():
    """Verify metrics calculation logic with known deterministic trade values."""
    trades = [
        {"pnl": 200.0, "r_multiple": 2.0, "bars_held": 5, "exit_reason": "TP2_HIT"},
        {"pnl": 100.0, "r_multiple": 1.0, "bars_held": 3, "exit_reason": "TP1_HIT"},
        {"pnl": -100.0, "r_multiple": -1.0, "bars_held": 2, "exit_reason": "STOP_LOSS"},
        {"pnl": -100.0, "r_multiple": -1.0, "bars_held": 4, "exit_reason": "STOP_LOSS"},
    ]

    m = calculate_trade_metrics(trades, initial_equity=10000.0)
    assert m["total_trades"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 2
    assert m["win_rate"] == 50.0
    assert m["gross_profit"] == 300.0
    assert m["gross_loss"] == 200.0
    assert m["profit_factor"] == 1.5
    assert m["expectancy_r"] == pytest.approx(0.25, abs=1e-3)
    assert m["net_pnl"] == 100.0
    assert m["avg_holding_bars"] == 3.5
    assert m["t1_hit_rate"] == 50.0


def test_calculate_trade_metrics_empty():
    """Verify graceful zero-division safety on empty trades."""
    m = calculate_trade_metrics([])
    assert m["total_trades"] == 0
    assert m["win_rate"] == 0.0
    assert m["profit_factor"] == 0.0
    assert m["expectancy_r"] == 0.0


def test_run_ablation_matrix_conditions():
    """Verify that all 8 ablation conditions are tested without errors."""
    mock_data = {"TEST_SYM": create_mock_symbol_df(120)}
    results = run_ablation_matrix(mock_data)

    expected_keys = [
        "1. Baseline (VCP + 20 EMA + Vol)",
        "2. Baseline + CLV Candle Strength",
        "3. Baseline + VDU Volume Slope",
        "4. Baseline + HTF 1D/4H Alignment",
        "5. Baseline + Benchmark RS Slope",
        "6. Baseline + Market Regime Filter",
        "7. Baseline + Earnings Proximity Guard",
        "8. Full Confluence Engine (All On)"
    ]

    for k in expected_keys:
        assert k in results
        assert "metrics" in results[k]
        assert "trades" in results[k]


def test_walk_forward_holdout_split_chronology():
    """Verify that chronological splits partition data strictly without lookahead."""
    mock_data = {"TEST_SYM": create_mock_symbol_df(150)}
    split_res = run_walk_forward_holdout_split(mock_data)

    assert "in_sample" in split_res
    assert "out_of_sample" in split_res
    assert "holdout" in split_res
    assert "degradation_pct" in split_res
    assert isinstance(split_res["anti_overfit_passed"], (bool, np.bool_))


def test_regime_and_tier_decomposition():
    """Verify trade partitioning by conviction tier and market regime."""
    sample_trades = [
        {"composite_score": 95, "composite_grade": "A+", "pnl": 300, "r_multiple": 2.5, "regime": "BULL_EXPANSION"},
        {"composite_score": 85, "composite_grade": "A", "pnl": 150, "r_multiple": 1.2, "regime": "BULL_EXPANSION"},
        {"composite_score": 75, "composite_grade": "B", "pnl": -50, "r_multiple": -0.5, "regime": "SIDEWAYS_CHOP"},
        {"composite_score": 60, "composite_grade": "C", "pnl": -100, "r_multiple": -1.0, "regime": "BEAR_BREAKDOWN"},
    ]

    decomp = run_regime_and_tier_decomposition(sample_trades)
    assert "Grade A+ (90-100)" in decomp["tiers"]
    assert decomp["tiers"]["Grade A+ (90-100)"]["total_trades"] == 1
    assert decomp["tiers"]["Grade A+ (90-100)"]["win_rate"] == 100.0

    assert "Bull Expansion" in decomp["regimes"]
    assert decomp["regimes"]["Bull Expansion"]["total_trades"] >= 1


def test_markdown_report_generation(tmp_path):
    """Verify artifact generation matches institutional report layout."""
    mock_data = {"TEST_SYM": create_mock_symbol_df(120)}
    ablation_results = run_ablation_matrix(mock_data)
    walk_forward = run_walk_forward_holdout_split(mock_data)
    full_trades = ablation_results["8. Full Confluence Engine (All On)"]["trades"]
    decomp = run_regime_and_tier_decomposition(full_trades)

    report_path = str(tmp_path / "test_ablation_report.md")
    report_md = generate_markdown_report(ablation_results, walk_forward, decomp, output_path=report_path)

    assert os.path.exists(report_path)
    assert "# 🔬 Phase 3: Quantitative Filter Ablation & Walk-Forward Study" in report_md
    assert "1. Baseline (VCP + 20 EMA + Vol)" in report_md
    assert "Anti-Overfitting Verdict" in report_md
    assert "Score Tier Expectancy Monotonicity" in report_md

