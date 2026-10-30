"""
Phase 4: Risk & Portfolio Guard Layer
====================================
Bridges technical breakout signals to live institutional capital allocation:
1. Position Sizing Helper: Converts account equity risk (e.g., 1.0%) and structural ATR stop into exact share count.
2. Correlation Stacking Guard: Detects simultaneous alerts within the same sector/industry basket and selects only the single highest-scoring setup (Grade A+).
3. Daily Risk Budget Engine: Enforces max daily risk budget (e.g., 2.0%), max concurrent active positions (e.g., 4 trades), and sector concentration caps (e.g., 25%).
4. Actionable Trade Slate: Synthesizes candidates into an actionable, sized trade slate.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
import math
import numpy as np

from .models import Setup, State
from .sectors import get_sector_benchmark


@dataclass
class PositionAllocation:
    symbol: str
    direction: int = 1
    shares: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    dollar_risk: float = 0.0
    position_value: float = 0.0
    risk_pct_of_equity: float = 0.0
    capital_pct_of_equity: float = 0.0
    sector: str = ""
    score: int = 0
    grade: str = ""
    status: str = "APPROVED"  # 'APPROVED', 'SUPPRESSED', 'REJECTED'
    reason: Optional[str] = None
    setup: Optional[Setup] = None


@dataclass
class TradeSlate:
    portfolio_equity: float = 100000.0
    max_daily_risk_pct: float = 2.0
    max_daily_dollar_risk: float = 2000.0
    actionable_trades: List[PositionAllocation] = field(default_factory=list)
    suppressed_trades: List[PositionAllocation] = field(default_factory=list)
    total_daily_risk_allocated: float = 0.0
    total_capital_allocated: float = 0.0
    remaining_daily_risk_budget: float = 2000.0
    remaining_trade_slots: int = 4
    max_active_trades: int = 4


class PositionSizer:
    """
    Institutional Position Sizing Helper.
    Translates account equity risk % and structural stop distance into exact share/contract sizes.
    """
    def __init__(
        self,
        account_equity: float = 100000.0,
        risk_per_trade_pct: float = 1.0,
        max_position_size_pct: float = 20.0,
        allow_fractional: bool = False,
    ):
        self.account_equity = max(1.0, float(account_equity))
        self.risk_per_trade_pct = max(0.01, float(risk_per_trade_pct))
        self.max_position_size_pct = max(1.0, float(max_position_size_pct))
        self.allow_fractional = allow_fractional

    def calculate_size(
        self,
        entry: float,
        stop: float,
        direction: int = 1,
        symbol: str = "",
        sector: str = "",
        score: int = 0,
        grade: str = "",
        setup: Optional[Setup] = None,
    ) -> PositionAllocation:
        """
        Calculates position size strictly respecting risk and capital caps.
        """
        alloc = PositionAllocation(
            symbol=symbol,
            direction=direction,
            entry_price=entry if entry is not None else 0.0,
            stop_loss=stop if stop is not None else 0.0,
            sector=sector,
            score=score,
            grade=grade,
            setup=setup,
        )

        if entry is None or stop is None or entry <= 0 or stop <= 0 or entry == stop:
            alloc.status = "REJECTED"
            alloc.reason = "INVALID_PRICES: Entry and stop must be positive, non-equal values"
            return alloc

        per_share_risk = abs(entry - stop)
        target_dollar_risk = self.account_equity * (self.risk_per_trade_pct / 100.0)
        max_capital_for_trade = self.account_equity * (self.max_position_size_pct / 100.0)

        if self.allow_fractional:
            shares_by_risk = target_dollar_risk / per_share_risk
            shares_by_capital = max_capital_for_trade / entry
            shares = min(shares_by_risk, shares_by_capital)
        else:
            shares_by_risk = int(target_dollar_risk // per_share_risk)
            shares_by_capital = int(max_capital_for_trade // entry)
            shares = min(shares_by_risk, shares_by_capital)

        if shares <= 0:
            alloc.status = "REJECTED"
            alloc.shares = 0
            alloc.reason = (
                f"ZERO_SHARES_SIZED: Risk per share (${per_share_risk:.2f}) or price (${entry:.2f}) "
                f"exceeds risk budget (${target_dollar_risk:.2f}) or capital cap (${max_capital_for_trade:.2f})"
            )
            return alloc

        alloc.shares = shares
        alloc.position_value = round(shares * entry, 2)
        alloc.dollar_risk = round(shares * per_share_risk, 2)
        alloc.risk_pct_of_equity = round((alloc.dollar_risk / self.account_equity) * 100.0, 2)
        alloc.capital_pct_of_equity = round((alloc.position_value / self.account_equity) * 100.0, 2)

        if direction == 1:
            alloc.take_profit_1 = round(entry + per_share_risk, 4)
            alloc.take_profit_2 = round(entry + (2.0 * per_share_risk), 4)
        else:
            alloc.take_profit_1 = round(entry - per_share_risk, 4)
            alloc.take_profit_2 = round(entry - (2.0 * per_share_risk), 4)

        alloc.status = "APPROVED"
        return alloc


class CorrelationStackingGuard:
    """
    Prevents correlated industry stacking by filtering simultaneous alerts in the same sector.
    Selects only the highest-conviction setup per industry basket.
    """
    def __init__(self, max_trades_per_sector: int = 1):
        self.max_trades_per_sector = max(1, int(max_trades_per_sector))

    def filter_correlations(
        self, setups: List[Setup]
    ) -> Tuple[List[Setup], List[Tuple[Setup, str]]]:
        """
        Groups setups by sector, ranks by conviction (composite_score, sector RS, tightness),
        and filters out lower-ranked candidates in the same sector basket.
        """
        if not setups:
            return [], []

        # 1. Group setups by sector
        sector_groups: Dict[str, List[Setup]] = {}
        for s in setups:
            sec = s.sector_name
            if not sec:
                try:
                    sec, _ = get_sector_benchmark(s.symbol)
                except Exception:
                    sec = "Broad Market"
            sec = sec or "Broad Market"
            sector_groups.setdefault(sec, []).append(s)

        approved_setups: List[Setup] = []
        suppressed_setups: List[Tuple[Setup, str]] = []

        # 2. Rank each sector basket
        for sec, group in sector_groups.items():
            if len(group) <= self.max_trades_per_sector:
                approved_setups.extend(group)
            else:
                def ranking_key(setup: Setup):
                    score = setup.composite_score if (setup.composite_score is not None and np.isfinite(setup.composite_score)) else 0
                    rs = setup.sector_rs_pct if (setup.sector_rs_pct is not None and np.isfinite(setup.sector_rs_pct)) else -999.0
                    risk = -setup.risk_pct if (setup.risk_pct is not None and np.isfinite(setup.risk_pct)) else -999.0
                    return (score, rs, risk)

                sorted_group = sorted(group, key=ranking_key, reverse=True)
                approved = sorted_group[:self.max_trades_per_sector]
                suppressed = sorted_group[self.max_trades_per_sector:]

                approved_setups.extend(approved)
                winner = approved[0]
                winner_score = getattr(winner, 'composite_score', 'N/A')
                for sup in suppressed:
                    reason = (
                        f"CORRELATION_STACKING: Sector '{sec}' capped at {self.max_trades_per_sector}. "
                        f"Top setup selected: {winner.symbol} (Score: {winner_score})"
                    )
                    suppressed_setups.append((sup, reason))

        return approved_setups, suppressed_setups


class DailyRiskBudgetEngine:
    """
    Enforces daily risk limits, max concurrent open positions, and sector concentration caps.
    """
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        guard_cfg = cfg.get("portfolio_guard", {})
        risk_cfg = cfg.get("risk", {})

        self.account_equity = float(guard_cfg.get("account_equity", risk_cfg.get("account_size", 100000.0)))
        self.risk_per_trade_pct = float(guard_cfg.get("risk_per_trade_pct", risk_cfg.get("risk_per_trade_pct", 1.0)))
        self.max_daily_risk_pct = float(guard_cfg.get("max_daily_risk_pct", 2.0))
        self.max_active_trades = int(guard_cfg.get("max_active_trades", risk_cfg.get("max_positions", 4)))
        self.max_sector_allocation_pct = float(guard_cfg.get("max_sector_allocation_pct", 25.0))
        self.max_position_size_pct = float(guard_cfg.get("max_position_size_pct", 20.0))
        self.max_trades_per_sector = int(guard_cfg.get("max_trades_per_sector", 1))
        self.allow_fractional = bool(guard_cfg.get("allow_fractional", False))

        self.sizer = PositionSizer(
            account_equity=self.account_equity,
            risk_per_trade_pct=self.risk_per_trade_pct,
            max_position_size_pct=self.max_position_size_pct,
            allow_fractional=self.allow_fractional,
        )
        self.correlation_guard = CorrelationStackingGuard(
            max_trades_per_sector=self.max_trades_per_sector
        )
        from .rejection_gate import TradeRejectionGate
        self.rejection_gate = TradeRejectionGate(cfg)

    def allocate_slate(
        self,
        setups: List[Setup],
        open_positions: Optional[List[dict]] = None,
    ) -> TradeSlate:
        """
        Generates the institutional TradeSlate by applying correlation filtering,
        risk budget constraints, and exact position sizing.
        """
        open_positions = open_positions or []
        max_daily_dollar_risk = self.account_equity * (self.max_daily_risk_pct / 100.0)
        max_sector_capital = self.account_equity * (self.max_sector_allocation_pct / 100.0)

        current_active_count = len(open_positions)
        current_daily_risk = sum(float(p.get("dollar_risk", 0.0)) for p in open_positions)
        sector_capital_used: Dict[str, float] = {}
        for p in open_positions:
            sec = p.get("sector") or "Broad Market"
            sector_capital_used[sec] = sector_capital_used.get(sec, 0.0) + float(p.get("position_value", 0.0))

        # Filter candidate setups (active trades / fresh entries)
        active_candidates = [
            s for s in setups
            if s.state in (State.BUY, State.SELL) or (s.state == State.CONTRACTION and s.bias_score >= 3)
        ]

        # Step 1: Filter correlation stacking
        approved_setups, correlation_suppressed = self.correlation_guard.filter_correlations(active_candidates)

        actionable_trades: List[PositionAllocation] = []
        suppressed_trades: List[PositionAllocation] = []

        # Record correlation suppressed setups
        for sup_setup, sup_reason in correlation_suppressed:
            alloc = self.sizer.calculate_size(
                entry=sup_setup.entry or sup_setup.current_price or 0.0,
                stop=sup_setup.stop or 0.0,
                direction=sup_setup.direction,
                symbol=sup_setup.symbol,
                sector=sup_setup.sector_name or "Broad Market",
                score=sup_setup.composite_score,
                grade=sup_setup.composite_grade,
                setup=sup_setup,
            )
            alloc.status = "SUPPRESSED"
            alloc.reason = sup_reason
            suppressed_trades.append(alloc)

        # Step 2: Global priority sorting of correlation-approved candidates
        def global_priority(s: Setup):
            score = s.composite_score if (s.composite_score is not None and np.isfinite(s.composite_score)) else 0
            bias = s.bias_score if (s.bias_score is not None and np.isfinite(s.bias_score)) else 0
            return (score, bias)

        approved_setups.sort(key=global_priority, reverse=True)

        # Step 3: Risk budget allocation loop
        for s in approved_setups:
            entry = s.entry or s.current_price or 0.0
            stop = s.stop or 0.0
            sec = s.sector_name or "Broad Market"

            alloc = self.sizer.calculate_size(
                entry=entry,
                stop=stop,
                direction=s.direction,
                symbol=s.symbol,
                sector=sec,
                score=s.composite_score,
                grade=s.composite_grade,
                setup=s,
            )

            if alloc.status == "REJECTED":
                suppressed_trades.append(alloc)
                continue

            # Phase 4 Trade Rejection Gate (Rule 4.4: Structural Risk, Friction, Payoff, Earnings)
            decision = self.rejection_gate.evaluate_setup(s)
            if not decision.approved:
                alloc.status = "REJECTED"
                alloc.reason = decision.reason
                suppressed_trades.append(alloc)
                continue

            # Phase 2 Anti-Chase Guard: Suppress trades extended past pivot tolerance
            if s.state in (State.BUY, State.SELL) and not s.is_fresh_entry:
                alloc.status = "SUPPRESSED"
                alloc.reason = "LATE_CHASE: Price extended past pivot tolerance (Anti-Chase Guard)"
                suppressed_trades.append(alloc)
                continue

            # Check 1: Max active positions
            if current_active_count >= self.max_active_trades:
                alloc.status = "SUPPRESSED"
                alloc.reason = (
                    f"MAX_ACTIVE_TRADES_EXCEEDED: Active trades ({current_active_count}) "
                    f"reached maximum allowed limit ({self.max_active_trades})"
                )
                suppressed_trades.append(alloc)
                continue

            # Check 2: Daily risk budget
            if (current_daily_risk + alloc.dollar_risk) > (max_daily_dollar_risk + 1e-4):
                remaining_risk = max(0.0, max_daily_dollar_risk - current_daily_risk)
                alloc.status = "SUPPRESSED"
                alloc.reason = (
                    f"DAILY_RISK_BUDGET_EXCEEDED: Trade risk ${alloc.dollar_risk:.2f} exceeds "
                    f"remaining daily risk budget ${remaining_risk:.2f} (Max: ${max_daily_dollar_risk:.2f})"
                )
                suppressed_trades.append(alloc)
                continue

            # Check 3: Sector capital concentration cap
            cur_sec_cap = sector_capital_used.get(sec, 0.0)
            if (cur_sec_cap + alloc.position_value) > (max_sector_capital + 1e-4):
                alloc.status = "SUPPRESSED"
                alloc.reason = (
                    f"SECTOR_CAP_EXCEEDED: Sector '{sec}' capital would reach "
                    f"${cur_sec_cap + alloc.position_value:.2f} (exceeds {self.max_sector_allocation_pct}% cap: ${max_sector_capital:.2f})"
                )
                suppressed_trades.append(alloc)
                continue

            # Approved for live execution
            alloc.status = "APPROVED"
            current_active_count += 1
            current_daily_risk += alloc.dollar_risk
            sector_capital_used[sec] = cur_sec_cap + alloc.position_value

            s.position_allocation = alloc
            actionable_trades.append(alloc)

        total_cap_allocated = sum(t.position_value for t in actionable_trades)
        remaining_budget = max(0.0, max_daily_dollar_risk - current_daily_risk)
        remaining_slots = max(0, self.max_active_trades - current_active_count)

        return TradeSlate(
            portfolio_equity=self.account_equity,
            max_daily_risk_pct=self.max_daily_risk_pct,
            max_daily_dollar_risk=max_daily_dollar_risk,
            actionable_trades=actionable_trades,
            suppressed_trades=suppressed_trades,
            total_daily_risk_allocated=round(current_daily_risk, 2),
            total_capital_allocated=round(total_cap_allocated, 2),
            remaining_daily_risk_budget=round(remaining_budget, 2),
            remaining_trade_slots=remaining_slots,
            max_active_trades=self.max_active_trades,
        )


def generate_actionable_slate(
    candidates: List[Setup],
    config: Optional[dict] = None,
    open_positions: Optional[List[dict]] = None,
) -> TradeSlate:
    """
    Pure orchestrator entry point to produce the institutional TradeSlate.
    """
    engine = DailyRiskBudgetEngine(config)
    return engine.allocate_slate(candidates, open_positions=open_positions)


def format_trade_slate_cli(slate: TradeSlate) -> str:
    """
    Renders terminal ASCII output for the institutional TradeSlate.
    """
    lines = [
        "",
        "=======================================================",
        " SECTION 3: 🛡️ INSTITUTIONAL ACTIONABLE TRADE SLATE",
        "=======================================================",
        f" Portfolio Equity:    ${slate.portfolio_equity:,.2f} | Daily Risk Cap: ${slate.max_daily_dollar_risk:,.2f} ({slate.max_daily_risk_pct:.1f}%)",
        f" Allocated Risk:      ${slate.total_daily_risk_allocated:,.2f} | Remaining Risk Budget: ${slate.remaining_daily_risk_budget:,.2f}",
        f" Allocated Capital:   ${slate.total_capital_allocated:,.2f} | Open Slots: {slate.remaining_trade_slots} / {slate.max_active_trades}",
        "-------------------------------------------------------",
    ]

    if not slate.actionable_trades:
        lines.append("  (No trades approved for capital allocation)")
    else:
        for t in slate.actionable_trades:
            dir_str = "BUY" if t.direction == 1 else "SELL"
            lines.append(
                f"  • \033[92m{dir_str} {t.shares:,} shares {t.symbol:8}\033[0m @ ${t.entry_price:.2f} "
                f"(Value: ${t.position_value:,.2f} / {t.capital_pct_of_equity:.1f}% equity) [{t.sector}]"
            )
            lines.append(
                f"    Stop: ${t.stop_loss:.2f} | TP1: ${t.take_profit_1:.2f} (+1.0R) | TP2: ${t.take_profit_2:.2f} (+2.0R)"
            )
            grade_str = f"{t.grade} [{t.score}]" if t.grade else f"[{t.score}]"
            lines.append(
                f"    Risk: ${t.dollar_risk:,.2f} ({t.risk_pct_of_equity:.2f}% equity) | Grade: {grade_str}"
            )
            lines.append("")

    if slate.suppressed_trades:
        lines.append("-------------------------------------------------------")
        lines.append(" ⚠️ SUPPRESSED CANDIDATES (PORTFOLIO GUARD DEFENSE):")
        for s in slate.suppressed_trades:
            lines.append(f"  • \033[93m{s.symbol}\033[0m: {s.reason}")

    lines.append("=======================================================")
    return "\n".join(lines)


def format_trade_slate_html(slate: TradeSlate) -> str:
    """
    Renders clean Telegram HTML block for the institutional TradeSlate.
    """
    lines = [
        "🛡️ <b>ACTIONABLE TRADE SLATE (PORTFOLIO GUARD)</b>",
        f"Equity: <code>${slate.portfolio_equity:,.0f}</code> | Risk Budget: <code>${slate.max_daily_dollar_risk:,.0f} ({slate.max_daily_risk_pct:.1f}%)</code>",
        f"Allocated: <code>${slate.total_daily_risk_allocated:,.2f}</code> | Slots Left: <code>{slate.remaining_trade_slots}/{slate.max_active_trades}</code>",
        "",
    ]

    if not slate.actionable_trades:
        lines.append("<i>No trades approved for capital allocation.</i>")
    else:
        for t in slate.actionable_trades:
            dir_str = "BUY" if t.direction == 1 else "SELL"
            dir_emoji = "🟢" if t.direction == 1 else "🔴"
            lines.append(
                f"{dir_emoji} <b>{dir_str} {t.shares:,} {t.symbol}</b> @ <code>{t.entry_price:.2f}</code> [{t.sector}]"
            )
            lines.append(
                f"  • Pos Value: <code>${t.position_value:,.2f}</code> | Stop: <code>{t.stop_loss:.2f}</code>"
            )
            lines.append(
                f"  • TP1: <code>{t.take_profit_1:.2f}</code> | TP2: <code>{t.take_profit_2:.2f}</code>"
            )
            lines.append(
                f"  • Dollar Risk: <code>${t.dollar_risk:,.2f} ({t.risk_pct_of_equity:.2f}%)</code> | Grade: <b>{t.grade} [{t.score}]</b>"
            )
            lines.append("")

    if slate.suppressed_trades:
        lines.append("⚠️ <b>Risk Guard Suppressed:</b>")
        for s in slate.suppressed_trades:
            lines.append(f"• <code>{s.symbol}</code>: {s.reason}")

    return "\n".join(lines)
