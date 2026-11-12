from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

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
    breakout_time: Optional[str] = None
    retest_time: Optional[str] = None
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
    cons_density_pct: Optional[float] = None
    cons_density_confirmed: bool = False
    rs_slope: Optional[float] = None
    rs_trend: str = ""
    composite_score: int = 0
    composite_grade: str = ""
    evidence_status: list[str] = field(default_factory=list)

    # Phase 1: Robust Intraday Rules & Policy Tags
    friction_r: Optional[float] = None
    underlying_validated: bool = False
    options_eligible: bool = False
    options_status: str = "UNDERLYING_UNVALIDATED"
    failed_breakout_confirmed: bool = False
    confirmation_status: str = "CONFIRMED"

    # Phase 2: Signal Quality & Noise Reduction
    candle_close_loc: Optional[float] = None
    candle_close_confirmed: bool = False
    vdu_slope: Optional[float] = None
    vdu_slope_confirmed: bool = False
    distance_to_trigger_pct: Optional[float] = None
    trigger_tightness_confirmed: bool = False
    failed_breakouts_in_base: int = 0
    atr: Optional[float] = None
    requires_confirmation: bool = False
    is_extended: bool = False

    # Phase 3: Confluence & Context Layer
    htf_4h_trend: int = 0
    htf_4h_confirmed: bool = False
    days_to_earnings: Optional[int] = None
    market_regime: str = "NORMAL"
    market_regime_favorable: bool = True

    # Phase 4: Risk & Portfolio Guard
    position_allocation: Optional[Any] = None

    @property
    def breakout_score(self) -> int:
        return self.bias_score

    @property
    def is_tp1_reached(self) -> bool:
        curr = self.current_price if self.current_price is not None else self.entry
        if curr is None or self.tp1 is None or self.entry is None:
            return False
        if self.direction == 1:
            return curr >= self.tp1
        elif self.direction == -1:
            return curr <= self.tp1
        return False

    @property
    def is_fresh_entry(self) -> bool:
        curr = self.current_price if self.current_price is not None else self.entry
        if curr is None or self.entry is None or self.entry <= 0:
            return False
        if self.is_tp1_reached:
            return False
        # Age check: setups older than 3 bars cannot show as fresh entries
        if self.bars_since_breakout is not None and self.bars_since_breakout > 3:
            return False

        # Base breach check: price must not have fallen back inside the base or breached stop
        if self.direction == 1:
            base_level = self.breakout_level if self.breakout_level is not None else self.resistance
            if base_level is not None and curr < base_level:
                return False
            if self.stop is not None and curr <= self.stop:
                return False
            favorable_dist = (curr - self.entry) / self.entry * 100.0
        elif self.direction == -1:
            base_level = self.breakout_level if self.breakout_level is not None else self.support
            if base_level is not None and curr > base_level:
                return False
            if self.stop is not None and curr >= self.stop:
                return False
            favorable_dist = (self.entry - curr) / self.entry * 100.0
        else:
            return False

        max_dist = 1.0
        if self.atr is not None and self.atr > 0 and self.entry > 0:
            atr_dist_pct = (0.5 * self.atr / self.entry) * 100.0
            max_dist = min(1.0, atr_dist_pct)

        return favorable_dist <= max_dist

    @property
    def trade_status_label(self) -> str:
        """
        Unambiguous institutional trade classification:
        - WATCH: Consolidating inside VCP base (preparation phase)
        - CONFIRMED: Fresh breakout within entry tolerance, playable now
        - LATE_CHASE: Extended past entry tolerance
        - TP1_REACHED: Take-profit 1 hit, holders only
        - FAILED_EXIT: Breached stop or fell back into base
        - BLOCKED_BY_RISK: High friction, wide risk, or imminent earnings
        """
        if self.state == State.FAILED:
            return "FAILED_EXIT"
        if self.earnings_warning:
            return "BLOCKED_BY_RISK"
        if self.state == State.CONTRACTION:
            return "WATCH"
        if self.state in (State.BUY, State.SELL):
            if getattr(self, "requires_confirmation", False):
                return "AWAITING_CONFIRMATION"
            elif self.is_fresh_entry:
                return "CONFIRMED"
            elif self.is_tp1_reached:
                return "TP1_REACHED"
            else:
                return "LATE_CHASE"
        return "IDLE"
