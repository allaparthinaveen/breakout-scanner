"""
Phase 5: Paper Trading Data Models
==================================
Defines structured records for virtual orders, execution variance audits,
performance telemetry, and Go/No-Go validation gates.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    direction: int
    shares: float
    order_type: str = "MARKET_OPEN"  # Next-bar open fill
    theoretical_price: float = 0.0
    signal_timestamp: str = ""
    status: str = "PENDING"  # PENDING, FILLED, REJECTED, CANCELLED
    fill_price: Optional[float] = None
    fill_timestamp: Optional[str] = None
    slippage_delta: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionVarianceRecord:
    order_id: str
    symbol: str
    signal_timestamp: str
    theoretical_price: float
    fill_timestamp: str
    actual_fill_price: float
    slippage_abs: float
    slippage_pct: float
    slippage_r: float
    execution_latency_ms: float = 0.0


@dataclass
class PerformanceLedgerRecord:
    timestamp: str
    total_scanned: int
    total_actionable: int
    total_rejected: int
    active_positions: int
    realized_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    realized_expectancy_r: float
    total_net_pnl: float
    max_drawdown_pct: float
    average_slippage_r: float
    system_health_status: str = "HEALTHY"


@dataclass
class GateAuditCheck:
    criterion_name: str
    threshold_str: str
    actual_value_str: str
    passed: bool
    fail_action: str


@dataclass
class GoNoGoAuditReport:
    timestamp: str
    total_trades_evaluated: int
    validation_window_days: int
    overall_verdict: str  # 'GO' or 'NO-GO'
    checks: List[GateAuditCheck]
    summary_notes: List[str]

