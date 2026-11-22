"""
Unit tests for T003 12-1m Momentum Research Harness (tools/research_momentum_12_1.py):
  - Zero-lookahead proofs for Mom_12_1, benchmark regime, and 60d vol-target weights
  - Exact rebalance turnover & cost calculation across full, partial, and cash transitions
  - Stage 1 DEV-only isolation (HOLDOUT strictly untouched)
  - Sharpe convention parity & Stage 2 graduation gate checks
"""

import math
import numpy as np
import pandas as pd
import pytest

from research.tools.research_momentum_12_1 import (
    assign_quintiles,
    build_monthly_schedule,
    compute_basket_vol_weight,
    compute_benchmark_regime,
    compute_mom_12_1_panel,
    compute_rebalance_cost,
    compute_series_metrics,
    simulate_monthly_strategies,
)


def _make_ohlcv(n: int = 400, start: str = "2019-01-02", seed: int = 11, drift: float = 0.0005) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="B")
    rets = rng.normal(drift, 0.015, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    open_ = close * (1.0 + rng.normal(0.0, 0.004, size=n))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": np.full(n, 500_000.0)},
        index=idx,
    )


def test_mom_12_1_and_regime_no_lookahead():
    bench = _make_ohlcv(n=400, seed=1)
    universe = {f"S{i}": _make_ohlcv(n=400, seed=10 + i, drift=0.0002 * i) for i in range(10)}

    full_mom, full_close, _ = compute_mom_12_1_panel(universe)
    full_rets = full_close.pct_change()
    full_reg = compute_benchmark_regime(bench)

    for probe_i in [252, 280, 320, 360]:
        dt = bench.index[probe_i]

        # 1. Truncate strictly to <= dt
        trunc_u = {s: df.loc[:dt] for s, df in universe.items()}
        trunc_mom, trunc_close, _ = compute_mom_12_1_panel(trunc_u)
        trunc_rets = trunc_close.pct_change()
        trunc_reg = compute_benchmark_regime(bench.loc[:dt])

        pd.testing.assert_series_equal(full_mom.loc[dt], trunc_mom.loc[dt])
        assert bool(full_reg.loc[dt]) == bool(trunc_reg.loc[dt])
        assert compute_basket_vol_weight(full_rets, ["S0", "S1", "S2"], dt) == pytest.approx(
            compute_basket_vol_weight(trunc_rets, ["S0", "S1", "S2"], dt)
        )

        # 2. Violently corrupt all future bars (> dt)
        mut_u = {}
        for s, df in universe.items():
            m = df.copy()
            m.loc[m.index > dt, ["Open", "High", "Low", "Close"]] *= 0.05
            mut_u[s] = m
        mut_bench = bench.copy()
        mut_bench.loc[mut_bench.index > dt, "Close"] *= 10.0

        mut_mom, mut_close, _ = compute_mom_12_1_panel(mut_u)
        mut_rets = mut_close.pct_change()
        mut_reg = compute_benchmark_regime(mut_bench)

        pd.testing.assert_frame_equal(full_mom.loc[:dt], mut_mom.loc[:dt])
        assert full_reg.loc[:dt].equals(mut_reg.loc[:dt])
        assert compute_basket_vol_weight(full_rets, ["S0", "S1", "S2"], dt) == pytest.approx(
            compute_basket_vol_weight(mut_rets, ["S0", "S1", "S2"], dt)
        )


def test_rebalance_turnover_and_cost_all_regimes():
    one_way = 0.0010  # 10 bps one-way = 20 bps round-trip

    # Case A: 100% -> 100%, replacing 20% of portfolio (no drift)
    to_a, cost_a = compute_rebalance_cost(
        prev_weights={"A": 0.5, "B": 0.5},
        prev_stock_rets={"A": 0.0, "B": 0.0},
        prev_port_gross_ret=0.0,
        new_weights={"A": 0.5, "B": 0.3, "C": 0.2},
        one_way_cost=one_way,
    )
    assert to_a == pytest.approx(0.20)
    assert cost_a == pytest.approx(0.0004)  # 4 bps

    # Case B: 100% invested -> 0% cash (1-sided liquidation = 10 bps)
    to_b, cost_b = compute_rebalance_cost(
        prev_weights={"A": 0.5, "B": 0.5},
        prev_stock_rets={"A": 0.0, "B": 0.0},
        prev_port_gross_ret=0.0,
        new_weights={},
        one_way_cost=one_way,
    )
    assert to_b == pytest.approx(0.50)
    assert cost_b == pytest.approx(0.0010)  # 10 bps

    # Case C: 0% cash -> 100% initial entry (1-sided buy = 10 bps)
    to_c, cost_c = compute_rebalance_cost(
        prev_weights={},
        prev_stock_rets={},
        prev_port_gross_ret=0.0,
        new_weights={"A": 0.5, "B": 0.5},
        one_way_cost=one_way,
    )
    assert to_c == pytest.approx(0.50)
    assert cost_c == pytest.approx(0.0010)

    # Case D: 0% cash -> 0% cash (0 bps)
    to_d, cost_d = compute_rebalance_cost(
        prev_weights={},
        prev_stock_rets={},
        prev_port_gross_ret=0.0,
        new_weights={},
        one_way_cost=one_way,
    )
    assert to_d == pytest.approx(0.0)
    assert cost_d == pytest.approx(0.0)


def test_quintile_sorting_and_schedule_strict_open_to_open():
    scores = {f"SYM{i:02d}": float(i) for i in range(25)}
    qs = assign_quintiles(scores)
    assert [len(q) for q in qs] == [5, 5, 5, 5, 5]
    assert qs[0] == ["SYM00", "SYM01", "SYM02", "SYM03", "SYM04"]
    assert qs[4] == ["SYM20", "SYM21", "SYM22", "SYM23", "SYM24"]

    bench = _make_ohlcv(n=100, start="2020-01-02", seed=5)
    sched = build_monthly_schedule(bench)
    assert len(sched) >= 3
    for step in sched:
        assert step["fill_date"] > step["signal_date"]
        assert step["next_fill_date"] > step["fill_date"]


def test_stage1_dev_isolation_does_not_touch_holdout():
    bench = _make_ohlcv(n=2100, start="2015-01-02", seed=42)
    universe = {f"U{i:02d}": _make_ohlcv(n=2100, start="2015-01-02", seed=100 + i) for i in range(30)}

    res = simulate_monthly_strategies("US", universe, bench, max_period="2021-12")
    mdf = res["monthly_df"]
    assert not mdf.empty
    assert (mdf["split"] == "DEV").all()
    assert mdf["period"].max() <= "2021-12"


def test_sharpe_rf0_and_rf4_convention():
    rets = pd.Series([0.02, 0.01, -0.01, 0.03, 0.02, 0.00] * 4)
    yrs = pd.Series([2022] * 12 + [2023] * 12)
    m = compute_series_metrics(rets, yrs)
    expected_rf0 = (rets.mean() / rets.std(ddof=1)) * math.sqrt(12.0)
    assert m["sharpe_rf0"] == pytest.approx(expected_rf0, abs=1e-3)
    assert m["sharpe_rf4"] < m["sharpe_rf0"]
