"""
Phase 6: Dynamic Liquidity Universe Engine Orchestrator
=======================================================
Orchestrates the entire daily universe generation lifecycle:
1. Broad stock universe retrieval (All eligible common stocks)
2. Quantitative liquidity & data-quality screening (ADV_20, price floor, spread, gap-risk)
3. Hysteresis stability buffering & sector concentration enforcement
4. Automated active watchlist and reproducible snapshot generation
"""

import os
from typing import List, Dict, Optional, Any
import pandas as pd

from .models import LiquidityMetrics, UniverseSelectionResult
from .screener import LiquidityScreener
from .stability import HysteresisBuffer
from .universe_provider import EligibleUniverseProvider
from ..providers.base import MarketDataProvider


class DynamicLiquidityEngine:
    """
    Institutional Dynamic Liquidity Universe Generator.
    """
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        cfg = cfg or {}
        u_cfg = cfg.get("dynamic_universe", {})

        self.target_size = int(u_cfg.get("target_size", 30))
        self.buffer_cutoff = int(u_cfg.get("buffer_cutoff", 35))
        self.max_sector_concentration = int(u_cfg.get("max_sector_concentration", 8))
        self.lookback_sessions = int(u_cfg.get("lookback_days", 20))
        self.min_active_sessions = int(u_cfg.get("min_active_sessions", 18))
        self.max_spread_pct = float(u_cfg.get("max_spread_pct", 6.0))
        self.max_avg_gap_pct = float(u_cfg.get("max_avg_gap_pct", 2.5))

        min_price_cfg = u_cfg.get("min_price", {})
        min_p_us = float(min_price_cfg.get("us", 5.0)) if isinstance(min_price_cfg, dict) else 5.0
        min_p_nse = float(min_price_cfg.get("nse", 50.0)) if isinstance(min_price_cfg, dict) else 50.0

        universe_dir = u_cfg.get("metadata_dir", "data/universe")
        snapshot_dir = os.path.join(universe_dir, "snapshots")

        self.screener = LiquidityScreener(
            lookback_sessions=self.lookback_sessions,
            min_active_sessions=self.min_active_sessions,
            min_price_us=min_p_us,
            min_price_nse=min_p_nse,
            max_spread_pct=self.max_spread_pct,
            max_avg_gap_pct=self.max_avg_gap_pct,
        )

        self.buffer_manager = HysteresisBuffer(
            target_size=self.target_size,
            buffer_cutoff=self.buffer_cutoff,
            max_sector_concentration=self.max_sector_concentration,
            universe_dir=universe_dir,
            snapshot_dir=snapshot_dir,
        )

        self.universe_provider = EligibleUniverseProvider(config_dir="config")

    def run_selection(
        self,
        provider: MarketDataProvider,
        market: str = "US",
        broad_symbols: Optional[List[str]] = None,
        custom_watchlist: Optional[str] = None,
        prior_active: Optional[List[str]] = None,
        symbols_daily_data: Optional[Dict[str, pd.DataFrame]] = None,
        date_str: Optional[str] = None
    ) -> UniverseSelectionResult:
        """
        Executes end-to-end daily universe selection pipeline.
        """
        # Step 1: Broad eligible stock universe
        if broad_symbols is None:
            broad_symbols = self.universe_provider.get_broad_universe(
                market=market, custom_watchlist=custom_watchlist
            )

        print(f"Screening {len(broad_symbols)} eligible symbols for {market.upper()} Liquidity Universe...")

        # Step 2: Fetch daily data if not pre-supplied
        if symbols_daily_data is None:
            symbols_daily_data = self.universe_provider.fetch_daily_data(
                broad_symbols, provider, lookback_days="60d"
            )

        # Step 3: Liquidity & data-quality screening
        candidates: List[LiquidityMetrics] = []
        for sym in broad_symbols:
            df = symbols_daily_data.get(sym)
            if df is not None and not df.empty:
                m = self.screener.screen_symbol(sym, df)
            else:
                m = LiquidityMetrics(
                    symbol=sym,
                    valid_data=False,
                    status="REJECTED",
                    rejection_reason="DATA_UNAVAILABLE"
                )
            candidates.append(m)

        # Step 4: Apply hysteresis stability buffer and sector concentration caps
        result = self.buffer_manager.select_stable_universe(
            candidates=candidates,
            market=market,
            prior_active=prior_active,
            date_str=date_str
        )

        return result

