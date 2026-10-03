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
