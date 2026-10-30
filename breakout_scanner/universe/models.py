"""
Dynamic Liquidity Universe Data Models
======================================
Defines structured records for liquidity screening, ADV metrics, data quality
validation, hysteresis stability buffering, liquidity trend tracking,
gap-risk detection, market impact estimation, and historical snapshot reproducibility.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class LiquidityMetrics:
    symbol: str
    last_close: float = 0.0
    volume_20d_avg: float = 0.0
    adv_20d: float = 0.0  # 20-day Average Daily Traded Value = mean(Close * Volume)
    adv_5d: float = 0.0   # 5-day Average Daily Traded Value
    liquidity_trend: float = 1.0  # ADV_5 / ADV_20 ratio (> 1.15 = EXPANDING, < 0.85 = CONTRACTING)
    liquidity_trend_label: str = "STABLE"  # EXPANDING, STABLE, CONTRACTING
    rank_history: List[int] = field(default_factory=list)  # Historical rank trajectory
    active_sessions: int = 0
    valid_data: bool = True
    rejection_reason: Optional[str] = None
    spread_pct: float = 0.0
    gap_risk_flag: bool = False  # Flagged if empirical overnight gaps exceed safe thresholds
    avg_overnight_gap_pct: float = 0.0
    market_impact_est_bps: float = 0.0  # Estimated market impact / execution drag in basis points
    sector: str = "Broad Market"
    rank: int = 999999
    is_incumbent: bool = False
    status: str = "QUALIFIED"  # QUALIFIED, REJECTED, SELECTED, BUFFER_RETAINED, DROPPED, SECTOR_CAPPED


@dataclass
class UniverseSelectionResult:
    date: str
    market: str
    target_size: int = 30
    buffer_cutoff: int = 35
    total_eligible: int = 0
    screened_count: int = 0
    qualified_count: int = 0
    rejected_count: int = 0
    selected_symbols: List[str] = field(default_factory=list)
    retained_symbols: List[str] = field(default_factory=list)
    added_symbols: List[str] = field(default_factory=list)
    removed_symbols: List[str] = field(default_factory=list)
    sector_distribution: Dict[str, int] = field(default_factory=dict)
    sector_concentration_pct: Dict[str, float] = field(default_factory=dict)
    candidates: List[LiquidityMetrics] = field(default_factory=list)
    turnover_rate_pct: float = 0.0
    watchlist_path: Optional[str] = None
    metadata_path: Optional[str] = None
    snapshot_path: Optional[str] = None
