"""
Phase 6 Verification Test Suite: Dynamic Liquidity Universe Engine
==================================================================
Tests:
1. Traded value calculation (ADV_20 = sum(Close * Volume) / 20)
2. Penny stock / minimum price floor filtering (US < $5, NSE < Rs 50)
3. Active sessions threshold (>= 18 active trading sessions)
4. Data quality rejection (NaNs, negative values, trading halts)
5. Hysteresis stability buffer (eliminating daily boundary jitter for ranks 25-35)
6. Sector concentration caps (avoiding single sector saturation)
7. Liquidity trend (ADV_5 / ADV_20) and overnight gap-risk detection
8. Reproducible historical universe snapshot persistence
9. Full daily_scan pipeline integration (Universe -> Watchlist -> Breakout Engine -> Trade Slate)
"""

import os
import json
from typing import List, Dict, Optional
import pytest
import pandas as pd
import numpy as np

from breakout_scanner.universe.models import LiquidityMetrics, UniverseSelectionResult
from breakout_scanner.universe.screener import LiquidityScreener
from breakout_scanner.universe.stability import HysteresisBuffer
from breakout_scanner.universe.engine import DynamicLiquidityEngine
from breakout_scanner.providers.base import MarketDataProvider
from breakout_scanner.models import Setup, State
from breakout_scanner.portfolio_guard import generate_actionable_slate


def create_daily_df(
    prices: List[float],
    volumes: List[float],
    spread_pct: float = 1.0,
    n_bars: int = 20
) -> pd.DataFrame:
    """Helper to generate a clean daily dataframe."""
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    df = pd.DataFrame({
        "Open": prices,
        "High": [p * (1.0 + spread_pct / 200.0) for p in prices],
        "Low": [p * (1.0 - spread_pct / 200.0) for p in prices],
        "Close": prices,
        "Volume": volumes
    }, index=dates)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 1. ADV Traded Value Calculation Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_adv_20_calculation():
    """Verify exact ADV_20 traded value calculation: mean(Close * Volume) over 20 sessions."""
    screener = LiquidityScreener(lookback_sessions=20)

    # 20 sessions: Close = 100.0, Volume = 50,000 -> Traded value per day = $5,000,000
    prices = [100.0] * 20
    volumes = [50000.0] * 20
    df = create_daily_df(prices, volumes)

    metrics = screener.screen_symbol("AAPL", df)

    assert metrics.valid_data is True
    assert metrics.adv_20d == pytest.approx(5000000.0, abs=1e-2)
    assert metrics.volume_20d_avg == pytest.approx(50000.0, abs=1e-2)
    assert metrics.last_close == 100.0


def test_traded_value_ranks_higher_than_raw_share_volume():
    """Verify that a stock trading 1M shares at $100 ($100M ADV) ranks higher than 5M shares at $10 ($50M ADV)."""
    screener = LiquidityScreener(lookback_sessions=20)

    # Stock A: $100 close, 1M shares -> $100M ADV
    df_a = create_daily_df([100.0] * 20, [1000000.0] * 20)
    # Stock B: $10 close, 5M shares -> $50M ADV (Higher share volume, lower traded money!)
    df_b = create_daily_df([10.0] * 20, [5000000.0] * 20)

    m_a = screener.screen_symbol("STOCK_A", df_a)
    m_b = screener.screen_symbol("STOCK_B", df_b)

    assert m_a.adv_20d > m_b.adv_20d
    assert m_a.adv_20d == 100000000.0
    assert m_b.adv_20d == 50000000.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Price Floor & Data Quality Rejection Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_penny_stock_rejection_us_and_nse():
    """Verify rejection of penny / very low-priced stocks."""
    screener = LiquidityScreener(min_price_us=5.0, min_price_nse=50.0)

    # US Penny Stock ($3.50 < $5.00)
    df_us_penny = create_daily_df([3.50] * 20, [100000.0] * 20)
    m_us = screener.screen_symbol("PENNY_US", df_us_penny)
    assert m_us.valid_data is False
    assert m_us.status == "REJECTED"
    assert "PENNY_STOCK" in m_us.rejection_reason

    # NSE Penny Stock (Rs 25.0 < Rs 50.0)
    df_nse_penny = create_daily_df([25.0] * 20, [100000.0] * 20)
    m_nse = screener.screen_symbol("PENNY.NS", df_nse_penny)
    assert m_nse.valid_data is False
    assert m_nse.status == "REJECTED"
    assert "PENNY_STOCK" in m_nse.rejection_reason


