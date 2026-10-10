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
