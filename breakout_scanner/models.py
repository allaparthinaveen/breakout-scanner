from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class State(str, Enum):
    IDLE='IDLE'; CONSOLIDATION='CONSOLIDATION'; BREAKOUT_READY='BREAKOUT_READY'
    ACCEPTANCE_WATCH='ACCEPTANCE_WATCH'; ACCEPTED_RETEST_WATCH='ACCEPTED_RETEST_WATCH'
    BUY='BUY'; SELL='SELL'; FAILED='FAILED'

@dataclass
class Setup:
    symbol: str
    state: State=State.IDLE
    trend: int=0
    support: Optional[float]=None
    resistance: Optional[float]=None
    breakout_level: Optional[float]=None
    direction: int=0
    breakout_score: int=0
    breakout_index: Optional[int]=None
    acceptance_count: int=0
    retest_index: Optional[int]=None
    entry: Optional[float]=None
    stop: Optional[float]=None
    target: Optional[float]=None
    evidence: list[str]=field(default_factory=list)
    failure_reason: Optional[str]=None
    cons_index: Optional[int]=None