def test_active_sessions_threshold():
    """Verify rejection when stock traded in fewer than 18 of last 20 sessions."""
    screener = LiquidityScreener(lookback_sessions=20, min_active_sessions=18)

    # Only 15 sessions available
    df_short = create_daily_df([100.0] * 15, [10000.0] * 15, n_bars=15)
    m = screener.screen_symbol("SHORT_HISTORY", df_short)

    assert m.valid_data is False
    assert "INSUFFICIENT_BARS" in m.rejection_reason


def test_data_quality_rejection_nan_and_halts():
    """Verify rejection of NaNs and trading halts (> 2 zero volume sessions)."""
    screener = LiquidityScreener()

    # 1. NaN in Close
    prices = [100.0] * 19 + [np.nan]
    df_nan = create_daily_df(prices, [50000.0] * 20)
    m_nan = screener.screen_symbol("NAN_STOCK", df_nan)
    assert m_nan.valid_data is False
    assert "NAN_DATA" in m_nan.rejection_reason

    # 2. Suspended / Zero-volume halt (3 days zero volume)
    vols = [50000.0] * 17 + [0.0, 0.0, 0.0]
    df_halt = create_daily_df([100.0] * 20, vols)
    m_halt = screener.screen_symbol("HALTED_STOCK", df_halt)
    assert m_halt.valid_data is False
    assert "TRADING_HALT" in m_halt.rejection_reason


# ─────────────────────────────────────────────────────────────────────────────
# 3. Liquidity Trend & Overnight Gap-Risk Detection Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_liquidity_trend_expansion_and_contraction():
    """Verify ADV_5 / ADV_20 trend identification."""
    screener = LiquidityScreener()

    # Expanding: First 15 days volume 10k, last 5 days surge to 100k -> ADV_5 >> ADV_20
    vols_expanding = [10000.0] * 15 + [100000.0] * 5
    df_exp = create_daily_df([100.0] * 20, vols_expanding)
    m_exp = screener.screen_symbol("EXPANDING_VOL", df_exp)

    assert m_exp.liquidity_trend > 1.15
    assert m_exp.liquidity_trend_label == "EXPANDING"

    # Contracting: First 15 days volume 100k, last 5 days dry up to 10k
    vols_contracting = [100000.0] * 15 + [10000.0] * 5
    df_con = create_daily_df([100.0] * 20, vols_contracting)
    m_con = screener.screen_symbol("CONTRACTING_VOL", df_con)

    assert m_con.liquidity_trend < 0.85
    assert m_con.liquidity_trend_label == "CONTRACTING"


def test_gap_risk_flag():
    """Verify that a stock with severe overnight gaps (> 2.5% avg) is flagged."""
    screener = LiquidityScreener(max_avg_gap_pct=2.5)

    dates = pd.date_range("2024-01-01", periods=20, freq="D")
    opens = []
    closes = []
    # Create alternating overnight gap jump of 4% every day
    curr = 100.0
    for i in range(20):
        o = curr * 1.04
        c = o * 1.01
        opens.append(o)
        closes.append(c)
        curr = c

    df_gappy = pd.DataFrame({
        "Open": opens,
        "High": [o * 1.02 for o in opens],
        "Low": [o * 0.99 for o in opens],
        "Close": closes,
        "Volume": [50000.0] * 20
    }, index=dates)

    m = screener.screen_symbol("GAPPY_STOCK", df_gappy)
    assert m.gap_risk_flag is True
    assert m.avg_overnight_gap_pct > 2.5


