"""
Phase 1: Unified Candidate-to-Slate Decision Pipeline
=====================================================
Integrates signal discovery, institutional rejection gates, dynamic position sizing,
and portfolio risk budgeting into a single versioned ActionableSlate domain model.

Pipeline Flow:
Raw Candles -> BreakoutEngine -> Setup Objects
                    |
                    v
    Pre-Trade Rejection Gate (rejection_gate.py)
    [Hard Gates: Max Stop 5%, Friction > 0.15R, R:R < 1.0, Earnings <= 3d]
                    |
                    v
    Portfolio Guard & Position Sizer (portfolio_guard.py)
    [Capital Allocation, Sector Caps 25%, Daily Risk Cap 2%, Correlation Stacking]
                    |
                    v
    ActionableSlate (Top-N Selected Trades + Suppressed Trades + Rejections)
                    |
    +---------------+---------------+
    |               |               |
    v               v               v
CLI Report    Telegram Alerts   Paper/Live Bot
"""

import os
import json
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from .models import Setup, State
from .rejection_gate import TradeRejectionGate, RejectionDecision
from .portfolio_guard import (
    PositionSizer,
    CorrelationStackingGuard,
    DailyRiskBudgetEngine,
    PositionAllocation,
    TradeSlate,
)
from .providers.yfinance_provider import YFinanceProvider
from .scanner import MultiSymbolScanner

logger = logging.getLogger("slate_pipeline")


@dataclass
class RejectionRecord:
    """Formal audit record of a candidate setup disqualified by pre-trade risk gates."""
    symbol: str
    rejection_code: str
    reason: str
    setup: Setup
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "rejection_code": self.rejection_code,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass
class SuppressedRecord:
    """Audit record of a valid setup suppressed due to portfolio risk budget or sector concentration caps."""
    symbol: str
    suppression_code: str
    reason: str
    setup: Setup
    allocation: Optional[PositionAllocation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "suppression_code": self.suppression_code,
            "reason": self.reason,
            "score": getattr(self.setup, "composite_score", None),
            "sector": getattr(self.setup, "sector_name", None),
        }


@dataclass
class WatchlistRecord:
    """Watchlist record for pre-breakout contraction setups with confirmed bias."""
    symbol: str
    bias_score: int
    resistance: float
    dist_to_pivot_pct: float
    setup: Optional[Setup] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bias_score": self.bias_score,
            "resistance": self.resistance,
            "dist_to_pivot_pct": self.dist_to_pivot_pct,
        }


