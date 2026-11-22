"""
Unit tests for T004 US Sector ETF Rotation harness (tools/research_t004_sector_etf.py):
  - Zero-lookahead proofs for ^IRX causal lookup and tercile selection
  - True excess-return Sharpe_tbill invariance to cash periods
  - Stage 1 DEV-only isolation (HOLDOUT 2016+ never read/computed)
"""

import math
import numpy as np
import pandas as pd
import pytest

from research.tools.research_t004_sector_etf import (
    SECTOR_ETFS,
    assign_terciles,
    compute_etf_metrics,
    lookup_causal_tbill_monthly,
    simulate_sector_rotation,
)


def _make_ohlcv(n: int = 1200, start: str = "1998-12-22", seed: int = 1, drift: float = 0.0003) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="B")
    rets = rng.normal(drift, 0.012, size=n)
    close = 50.0 * np.cumprod(1.0 + rets)
    open_ = close * (1.0 + rng.normal(0.0, 0.002, size=n))
    return pd.DataFrame(
        {"Open": open_, "High": np.maximum(open_, close) * 1.01, "Low": np.minimum(open_, close) * 0.99, "Close": close, "Volume": np.full(n, 1_000_000.0)},
        index=idx,
    )


def test_causal_tbill_lookup_no_lookahead():
    idx = pd.date_range("2020-01-02", periods=10, freq="B")
    irx = pd.Series([1.5, 1.6, 1.7, 1.8, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=idx)
    dt = idx[2]  # 1.7% annualized
    r_full = lookup_causal_tbill_monthly(irx, dt)
    r_trunc = lookup_causal_tbill_monthly(irx.loc[:dt], dt)
    expected = (1.0 + 0.017) ** (1.0 / 12.0) - 1.0
    assert r_full == pytest.approx(expected)
    assert r_full == pytest.approx(r_trunc)

    # Negative yield floored at 0
    irx_neg = pd.Series([-0.05], index=[idx[0]])
    assert lookup_causal_tbill_monthly(irx_neg, idx[0]) == 0.0


def test_tercile_assignment_exact_3_3_3():
    scores = {sym: float(i) for i, sym in enumerate(SECTOR_ETFS)}
    t1, t2, t3 = assign_terciles(scores)
    assert len(t1) == 3 and len(t2) == 3 and len(t3) == 3
    assert t1 == SECTOR_ETFS[:3]
    assert t2 == SECTOR_ETFS[3:6]
    assert t3 == SECTOR_ETFS[6:9]


def test_tbill_excess_sharpe_cash_is_zero_excess():
    rf = pd.Series([0.004] * 24)  # ~4.9%/yr T-bill
    yrs = pd.Series([2022] * 12 + [2023] * 12)
    # Pure cash (earns rf every month) -> exact 0 excess Sharpe
    m_cash = compute_etf_metrics(rf, rf, yrs)
    assert m_cash["sharpe_tbill"] == pytest.approx(0.0)

    # Positive excess strategy
    r_strat = rf + pd.Series([0.01, -0.005, 0.015, 0.005] * 6)
    m_s = compute_etf_metrics(r_strat, rf, yrs)
    ex = r_strat - rf
    expected = (ex.mean() / ex.std(ddof=1)) * math.sqrt(12.0)
    assert m_s["sharpe_tbill"] == pytest.approx(expected, abs=1e-3)


def test_stage1_dev_isolation_seals_holdout():
    spy = _make_ohlcv(n=5500, start="1998-12-22", seed=99)
    irx = pd.DataFrame({"Open": 2.0, "High": 2.0, "Low": 2.0, "Close": 2.0, "Volume": 0.0}, index=spy.index)
    sectors = {sym: _make_ohlcv(n=5500, start="1998-12-22", seed=10 + i) for i, sym in enumerate(SECTOR_ETFS)}

    res = simulate_sector_rotation(sectors, spy, irx, max_period="2015-12")
    mdf = res["monthly_df"]
    assert not mdf.empty
    assert (mdf["split"] == "DEV").all()
    assert mdf["period"].max() <= "2015-12"