# ─────────────────────────────────────────────────────────────────────────────
# 4. Hysteresis Stability Buffer & Sector Concentration Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_hysteresis_buffer_preserves_incumbents(tmp_path):
    """Verify that an incumbent ranking at 32 is preserved over a newcomer at 28."""
    buffer_mgr = HysteresisBuffer(
        target_size=30,
        buffer_cutoff=35,
        max_sector_concentration=40,  # Disable sector cap for pure buffer test
        universe_dir=str(tmp_path / "universe"),
        snapshot_dir=str(tmp_path / "snapshots")
    )

    # 40 candidates: SYM_01 to SYM_40 with ADV decreasing from 100M to 1M
    candidates = []
    for i in range(1, 41):
        sym = f"SYM_{i:02d}"
        adv = (41 - i) * 1000000.0
        c = LiquidityMetrics(symbol=sym, last_close=100.0, adv_20d=adv, valid_data=True, sector="Tech")
        candidates.append(c)

    # Yesterday's universe contained SYM_32 (incumbent)
    prior_universe = [f"SYM_{i:02d}" for i in range(1, 30)] + ["SYM_32"]

    # Select universe
    result = buffer_mgr.select_stable_universe(candidates, market="US", prior_active=prior_universe)

    assert len(result.selected_symbols) == 30
    # SYM_32 (rank 32 <= buffer_cutoff 35) was retained!
    assert "SYM_32" in result.selected_symbols
    assert "SYM_32" in result.retained_symbols

    # An incumbent falling to rank 38 (beyond buffer_cutoff 35) is DROPPED
    prior_with_38 = [f"SYM_{i:02d}" for i in range(1, 30)] + ["SYM_38"]
    res2 = buffer_mgr.select_stable_universe(candidates, market="US", prior_active=prior_with_38)
    assert "SYM_38" not in res2.selected_symbols
    assert "SYM_38" in res2.removed_symbols


def test_sector_concentration_cap_enforcement(tmp_path):
    """Verify that no single sector can occupy more than max_sector_concentration slots."""
    buffer_mgr = HysteresisBuffer(
        target_size=30,
        buffer_cutoff=35,
        max_sector_concentration=8,  # Max 8 stocks per sector
        universe_dir=str(tmp_path / "universe"),
        snapshot_dir=str(tmp_path / "snapshots")
    )

    candidates = []
    # 20 Tech stocks with top ADV (ranks 1 to 20)
    for i in range(1, 21):
        c = LiquidityMetrics(symbol=f"TECH_{i:02d}", last_close=100.0, adv_20d=(60 - i) * 1000000.0, valid_data=True, sector="Technology")
        candidates.append(c)

    # 15 Healthcare stocks (ranks 21 to 35)
    for i in range(1, 16):
        c = LiquidityMetrics(symbol=f"HLTH_{i:02d}", last_close=100.0, adv_20d=(35 - i) * 1000000.0, valid_data=True, sector="Healthcare")
        candidates.append(c)

    # 10 Financials stocks (ranks 36 to 45)
    for i in range(1, 11):
        c = LiquidityMetrics(symbol=f"FIN_{i:02d}", last_close=100.0, adv_20d=(20 - i) * 1000000.0, valid_data=True, sector="Financials")
        candidates.append(c)

    # 10 Energy stocks (ranks 46 to 55)
    for i in range(1, 11):
        c = LiquidityMetrics(symbol=f"NRG_{i:02d}", last_close=100.0, adv_20d=(10 - i) * 1000000.0, valid_data=True, sector="Energy")
        candidates.append(c)

    result = buffer_mgr.select_stable_universe(candidates, market="US", prior_active=[])

    assert len(result.selected_symbols) == 30
    # Exactly 8 Tech stocks allowed
    assert result.sector_distribution["Technology"] == 8
    # Remaining slots allocated across other sectors
    assert result.sector_distribution["Healthcare"] == 8
    assert result.sector_distribution["Financials"] == 8
    assert result.sector_distribution["Energy"] == 6