@dataclass
class ActionableSlate:
    """
    Authoritative, versioned trade decision slate.
    All downstream consumers (Scheduler, Telegram bot, CLI report, Live execution)
    consume this exact domain model.
    """
    slate_id: str
    timestamp: str
    profile_name: str
    total_scanned: int
    portfolio_equity: float
    max_daily_risk_pct: float
    max_daily_dollar_risk: float
    selected_trades: List[PositionAllocation] = field(default_factory=list)
    suppressed_trades: List[SuppressedRecord] = field(default_factory=list)
    rejected_trades: List[RejectionRecord] = field(default_factory=list)
    watchlist: List[WatchlistRecord] = field(default_factory=list)
    total_daily_risk_allocated: float = 0.0
    total_capital_allocated: float = 0.0
    remaining_daily_risk_budget: float = 0.0
    remaining_trade_slots: int = 4
    max_active_trades: int = 4
    is_illustrative_sizing: bool = False
    effective_config_summary: Optional[str] = None

    @property
    def has_actionable_trades(self) -> bool:
        return len(self.selected_trades) > 0

    @property
    def actionable_trades(self) -> List[PositionAllocation]:
        return self.selected_trades

    @property
    def actionable_count(self) -> int:
        return len(self.selected_trades)

    @property
    def suppressed_count(self) -> int:
        return len(self.suppressed_trades)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_trades)

    @property
    def watchlist_count(self) -> int:
        return len(self.watchlist)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slate_id": self.slate_id,
            "timestamp": self.timestamp,
            "profile_name": self.profile_name,
            "total_scanned": self.total_scanned,
            "portfolio_equity": self.portfolio_equity,
            "max_daily_risk_pct": self.max_daily_risk_pct,
            "max_daily_dollar_risk": self.max_daily_dollar_risk,
            "selected_trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "shares": t.shares,
                    "entry_price": t.entry_price,
                    "stop_loss": t.stop_loss,
                    "take_profit_1": t.take_profit_1,
                    "take_profit_2": t.take_profit_2,
                    "dollar_risk": t.dollar_risk,
                    "position_value": t.position_value,
                    "risk_pct_of_equity": t.risk_pct_of_equity,
                    "sector": t.sector,
                    "score": t.score,
                    "grade": t.grade,
                }
                for t in self.selected_trades
            ],
            "suppressed_trades": [s.to_dict() for s in self.suppressed_trades],
            "rejected_trades": [r.to_dict() for r in self.rejected_trades],
            "watchlist": [w.to_dict() if hasattr(w, "to_dict") else w for w in self.watchlist],
            "total_daily_risk_allocated": self.total_daily_risk_allocated,
            "total_capital_allocated": self.total_capital_allocated,
            "remaining_daily_risk_budget": self.remaining_daily_risk_budget,
            "remaining_trade_slots": self.remaining_trade_slots,
            "max_active_trades": self.max_active_trades,
            "is_illustrative_sizing": self.is_illustrative_sizing,
        }

    def format_cli(self) -> str:
        """Renders comprehensive terminal output for operators."""
        lines = [
            "",
            "================================================================================",
            f" 🏛️ INSTITUTIONAL ACTIONABLE TRADE SLATE | {self.profile_name}",
            "================================================================================",
            f" Slate ID:        {self.slate_id} | Timestamp: {self.timestamp}",
            f" Portfolio Equity: ${self.portfolio_equity:,.2f} | Daily Risk Cap: ${self.max_daily_dollar_risk:,.2f} ({self.max_daily_risk_pct:.1f}%)",
            f" Allocated Risk:   ${self.total_daily_risk_allocated:,.2f} | Remaining Risk: ${self.remaining_daily_risk_budget:,.2f}",
            f" Allocated Capital:${self.total_capital_allocated:,.2f} | Open Slots: {self.remaining_trade_slots} / {self.max_active_trades}",
        ]

        if self.is_illustrative_sizing:
            lines.append(" ⚠️ NOTE: Portfolio Guard disabled in config. Position sizing is ILLUSTRATIVE only.")

        lines.append("--------------------------------------------------------------------------------")

        if not self.selected_trades:
            lines.append("  (No trades approved for capital allocation in this scan)")
        else:
            lines.append(f" 🎯 TOP-N SELECTED TRADES ({len(self.selected_trades)} APPROVED):")
            for t in self.selected_trades:
                dir_str = "BUY" if t.direction == 1 else "SELL"
                grade_str = f"{t.grade} [{t.score}]" if t.grade else f"[{t.score}]"
                lines.append(
                    f"  • \033[92m{dir_str:4} {t.shares:,} shares {t.symbol:8}\033[0m @ ${t.entry_price:.2f} "
                    f"(Position: ${t.position_value:,.2f} / {t.capital_pct_of_equity:.1f}% equity) [{t.sector}]"
                )
                lines.append(
                    f"    Stop: ${t.stop_loss:.2f} | TP1: ${t.take_profit_1:.2f} (+1.0R) | TP2: ${t.take_profit_2:.2f} (+2.0R)"
                )
                lines.append(
                    f"    Risk: ${t.dollar_risk:,.2f} ({t.risk_pct_of_equity:.2f}% equity) | Grade: {grade_str}"
                )
                lines.append("")

        if self.suppressed_trades:
            lines.append("--------------------------------------------------------------------------------")
            lines.append(f" 🔒 SUPPRESSED CANDIDATES ({len(self.suppressed_trades)} BLOCKED BY PORTFOLIO GUARD):")
            for s in self.suppressed_trades:
                lines.append(f"  • \033[93m{s.symbol:8}\033[0m: {s.reason}")

        if self.rejected_trades:
            lines.append("--------------------------------------------------------------------------------")
            lines.append(f" 🛡️ REJECTED CANDIDATES ({len(self.rejected_trades)} DISQUALIFIED BY RISK GATE):")
            for r in self.rejected_trades:
                lines.append(f"  • \033[91m{r.symbol:8}\033[0m [{r.rejection_code}]: {r.reason}")

        lines.append("================================================================================")
        return "\n".join(lines)


