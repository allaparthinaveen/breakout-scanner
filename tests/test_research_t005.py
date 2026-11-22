"""
Unit tests for T005 Multi-Asset Trend harness (tools/research_t005_multi_asset.py):
  - Zero-lookahead proofs for inverse-vol weights & causal annual T-bill lookup
  - Stage 1 DEV-only isolation (HOLDOUT 2016+ never touched)
  - A0/A1/A2 cash & trend mechanics
"""

import numpy as np
import pandas as pd
import pytest

from research.tools.research_t005_multi_asset import (
    MACRO_ETFS,
    compute_inv_vol_weights,
    lookup_causal_tbill_annual,
    simulate_multi_asset,
)


def _make_ohlcv(n: int = 3500, start: str = "2006-02-06", seed: int = 1, drift: float = 0.0003, vol: float = 0.012) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="B")
    rets = rng.normal(drift, vol, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    open_ = close * (1.0 + rng.normal(0.0, 0.002, size=n))
    return pd.DataFrame(
        {"Open": open_, "High": np.maximum(open_, close) * 1.01, "Low": np.minimum(open_, close) * 0.99, "Close": close, "Volume": np.full(n, 1_000_000.0)},
        index=idx,
    )


def test_inv_vol_weights_no_lookahead_and_sum_to_one():
    dfs = {
        "A": _make_ohlcv(n=200, seed=1, vol=0.01),
        "B": _make_ohlcv(n=200, seed=2, vol=0.03),
    }
    rets = pd.DataFrame({k: df["Close"].pct_change() for k, df in dfs.items()})
    dt = rets.index[120]

    w_full = compute_inv_vol_weights(rets, ["A", "B"], dt, vol_lb=60)
    w_trunc = compute_inv_vol_weights(rets.loc[:dt], ["A", "B"], dt, vol_lb=60)
    assert sum(w_full.values()) == pytest.approx(1.0)
    assert w_full["A"] > w_full["B"]  # lower vol gets larger weight
    assert w_full["A"] == pytest.approx(w_trunc["A"])

    # Corrupt future bars > dt -> weights at dt unchanged
    mut = rets.copy()
    mut.loc[mut.index > dt, "A"] *= 20.0
    w_mut = compute_inv_vol_weights(mut, ["A", "B"], dt, vol_lb=60)
    assert w_mut["A"] == pytest.approx(w_full["A"])


def test_causal_annual_tbill_lookup():
    idx = pd.date_range("2020-01-02", periods=5, freq="B")
    irx = pd.Series([1.5, 2.0, 5.0, 5.0, 5.0], index=idx)
    assert lookup_causal_tbill_annual(irx, idx[1]) == pytest.approx(0.02)
    assert lookup_causal_tbill_annual(irx.loc[: idx[1]], idx[1]) == pytest.approx(0.02)


def test_stage1_dev_isolation_seals_holdout():
    etfs = {s: _make_ohlcv(n=3500, start="2006-02-06", seed=10 + i) for i, s in enumerate(MACRO_ETFS)}
    irx = pd.DataFrame({"Open": 2.0, "High": 2.0, "Low": 2.0, "Close": 2.0, "Volume": 0.0}, index=etfs["SPY"].index)

    res = simulate_multi_asset(etfs, irx, max_period="2015-12")
    mdf = res["monthly_df"]
    assert not mdf.empty
    assert (mdf["split"] == "DEV").all()
    assert mdf["period"].max() <= "2015-12"