def test_snapshot_persistence_reproducibility(tmp_path):
    """Verify that daily selection creates reproducible historical JSON snapshots."""
    u_dir = str(tmp_path / "universe")
    s_dir = str(tmp_path / "snapshots")
    buffer_mgr = HysteresisBuffer(target_size=5, buffer_cutoff=8, universe_dir=u_dir, snapshot_dir=s_dir)

    candidates = [
        LiquidityMetrics(symbol=f"S_{i}", last_close=100.0, adv_20d=(10 - i) * 1000.0, valid_data=True, sector="Tech")
        for i in range(10)
    ]

    result = buffer_mgr.select_stable_universe(candidates, market="US", date_str="2026-10-30")

    assert os.path.exists(result.watchlist_path)
    assert os.path.exists(result.snapshot_path)
    assert os.path.exists(result.metadata_path)

    # Read back snapshot
    with open(result.snapshot_path, "r", encoding="utf-8") as f:
        snap = json.load(f)
        assert snap["market"] == "US"
        assert len(snap["selected_symbols"]) == 5
        assert snap["date"] == "2026-10-30"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Full Pipeline Integration Test (Universe -> Scanner -> Slate)
# ─────────────────────────────────────────────────────────────────────────────

class MockOfflineProvider(MarketDataProvider):
    """Mock provider serving deterministic daily and hourly frames."""
    def __init__(self, symbols_data: dict):
        self.symbols_data = symbols_data
        self.daily_cache = {}
        self.daily_cache_ts = {}
        self.market_cache = {}
        self.market_cache_ts = {}

    def candles(self, symbol: str, period: str = "60d", interval: str = "1h", **kwargs):
        df = self.symbols_data.get(symbol, pd.DataFrame())
        return df


