"""
Phase 6: Hysteresis Stability Buffer & Sector Concentration Manager
===================================================================
Applies institutional buffer mechanics to eliminate daily boundary jitter,
enforces sector diversification caps, logs daily turnover, and saves
reproducible historical universe snapshots.
"""

import os
import json
import datetime
from typing import List, Dict, Any, Tuple, Optional
import numpy as np

from .models import LiquidityMetrics, UniverseSelectionResult


DEFAULT_UNIVERSE_DIR = "data/universe"
DEFAULT_SNAPSHOT_DIR = "data/universe/snapshots"


class HysteresisBuffer:
    """
    Manages universe stability, sector concentration, and historical snapshots.
    """
    def __init__(
        self,
        target_size: int = 30,
        buffer_cutoff: int = 35,
        max_sector_concentration: int = 8,
        universe_dir: str = DEFAULT_UNIVERSE_DIR,
        snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
    ):
        self.target_size = target_size
        self.buffer_cutoff = buffer_cutoff
        self.max_sector_concentration = max_sector_concentration
        self.universe_dir = universe_dir
        self.snapshot_dir = snapshot_dir

        os.makedirs(self.universe_dir, exist_ok=True)
        os.makedirs(self.snapshot_dir, exist_ok=True)

    def load_prior_universe(self, market: str) -> List[str]:
        """Loads previous session's active universe list from disk."""
        path = os.path.join(self.universe_dir, f"{market.lower()}_active_universe.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("selected_symbols", [])
            except Exception:
                pass
        return []

    def load_historical_ranks(self, market: str) -> Dict[str, List[int]]:
        """Loads rank history dictionary (symbol -> list of trailing ranks)."""
        path = os.path.join(self.universe_dir, f"{market.lower()}_rank_history.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_historical_ranks(self, market: str, rank_history: Dict[str, List[int]]):
        """Persists updated rank history to disk."""
        path = os.path.join(self.universe_dir, f"{market.lower()}_rank_history.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rank_history, f, indent=2)
        except Exception:
            pass

    def select_stable_universe(
        self,
        candidates: List[LiquidityMetrics],
        market: str = "US",
        prior_active: Optional[List[str]] = None,
        date_str: Optional[str] = None
    ) -> UniverseSelectionResult:
        """
        Selects top 30 using hysteresis buffer and sector caps.
        """
        if date_str is None:
            date_str = datetime.date.today().isoformat()

        if prior_active is None:
            prior_active = self.load_prior_universe(market)

        prior_set = set(prior_active)

        # 1. Filter qualified candidates and sort strictly by ADV_20 descending
        qualified = [c for c in candidates if c.valid_data and c.adv_20d > 0]
        qualified.sort(key=lambda c: c.adv_20d, reverse=True)

        # Assign raw liquidity rank
        for idx, c in enumerate(qualified, 1):
            c.rank = idx
            c.is_incumbent = (c.symbol in prior_set)

        # 2. Hysteresis Selection Algorithm
        selected: List[LiquidityMetrics] = []
        selected_symbols: List[str] = []
        sector_counts: Dict[str, int] = {}

        # Pass A: Guaranteed top tier (ranks 1 to 25) respecting sector caps
        tier_a_cutoff = min(self.target_size - 5, 25)
        for c in qualified:
            if c.rank <= tier_a_cutoff:
                sec = c.sector
                current_sec_count = sector_counts.get(sec, 0)
                if current_sec_count < self.max_sector_concentration:
                    c.status = "SELECTED" if not c.is_incumbent else "BUFFER_RETAINED"
                    selected.append(c)
                    selected_symbols.append(c.symbol)
                    sector_counts[sec] = current_sec_count + 1
                else:
                    c.status = "SECTOR_CAPPED"

        # Pass B: Hysteresis Buffer Zone (ranks 26 to buffer_cutoff e.g. 35)
        # Give preference to incumbent stocks already in yesterday's Top 30
        if len(selected) < self.target_size:
            buffer_incumbents = [
                c for c in qualified
                if tier_a_cutoff < c.rank <= self.buffer_cutoff
                and c.is_incumbent
                and c.symbol not in selected_symbols
            ]
            for c in buffer_incumbents:
                if len(selected) >= self.target_size:
                    break
                sec = c.sector
                current_sec_count = sector_counts.get(sec, 0)
                if current_sec_count < self.max_sector_concentration:
                    c.status = "BUFFER_RETAINED"
                    selected.append(c)
                    selected_symbols.append(c.symbol)
                    sector_counts[sec] = current_sec_count + 1
                else:
                    c.status = "SECTOR_CAPPED"

        # Pass C: Fill remaining slots with next highest-ranked qualified candidates
        if len(selected) < self.target_size:
            remaining_candidates = [
                c for c in qualified
                if c.symbol not in selected_symbols and c.status != "SECTOR_CAPPED"
            ]
            for c in remaining_candidates:
                if len(selected) >= self.target_size:
                    break
                sec = c.sector
                current_sec_count = sector_counts.get(sec, 0)
                if current_sec_count < self.max_sector_concentration:
                    c.status = "SELECTED"
                    selected.append(c)
                    selected_symbols.append(c.symbol)
                    sector_counts[sec] = current_sec_count + 1
                else:
                    c.status = "SECTOR_CAPPED"

        # Pass D: If slots remain unfilled due to strict sector caps, backfill with top qualified candidates
        if len(selected) < self.target_size:
            backfill_candidates = [c for c in qualified if c.symbol not in selected_symbols]
            for c in backfill_candidates:
                if len(selected) >= self.target_size:
                    break
                c.status = "SELECTED"
                selected.append(c)
                selected_symbols.append(c.symbol)
                sector_counts[c.sector] = sector_counts.get(c.sector, 0) + 1

        # 3. Additions, Removals, and Turnover Metrics
        selected_set = set(selected_symbols)
        retained = [sym for sym in prior_active if sym in selected_set]
        added = [sym for sym in selected_symbols if sym not in prior_set]
        removed = [sym for sym in prior_active if sym not in selected_set]

        turnover_rate = (len(added) / float(self.target_size) * 100.0) if self.target_size > 0 else 0.0

        # Mark dropped incumbents
        for c in candidates:
            if c.symbol in removed:
                c.status = "DROPPED"

        # Sector concentration percentage
        total_selected = len(selected_symbols)
        sector_pcts = {
            sec: round((cnt / total_selected * 100.0), 1)
            for sec, cnt in sector_counts.items()
        } if total_selected > 0 else {}

        # 4. Update trailing rank history
        rank_history = self.load_historical_ranks(market)
        for c in qualified:
            hist = rank_history.get(c.symbol, [])
            hist.append(c.rank)
            rank_history[c.symbol] = hist[-30:]  # Keep last 30 sessions
            c.rank_history = rank_history[c.symbol]
        self.save_historical_ranks(market, rank_history)

        result = UniverseSelectionResult(
            date=date_str,
            market=market.upper(),
            target_size=self.target_size,
            buffer_cutoff=self.buffer_cutoff,
            total_eligible=len(candidates),
            screened_count=len(qualified),
            qualified_count=len(qualified),
            rejected_count=len(candidates) - len(qualified),
            selected_symbols=selected_symbols,
            retained_symbols=retained,
            added_symbols=added,
            removed_symbols=removed,
            sector_distribution=sector_counts,
            sector_concentration_pct=sector_pcts,
            candidates=candidates,
            turnover_rate_pct=round(turnover_rate, 2),
        )

        # 5. Persist Watchlist, Metadata, and Reproducible Historical Snapshot
        self._persist_selection(result)
        return result

    def _persist_selection(self, result: UniverseSelectionResult):
        """Persists active universe, JSON metadata, daily history log, and historical snapshot."""
        m_lower = result.market.lower()

        # 1. Plain text active watchlist
        watchlist_file = os.path.join(self.universe_dir, f"{m_lower}_top30_active.txt")
        try:
            with open(watchlist_file, "w", encoding="utf-8") as f:
                f.write(f"# Dynamic Top {result.target_size} Liquidity Watchlist - {result.market}\n")
                f.write(f"# Date: {result.date} | Turnover: {result.turnover_rate_pct}%\n\n")
                for sec, syms in self._group_by_sector(result).items():
                    f.write(f"## {sec}\n")
                    for sym in syms:
                        f.write(f"{sym}\n")
                    f.write("\n")
            result.watchlist_path = watchlist_file
        except Exception:
            pass

        # 2. Latest active universe JSON
        active_json_file = os.path.join(self.universe_dir, f"{m_lower}_active_universe.json")
        try:
            with open(active_json_file, "w", encoding="utf-8") as f:
                json.dump({
                    "date": result.date,
                    "market": result.market,
                    "selected_symbols": result.selected_symbols,
                    "sector_distribution": result.sector_distribution,
                    "turnover_rate_pct": result.turnover_rate_pct,
                }, f, indent=2)
        except Exception:
            pass

        # 3. Comprehensive metadata JSON
        meta_file = os.path.join(self.universe_dir, f"{m_lower}_metadata_latest.json")
        candidate_meta = [
            {
                "symbol": c.symbol,
                "rank": c.rank,
                "last_close": c.last_close,
                "adv_20d": c.adv_20d,
                "adv_5d": c.adv_5d,
                "liquidity_trend": c.liquidity_trend,
                "liquidity_trend_label": c.liquidity_trend_label,
                "spread_pct": c.spread_pct,
                "gap_risk_flag": c.gap_risk_flag,
                "market_impact_est_bps": c.market_impact_est_bps,
                "sector": c.sector,
                "status": c.status,
                "rejection_reason": c.rejection_reason
            }
            for c in result.candidates if c.status in ("SELECTED", "BUFFER_RETAINED", "DROPPED", "SECTOR_CAPPED")
        ]
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump({
                    "date": result.date,
                    "market": result.market,
                    "target_size": result.target_size,
                    "selected_symbols": result.selected_symbols,
                    "retained_symbols": result.retained_symbols,
                    "added_symbols": result.added_symbols,
                    "removed_symbols": result.removed_symbols,
                    "sector_distribution": result.sector_distribution,
                    "sector_concentration_pct": result.sector_concentration_pct,
                    "candidates": candidate_meta
                }, f, indent=2)
            result.metadata_path = meta_file
        except Exception:
            pass

        # 4. Historical Snapshot for 100% historical reproducibility
        clean_date = result.date.replace(":", "-").replace(" ", "_")
        snapshot_file = os.path.join(self.snapshot_dir, f"universe_{m_lower}_{clean_date}.json")
        try:
            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump({
                    "date": result.date,
                    "market": result.market,
                    "selected_symbols": result.selected_symbols,
                    "sector_distribution": result.sector_distribution,
                    "turnover_rate_pct": result.turnover_rate_pct,
                    "candidates": candidate_meta
                }, f, indent=2)
            result.snapshot_path = snapshot_file
        except Exception:
            pass

        # 5. Universe History JSONL Log
        history_file = os.path.join(self.universe_dir, "universe_history.jsonl")
        try:
            with open(history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "timestamp": result.date,
                    "market": result.market,
                    "selected_count": len(result.selected_symbols),
                    "retained_count": len(result.retained_symbols),
                    "added_count": len(result.added_symbols),
                    "removed_count": len(result.removed_symbols),
                    "turnover_rate_pct": result.turnover_rate_pct,
                    "top_sectors": list(result.sector_distribution.keys())[:3]
                }) + "\n")
        except Exception:
            pass

    def _group_by_sector(self, result: UniverseSelectionResult) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        lookup = {c.symbol: c.sector for c in result.candidates}
        for sym in result.selected_symbols:
            sec = lookup.get(sym, "Broad Market")
            groups.setdefault(sec, []).append(sym)
        return groups
