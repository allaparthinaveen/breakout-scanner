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
        self.report_dir = u_cfg.get("report_dir", "reports")

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

        # Step 5: Automatically generate Markdown Audit Report
        try:
            report_out = os.path.join(self.report_dir, "universe_liquidity_report.md")
            self.generate_markdown_report(result, output_path=report_out)
        except Exception:
            pass

        return result

    def generate_markdown_report(
        self,
        result: UniverseSelectionResult,
        output_path: str = "reports/universe_liquidity_report.md"
    ) -> str:
        """
        Generates a comprehensive Markdown audit report for the screened universe.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        lines = [
            f"# 🏛️ Layer 1 Universe Intelligence: Dynamic Liquidity Audit Report",
            f"",
            f"> **Market:** `{result.market.upper()}` | **Date:** `{result.date}`  ",
            f"> **Eligible Universe:** {result.total_eligible} candidates | **Qualified:** {result.qualified_count} | **Rejected:** {result.rejected_count}  ",
            f"> **Stability Metrics:** Target: {result.target_size} | Buffer Cutoff: {result.buffer_cutoff} | Retained: {len(result.retained_symbols)} | Additions: {len(result.added_symbols)} | Drops: {len(result.removed_symbols)} | Turnover: {result.turnover_rate_pct:.1f}%",
            f"",
            f"---",
            f"",
            f"## 1. Selected Top {len(result.selected_symbols)} Liquid Constituents",
            f"",
            f"| Rank | Symbol | Sector | Last Close | 20D ADV | 20D Avg Vol | Trend | Spread % | Gap Risk | Impact (bps) | Status |",
            f"| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
        ]

        c_map = {c.symbol: c for c in result.candidates}
        curr_symbol = "₹" if result.market.upper() == "NSE" else "$"

        for sym in result.selected_symbols:
            c = c_map.get(sym)
            if not c:
                continue
            adv_str = f"{curr_symbol}{c.adv_20d:,.0f}"
            vol_str = f"{c.volume_20d_avg:,.0f}"
            gap_flag = "⚠️ YES" if c.gap_risk_flag else "NO"
            lines.append(
                f"| {c.rank} | **{c.symbol}** | {c.sector} | {curr_symbol}{c.last_close:.2f} | {adv_str} | {vol_str} | {c.liquidity_trend_label} ({c.liquidity_trend:.2f}x) | {c.spread_pct:.2f}% | {gap_flag} | {c.market_impact_est_bps:.1f} bps | `{c.status}` |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 2. Sector Concentration & Diversification",
            f"",
            f"| Sector | Count | Allocation % | Max Cap ({self.max_sector_concentration}) | Concentration Status |",
            f"| :--- | :---: | :---: | :---: | :--- |",
        ])

        for sec, count in sorted(result.sector_distribution.items(), key=lambda x: x[1], reverse=True):
            pct = result.sector_concentration_pct.get(sec, 0.0)
            status = "⚠️ AT CAP" if count >= self.max_sector_concentration else "OK"
            lines.append(f"| **{sec}** | {count} | {pct:.1f}% | {self.max_sector_concentration} | {status} |")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 3. Boundary & Churn Audit",
            f"",
            f"- **🟢 New Inclusions ({len(result.added_symbols)}):** {', '.join(result.added_symbols) if result.added_symbols else 'None'}",
            f"- **🛡️ Buffer Retentions ({len(result.retained_symbols)}):** {', '.join(result.retained_symbols) if result.retained_symbols else 'None'}",
            f"- **🔴 Dropped Constituents ({len(result.removed_symbols)}):** {', '.join(result.removed_symbols) if result.removed_symbols else 'None'}",
            f"",
            f"---",
            f"",
            f"## 4. Reproducibility & Audit Paths",
            f"",
            f"- **Active Watchlist File:** `{result.watchlist_path}`",
            f"- **Latest Metadata File:** `{result.metadata_path}`",
            f"- **Historical Snapshot:** `{result.snapshot_path}`",
        ])

        content = "\n".join(lines) + "\n"
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            result.report_path = output_path
        except Exception:
            pass

        return content