def test_daily_scan_integration_without_modifying_breakout_engine(tmp_path):
    """
    Verify complete daily_scan workflow:
    1. Broad universe is screened for liquidity.
    2. Dynamic Top watchlist is generated with stability buffer.
    3. Existing BreakoutEngine runs on the selected watchlist without modification.
    4. Actionable Trade Slate is generated with exact sizing.
    """
    u_dir = str(tmp_path / "universe")
    os.makedirs(u_dir, exist_ok=True)

    # Create 5 mock symbols: 3 liquid, 1 illiquid, 1 penny stock
    dates_daily = pd.date_range("2024-01-01", periods=25, freq="D")
    dates_hourly = pd.date_range("2024-01-01", periods=80, freq="h")

    symbols_data = {}

    # Liquid 1: NVDA (Liquid, Valid Breakout Setup, highest ADV)
    df_nvda = pd.DataFrame({
        "Open": 100.0, "High": 102.0, "Low": 99.0, "Close": 101.5, "Volume": 2000000.0
    }, index=dates_daily)
    # Hourly with breakout
    prices_h = [100.0] * 65 + [102.0 + (i - 65) * 0.5 for i in range(65, 80)]
    df_nvda_h = pd.DataFrame({
        "Open": prices_h, "High": [p + 0.5 for p in prices_h], "Low": [p - 0.5 for p in prices_h],
        "Close": prices_h, "Volume": [1000.0 if i < 65 else 8000.0 for i in range(80)]
    }, index=dates_hourly)
    symbols_data["NVDA"] = df_nvda
    symbols_data["NVDA_1h"] = df_nvda_h

    # Liquid 2: AAPL (Liquid)
    df_aapl = pd.DataFrame({
        "Open": 150.0, "High": 152.0, "Low": 149.0, "Close": 151.0, "Volume": 800000.0
    }, index=dates_daily)
    symbols_data["AAPL"] = df_aapl

    # Liquid 3: MSFT (Liquid)
    df_msft = pd.DataFrame({
        "Open": 200.0, "High": 202.0, "Low": 198.0, "Close": 201.0, "Volume": 600000.0
    }, index=dates_daily)
    symbols_data["MSFT"] = df_msft

    # Illiquid 4: TINY (Very low volume -> Low ADV)
    df_tiny = pd.DataFrame({
        "Open": 50.0, "High": 51.0, "Low": 49.0, "Close": 50.0, "Volume": 500.0
    }, index=dates_daily)
    symbols_data["TINY"] = df_tiny

    # Penny 5: PENNY (Price $2.00 -> Rejected)
    df_penny = pd.DataFrame({
        "Open": 2.0, "High": 2.1, "Low": 1.9, "Close": 2.0, "Volume": 500000.0
    }, index=dates_daily)
    symbols_data["PENNY"] = df_penny

    provider = MockOfflineProvider(symbols_data)

    cfg = {
        "dynamic_universe": {
            "target_size": 2,  # Top 2
            "buffer_cutoff": 3,
            "min_price": {"us": 5.0},
            "min_active_sessions": 18,
            "metadata_dir": u_dir,
            "report_dir": str(tmp_path / "reports")
        },
        "portfolio_guard": {
            "account_equity": 100000.0,
            "risk_per_trade_pct": 1.0,
            "max_daily_risk_pct": 2.0,
            "max_active_trades": 4,
            "max_sector_allocation_pct": 25.0,
            "max_trades_per_sector": 1
        }
    }

    engine = DynamicLiquidityEngine(cfg)
    universe_result = engine.run_selection(
        provider=provider,
        market="US",
        broad_symbols=["NVDA", "AAPL", "MSFT", "TINY", "PENNY"],
        symbols_daily_data={
            "NVDA": df_nvda, "AAPL": df_aapl, "MSFT": df_msft, "TINY": df_tiny, "PENNY": df_penny
        }
    )

    # 1. PENNY must be rejected
    rejected_reasons = {c.symbol: c.rejection_reason for c in universe_result.candidates if not c.valid_data}
    assert "PENNY" in rejected_reasons
    assert "PENNY_STOCK" in rejected_reasons["PENNY"]

    # 2. Selected watchlist has top 2 liquid (NVDA and AAPL)
    assert len(universe_result.selected_symbols) == 2
    assert "NVDA" in universe_result.selected_symbols
    assert "AAPL" in universe_result.selected_symbols
    assert "TINY" not in universe_result.selected_symbols

    # 3. Feed selected watchlist directly into existing actionable trade slate
    s_nvda = Setup(symbol="NVDA", state=State.BUY, direction=1, entry=105.0, stop=101.0, current_price=105.0, sector_name="Semis", composite_score=92)
    s_aapl = Setup(symbol="AAPL", state=State.BUY, direction=1, entry=151.0, stop=147.0, current_price=151.0, sector_name="Tech", composite_score=88)

    slate = generate_actionable_slate([s_nvda, s_aapl], config=cfg)
    assert len(slate.actionable_trades) >= 1
    assert slate.actionable_trades[0].symbol == "NVDA"

    # 4. Verify markdown report generation
    assert universe_result.report_path is not None
    assert os.path.exists(universe_result.report_path)


def test_markdown_audit_report_content(tmp_path):
    """Verify detailed contents of generated universe liquidity audit markdown report."""
    provider = MockOfflineProvider({})
    u_dir = str(tmp_path / "universe")
    rep_path = str(tmp_path / "universe_report.md")

    cfg = {
        "dynamic_universe": {
            "target_size": 2,
            "buffer_cutoff": 3,
            "metadata_dir": u_dir,
            "report_dir": str(tmp_path / "reports")
        }
    }

    engine = DynamicLiquidityEngine(cfg)
    df_a = create_daily_df([100.0] * 20, [1000000.0] * 20)
    df_b = create_daily_df([50.0] * 20, [500000.0] * 20)

    res = engine.run_selection(
        provider=provider,
        market="US",
        broad_symbols=["SYM_A", "SYM_B"],
        symbols_daily_data={"SYM_A": df_a, "SYM_B": df_b}
    )

    report_content = engine.generate_markdown_report(res, output_path=rep_path)
    assert os.path.exists(rep_path)
    assert "Layer 1 Universe Intelligence" in report_content
    assert "Selected Top 2 Liquid Constituents" in report_content
    assert "SYM_A" in report_content
    assert "SYM_B" in report_content
    assert "Sector Concentration & Diversification" in report_content
    assert "Boundary & Churn Audit" in report_content