class EffectiveConfigSummary:
    """
    Generates and logs a prominent Effective Configuration Safety Matrix
    detailing which filters and safeguards are ACTIVE vs DISABLED on startup.
    """
    @staticmethod
    def generate_matrix(cfg: dict) -> str:
        cfg = cfg or {}
        profile_name = cfg.get("profile_name", "Default / Custom Profile")
        market = cfg.get("market", "Global")
        currency = cfg.get("currency", "USD")

        pg_cfg = cfg.get("portfolio_guard", {})
        pg_enabled = bool(pg_cfg.get("enabled", False))
        pg_equity = float(pg_cfg.get("account_equity", cfg.get("risk", {}).get("account_size", 100000.0)))
        pg_risk = float(pg_cfg.get("risk_per_trade_pct", 1.0))
        pg_daily_risk = float(pg_cfg.get("max_daily_risk_pct", 2.0))
        pg_max_trades = int(pg_cfg.get("max_active_trades", 4))

        rg_cfg = cfg.get("rejection_gate", {})
        rg_enabled = bool(rg_cfg.get("enabled", False))
        rg_max_stop = float(rg_cfg.get("max_stop_pct", 5.0))
        rg_max_fric = float(rg_cfg.get("max_friction_r", 0.15))
        rg_min_rr = float(rg_cfg.get("min_rr_ratio", 1.0))

        sq_cfg = cfg.get("signal_quality", {})
        sq_enabled = bool(sq_cfg.get("enabled", False))

        conf_cfg = cfg.get("confluence_context", {})
        conf_enabled = bool(conf_cfg.get("enabled", False))

        safe_cfg = cfg.get("safeguards", {})
        stale_cap = int(safe_cfg.get("max_stale_data_seconds", 600))
        kill_file = safe_cfg.get("kill_switch_file", "data/KILL_SWITCH")

        cost_filter = bool(cfg.get("risk", {}).get("use_cost_filter", False))

        lines = [
            "================================================================================",
            " 🏛️ BREAKOUT SCANNER EFFECTIVE CONFIGURATION SAFETY MATRIX",
            "================================================================================",
            f" Active Profile:         {profile_name} [{market} | {currency}]",
            f" Portfolio Guard:        {'🟢 ACTIVE' if pg_enabled else '⚠️ DISABLED (Illustrative Sizing)'} "
            f"[Equity: ${pg_equity:,.0f} | Risk/Trade: {pg_risk:.1f}% | Daily Cap: {pg_daily_risk:.1f}% | Max Pos: {pg_max_trades}]",
            f" Pre-Trade Rejection:    {'🟢 ACTIVE' if rg_enabled else '⚠️ DISABLED'} "
            f"[Max Stop: {rg_max_stop:.1f}% | Max Friction: {rg_max_fric:.2f}R | Min R:R: {rg_min_rr:.1f}R]",
            f" Signal Quality Layers:  {'🟢 ACTIVE' if sq_enabled else '⚪ DISABLED'} [CLV Confirmation, VDU Slope]",
            f" Confluence Context:     {'🟢 ACTIVE' if conf_enabled else '⚪ DISABLED'} [4H HTF Alignment, Regime Dampener]",
            f" Statutory Friction:     {'🟢 ACTIVE' if cost_filter else '⚪ DISABLED'} [Slippage + Commission Cost Filter]",
            f" Production Safeguards:  🟢 ACTIVE [Stale Data Cap: {stale_cap}s | Kill-Switch File: {kill_file}]",
            "================================================================================",
        ]
        return "\n".join(lines)

    @classmethod
    def log_matrix(cls, cfg: dict, log: Optional[logging.Logger] = None):
        target_logger = log or logger
        matrix = cls.generate_matrix(cfg)
        for line in matrix.split("\n"):
            target_logger.info(line)


