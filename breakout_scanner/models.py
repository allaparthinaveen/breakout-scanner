from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class State(str, Enum):
    IDLE = 'IDLE'
    CONTRACTION = 'CONTRACTION'
    BUY = 'BUY'
    SELL = 'SELL'
    FAILED = 'FAILED'

@dataclass
class Setup:
    symbol: str
    state: State = State.IDLE
    trend: int = 0
    support: Optional[float] = None
    resistance: Optional[float] = None
    direction: int = 0
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    evidence: list[str] = field(default_factory=list)
    failure_reason: Optional[str] = None
    cons_index: Optional[int] = None
    breakout_index: Optional[int] = None
    bars_since_breakout: Optional[int] = None
    
    # V2.3 Advanced Trade Management
    original_stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    
    tp1_hit: bool = False
    tp2_hit: bool = False
    
    entry_day: Optional[str] = None
    held_days: int = 0
    current_price: Optional[float] = None
    bias_score: int = 0
    earnings_warning: bool = False
    base_width_pct: Optional[float] = None
    risk_pct: Optional[float] = None
    pnl_pct: Optional[float] = None
    
    # Phase 1: Signal Precision
    htf_trend: int = 0
    htf_confirmed: bool = False
    sector_name: Optional[str] = None
    sector_rs_pct: Optional[float] = None
    breakout_type: str = "BREAKOUT"
    retest_confirmed: bool = False
    breakout_level: Optional[float] = None

    # Phase 3: Quantum Signal Precision (Steps 1, 2, 3, 4)
    vdu_confirmed: bool = False
    vdu_ratio: Optional[float] = None
    rvol: Optional[float] = None
    decay_ratio: Optional[float] = None
    tightening_confirmed: bool = False
    rs_slope: Optional[float] = None
    rs_trend: str = ""
    composite_score: int = 0
    composite_grade: str = ""

    @property
    def breakout_score(self) -> int:
        return self.bias_score

    @property
    def is_tp1_reached(self) -> bool:
        if self.current_price is None or self.tp1 is None or self.entry is None:
            return False
        if self.direction == 1:
            return self.current_price >= self.tp1
        elif self.direction == -1:
            return self.current_price <= self.tp1
        return False

    @property
    def is_fresh_entry(self) -> bool:
        if self.current_price is None or self.entry is None or self.entry <= 0:
            return False
        if self.is_tp1_reached:
            return False
        if self.direction == 1:
            favorable_dist = (self.current_price - self.entry) / self.entry * 100.0
        elif self.direction == -1:
            favorable_dist = (self.entry - self.current_price) / self.entry * 100.0
        else:
            favorable_dist = 0.0
        return favorable_dist <= 1.0