class SlatePipeline:
    """
    Authoritative Unified Decision Pipeline runner.
    Takes candidate setups or raw symbol data, passes them through:
    1. Pre-Trade Rejection Gate (hard safety disqualifications)
    2. Portfolio Guard & Sizer (correlation stacking, budget allocation)
    3. Produces single ActionableSlate consumed identically by CLI, Telegram, and Bot.
    """
    def __init__(
        self,
        cfg: Optional[dict] = None,
        scanner: Optional[MultiSymbolScanner] = None,
        provider: Optional[Any] = None,
        rejection_gate: Optional[TradeRejectionGate] = None,
        risk_budget_engine: Optional[DailyRiskBudgetEngine] = None,
    ):
        self.cfg = cfg or {}
        self.profile_name = self.cfg.get("profile_name", "Standard")
        self.rejection_gate = rejection_gate or TradeRejectionGate(self.cfg)
        self.risk_budget_engine = risk_budget_engine or DailyRiskBudgetEngine(self.cfg)
        self.provider = provider or YFinanceProvider()
        self.scanner = scanner or MultiSymbolScanner(self.provider, self.cfg)
        self.portfolio_guard_enabled = bool(self.cfg.get("portfolio_guard", {}).get("enabled", False))

    def evaluate_setups(
        self,
        setups: List[Setup],
        total_scanned: Optional[int] = None,
        open_positions: Optional[List[dict]] = None,
    ) -> ActionableSlate:
        """
        Pure pipeline evaluation from a collection of Setup candidate objects.
        """
        open_positions = open_positions or []
        scanned_count = total_scanned if total_scanned is not None else len(setups)

        # 1. Separate setups into active candidates (BUY/SELL) vs watchlist (CONTRACTION with bias >= 3)
        active_candidates = [
            s for s in setups
            if s.state in (State.BUY, State.SELL)
        ]

        watchlist_records: List[WatchlistRecord] = []
        for s in setups:
            if s.state == State.CONTRACTION and (getattr(s, "bias_score", 0) or 0) >= 3:
                res = float(s.resistance) if s.resistance is not None and s.resistance > 0 else (float(s.breakout_level) if s.breakout_level is not None else 0.0)
                curr = float(s.current_price) if s.current_price is not None else (float(s.entry) if s.entry is not None else 0.0)
                dist_pct = round(((res - curr) / res) * 100.0, 2) if res > 0 and curr > 0 else 0.0
                watchlist_records.append(
                    WatchlistRecord(
                        symbol=s.symbol,
                        bias_score=int(getattr(s, "bias_score", 0) or 0),
                        resistance=res,
                        dist_to_pivot_pct=dist_pct,
                        setup=s,
                    )
                )

        rejected_records: List[RejectionRecord] = []
        pre_qualified_setups: List[Setup] = []

        # 2. Gate 1: Pre-Trade Rejection Gate (Rule 1.3: Stop > 5%, Friction > 0.15R, R:R < 1.0, Earnings <= 3d)
        for s in active_candidates:
            decision = self.rejection_gate.evaluate_setup(s)
            if not decision.approved:
                rejected_records.append(
                    RejectionRecord(
                        symbol=s.symbol,
                        rejection_code=decision.rejection_code or "REJECTED",
                        reason=decision.reason or "Disqualified by pre-trade rejection gate",
                        setup=s,
                        details=decision.details or {},
                    )
                )
            else:
                pre_qualified_setups.append(s)

        # 3. Gate 2: Portfolio Guard & Sizing Engine
        trade_slate = self.risk_budget_engine.allocate_slate(
            pre_qualified_setups,
            open_positions=open_positions,
        )

        selected_allocations: List[PositionAllocation] = []
        suppressed_records: List[SuppressedRecord] = []

        # Process actionable trades from trade_slate
        for alloc in trade_slate.actionable_trades:
            # Final Anti-Chase defense: Must be fresh playable entry
            if alloc.setup and alloc.setup.state in (State.BUY, State.SELL) and not alloc.setup.is_fresh_entry:
                suppressed_records.append(
                    SuppressedRecord(
                        symbol=alloc.symbol,
                        suppression_code="LATE_CHASE",
                        reason="LATE_CHASE: Extended past pivot tolerance (Anti-Chase Guard)",
                        setup=alloc.setup,
                        allocation=alloc,
                    )
                )
            elif (
                alloc.setup
                and alloc.setup.state in (State.BUY, State.SELL)
                and getattr(alloc.setup, "provisional_data_incomplete", False)
                and (getattr(alloc.setup, "composite_score", 0) or 0) < 50
            ):
                suppressed_records.append(
                    SuppressedRecord(
                        symbol=alloc.symbol,
                        suppression_code="PROVISIONAL_LOW_TECHNICAL_SCORE",
                        reason=f"PROVISIONAL_LOW_TECHNICAL_SCORE: Technical score {getattr(alloc.setup, 'composite_score', 0)} < 50 below quality threshold",
                        setup=alloc.setup,
                        allocation=alloc,
                    )
                )
            elif (
                alloc.setup
                and alloc.setup.state in (State.BUY, State.SELL)
                and getattr(alloc.setup, "requires_confirmation", False)
                and not getattr(alloc.setup, "provisional_data_incomplete", False)
            ):
                min_score = int(self.cfg.get("confirmation_policy", {}).get("min_auto_entry_score", 70))
                suppressed_records.append(
                    SuppressedRecord(
                        symbol=alloc.symbol,
                        suppression_code="AWAITING_CONFIRMATION",
                        reason=f"AWAITING_CONFIRMATION: Score {getattr(alloc.setup, 'composite_score', 0)} < {min_score} requires follow-through confirmation",
                        setup=alloc.setup,
                        allocation=alloc,
                    )
                )
            else:
                selected_allocations.append(alloc)

        # Process suppressed trades from trade_slate
        for alloc in trade_slate.suppressed_trades:
            # Check if this was already recorded in rejected_records
            if alloc.status == "REJECTED":
                # Ensure it's in rejected_records
                already_rejected = any(r.symbol == alloc.symbol for r in rejected_records)
                if not already_rejected and alloc.setup:
                    rejected_records.append(
                        RejectionRecord(
                            symbol=alloc.symbol,
                            rejection_code="REJECTION_GATE",
                            reason=alloc.reason or "Disqualified by risk gate",
                            setup=alloc.setup,
                        )
                    )
            else:
                suppressed_records.append(
                    SuppressedRecord(
                        symbol=alloc.symbol,
                        suppression_code="PORTFOLIO_GUARD",
                        reason=alloc.reason or "Suppressed by portfolio risk/correlation caps",
                        setup=alloc.setup if alloc.setup else Setup(symbol=alloc.symbol),
                        allocation=alloc,
                    )
                )

        now_str = datetime.now().isoformat()
        slate = ActionableSlate(
            slate_id=str(uuid.uuid4())[:8],
            timestamp=now_str,
            profile_name=self.profile_name,
            total_scanned=scanned_count,
            portfolio_equity=trade_slate.portfolio_equity,
            max_daily_risk_pct=trade_slate.max_daily_risk_pct,
            max_daily_dollar_risk=trade_slate.max_daily_dollar_risk,
            selected_trades=selected_allocations,
            suppressed_trades=suppressed_records,
            rejected_trades=rejected_records,
            watchlist=watchlist_records,
            total_daily_risk_allocated=trade_slate.total_daily_risk_allocated,
            total_capital_allocated=trade_slate.total_capital_allocated,
            remaining_daily_risk_budget=trade_slate.remaining_daily_risk_budget,
            remaining_trade_slots=trade_slate.remaining_trade_slots,
            max_active_trades=trade_slate.max_active_trades,
            is_illustrative_sizing=not self.portfolio_guard_enabled,
            effective_config_summary=EffectiveConfigSummary.generate_matrix(self.cfg),
        )

        # Unified Audit Ledger Recording
        try:
            from .audit_ledger import record_forward_test_ledger
            record_forward_test_ledger(setups, self.cfg, slate=slate)
        except Exception as e:
            logger.error(f"❌ Failed to record forward-test audit ledger: {e}", exc_info=True)

        return slate

    def run_scan(
        self,
        symbols: List[str],
        period: str = "60d",
        interval: str = "1h",
        open_positions: Optional[List[dict]] = None,
    ) -> ActionableSlate:
        """
        Executes end-to-end multi-symbol scan and returns authoritative ActionableSlate.
        """
        setups = self.scanner.scan(symbols, period=period, interval=interval)
        return self.evaluate_setups(
            setups=setups,
            total_scanned=len(symbols),
            open_positions=open_positions,
        )
