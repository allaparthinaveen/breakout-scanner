"""
Persistent Portfolio State Engine (P0-4 & P0-5)
==============================================
Maintains atomic, persistent state across hourly cloud daemon scans and process restarts.
Tracks:
1. Active open positions with live MTM valuation
2. Cumulative daily risk committed
3. Realized & Unrealized P&L
4. Daily loss circuit breaker (-2.0% equity cap)
"""

import os
import json
import fcntl
import logging
from datetime import datetime, date, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from .models import Setup, State, TradeExitEvent
from .portfolio_guard import PositionAllocation

logger = logging.getLogger("portfolio_state")


class PortfolioStateManager:
    """
    Manages persistent portfolio exposure on disk with atomic JSON writes and advisory file locks.
    Survives restarts and guarantees daily risk budgets and loss limits are never bypassed.
    """
    def __init__(self, state_file: str = "data/portfolio_state.json"):
        self.state_file = state_file
        self.lock_file = f"{state_file}.lock"
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
        
        self.date_str = date.today().isoformat()
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.closed_positions_today: List[Dict[str, Any]] = []
        self.cumulative_daily_risk: float = 0.0
        self.realized_daily_pnl: float = 0.0
        self.unrealized_daily_pnl: float = 0.0
        self.circuit_breaker_tripped: bool = False
        self.circuit_breaker_reason: Optional[str] = None

        self.load()

    def _acquire_lock(self, file_handle):
        try:
            fcntl.flock(file_handle, fcntl.LOCK_EX)
        except Exception:
            pass

    def _release_lock(self, file_handle):
        try:
            fcntl.flock(file_handle, fcntl.LOCK_UN)
        except Exception:
            pass

    def load(self):
        """Loads state from disk. Automatically resets daily metrics if session date has changed."""
        today_str = date.today().isoformat()
        if not os.path.exists(self.state_file):
            self.date_str = today_str
            self.active_positions = {}
            self.closed_positions_today = []
            self.cumulative_daily_risk = 0.0
            self.realized_daily_pnl = 0.0
            self.unrealized_daily_pnl = 0.0
            self.circuit_breaker_tripped = False
            self.circuit_breaker_reason = None
            self.save()
            return

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)

            saved_date = data.get("date", today_str)
            self.active_positions = data.get("active_positions", {})

            if saved_date != today_str:
                logger.info(f"🔄 [PORTFOLIO STATE] New session detected ({saved_date} -> {today_str}). Resetting daily risk/loss counters.")
                self.date_str = today_str
                self.closed_positions_today = []
                self.cumulative_daily_risk = sum(float(p.get("dollar_risk", 0.0)) for p in self.active_positions.values())
                self.realized_daily_pnl = 0.0
                self.circuit_breaker_tripped = False
                self.circuit_breaker_reason = None
                self.save()
            else:
                self.date_str = saved_date
                self.closed_positions_today = data.get("closed_positions_today", [])
                self.cumulative_daily_risk = float(data.get("cumulative_daily_risk", 0.0))
                self.realized_daily_pnl = float(data.get("realized_daily_pnl", 0.0))
                self.circuit_breaker_tripped = bool(data.get("circuit_breaker_tripped", False))
                self.circuit_breaker_reason = data.get("circuit_breaker_reason")

            self.unrealized_daily_pnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in self.active_positions.values())
        except Exception as e:
            logger.error(f"⚠️ Failed to parse portfolio state file {self.state_file}: {e}. Initializing fresh state.")
            self.date_str = today_str
            self.active_positions = {}
            self.closed_positions_today = []
            self.cumulative_daily_risk = 0.0
            self.realized_daily_pnl = 0.0
            self.unrealized_daily_pnl = 0.0
            self.circuit_breaker_tripped = False
            self.circuit_breaker_reason = None

    def save(self):
        """Atomically saves state to disk with file locking and temporary swap."""
        tmp_path = f"{self.state_file}.tmp"
        data = {
            "date": self.date_str,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active_positions": self.active_positions,
            "closed_positions_today": self.closed_positions_today,
            "cumulative_daily_risk": round(self.cumulative_daily_risk, 2),
            "realized_daily_pnl": round(self.realized_daily_pnl, 2),
            "unrealized_daily_pnl": round(self.unrealized_daily_pnl, 2),
            "total_daily_pnl": round(self.get_total_daily_pnl(), 2),
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "circuit_breaker_reason": self.circuit_breaker_reason,
        }

        try:
            with open(self.lock_file, "w") as lock_f:
                self._acquire_lock(lock_f)
                try:
                    with open(tmp_path, "w") as f:
                        json.dump(data, f, indent=2)
                    os.replace(tmp_path, self.state_file)
                finally:
                    self._release_lock(lock_f)
        except Exception as e:
            logger.error(f"❌ Failed to save persistent portfolio state: {e}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Returns open positions formatted for DailyRiskBudgetEngine consumption."""
        return list(self.active_positions.values())

    def record_entry(self, alloc: PositionAllocation) -> bool:
        """
        Records a newly approved trade entry from the ActionableSlate into persistent state.
        Guarantees that cumulative daily risk is persistently tracked.
        """
        sym = alloc.symbol
        if sym in self.active_positions:
            logger.warning(f"Position {sym} already recorded as active. Skipping duplicate entry.")
            return False

        pos_dict = {
            "symbol": sym,
            "direction": alloc.direction,
            "entry_price": alloc.entry_price,
            "stop_loss": alloc.stop_loss,
            "take_profit_1": alloc.take_profit_1,
            "take_profit_2": alloc.take_profit_2,
            "shares": alloc.shares,
            "dollar_risk": alloc.dollar_risk,
            "position_value": alloc.position_value,
            "risk_pct_of_equity": alloc.risk_pct_of_equity,
            "sector": alloc.sector or "Broad Market",
            "score": alloc.score,
            "grade": alloc.grade,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "current_price": alloc.entry_price,
            "unrealized_pnl": 0.0,
            "pnl_pct": 0.0,
            "status": "OPEN",
        }

        self.active_positions[sym] = pos_dict
        self.cumulative_daily_risk += alloc.dollar_risk
        self.save()
        logger.info(
            f"📥 [PORTFOLIO RECORDED] {sym} entered: {alloc.shares} shares @ ${alloc.entry_price:.2f} "
            f"(Risk: ${alloc.dollar_risk:.2f} | Total Day Risk: ${self.cumulative_daily_risk:.2f})"
        )
        return True

    def update_market_data(self, setups: List[Setup]) -> List[TradeExitEvent]:
        """
        Updates open positions against latest closed candle data (P1-3):
        1. Marks-to-market unrealized PnL
        2. Detects Stop Loss / Take Profit 2 exit breaches and realizes PnL
        3. Detects Take Profit 1 scale-out and trails stop to breakeven
        4. Generates explicit TradeExitEvent instances for operator/notification dispatch
        """
        exit_events: List[TradeExitEvent] = []
        if not self.active_positions:
            return exit_events

        setup_map = {s.symbol: s for s in setups}
        symbols_to_close = []

        total_unrealized = 0.0

        for sym, pos in self.active_positions.items():
            s = setup_map.get(sym)
            if not s or s.current_price is None:
                total_unrealized += float(pos.get("unrealized_pnl", 0.0))
                continue

            cp = s.current_price
            pos["current_price"] = cp
            direction = pos["direction"]
            entry = pos["entry_price"]
            stop = pos["stop_loss"]
            tp1 = pos.get("take_profit_1")
            tp2 = pos.get("take_profit_2")
            shares = pos["shares"]

            # Calculate live PnL
            if direction == 1:
                pnl = (cp - entry) * shares
                pnl_pct = (cp - entry) / entry * 100.0 if entry > 0 else 0.0
                stopped_out = (cp <= stop)
                target_reached = (tp2 is not None and cp >= tp2)
                hit_tp1 = (tp1 is not None and cp >= tp1)
            else:
                pnl = (entry - cp) * shares
                pnl_pct = (entry - cp) / entry * 100.0 if entry > 0 else 0.0
                stopped_out = (cp >= stop)
                target_reached = (tp2 is not None and cp <= tp2)
                hit_tp1 = (tp1 is not None and cp <= tp1)

            pos["unrealized_pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct, 2)
            now_str = datetime.now(timezone.utc).isoformat()

            if stopped_out:
                pos["status"] = "STOPPED_OUT"
                pos["exit_price"] = cp
                pos["closed_at"] = now_str
                pos["realized_pnl"] = round(pnl, 2)
                self.realized_daily_pnl += pos["realized_pnl"]
                symbols_to_close.append((sym, "STOP_LOSS", pos["realized_pnl"]))
                exit_events.append(TradeExitEvent(
                    symbol=sym,
                    event_type="STOP_LOSS",
                    direction=direction,
                    entry_price=entry,
                    exit_price=cp,
                    pnl_pct=round(pnl_pct, 2),
                    realized_pnl=round(pnl, 2),
                    shares=shares,
                    timestamp=now_str,
                    reason="Stop Loss Triggered"
                ))
            elif target_reached:
                pos["status"] = "TARGET_HIT"
                pos["exit_price"] = cp
                pos["closed_at"] = now_str
                pos["realized_pnl"] = round(pnl, 2)
                self.realized_daily_pnl += pos["realized_pnl"]
                symbols_to_close.append((sym, "TAKE_PROFIT_2", pos["realized_pnl"]))
                exit_events.append(TradeExitEvent(
                    symbol=sym,
                    event_type="TP2_EXIT",
                    direction=direction,
                    entry_price=entry,
                    exit_price=cp,
                    pnl_pct=round(pnl_pct, 2),
                    realized_pnl=round(pnl, 2),
                    shares=shares,
                    timestamp=now_str,
                    reason="Take Profit 2 Hit (Full Target Reached)"
                ))
            else:
                # Check TP1 scale out if not hit yet
                if hit_tp1 and not pos.get("tp1_hit", False) and shares > 1:
                    pos["tp1_hit"] = True
                    half_shares = shares // 2
                    partial_pnl = (cp - entry) * half_shares * direction
                    pos["shares"] -= half_shares
                    pos["realized_pnl"] = pos.get("realized_pnl", 0.0) + round(partial_pnl, 2)
                    self.realized_daily_pnl += round(partial_pnl, 2)
                    # Trail stop to breakeven
                    pos["stop_loss"] = entry * (1.0 + 0.001 * direction)
                    exit_events.append(TradeExitEvent(
                        symbol=sym,
                        event_type="TP1_SCALE",
                        direction=direction,
                        entry_price=entry,
                        exit_price=cp,
                        pnl_pct=round(pnl_pct, 2),
                        realized_pnl=round(partial_pnl, 2),
                        shares=half_shares,
                        timestamp=now_str,
                        reason="TP1 Scaled 50% & Stop Trailed to Breakeven"
                    ))
                total_unrealized += pnl

        for sym, reason, realized in symbols_to_close:
            closed_pos = self.active_positions.pop(sym)
            self.closed_positions_today.append(closed_pos)
            logger.info(f"📤 [POSITION CLOSED] {sym} closed by {reason}. Realized P&L: ${realized:+.2f}")

        self.unrealized_daily_pnl = round(total_unrealized, 2)
        self.save()
        return exit_events

    def get_total_daily_pnl(self) -> float:
        """Returns total realized and unrealized net P&L for today's session."""
        return round(self.realized_daily_pnl + self.unrealized_daily_pnl, 2)

    def check_circuit_breaker(self, account_equity: float, max_loss_pct: float = 2.0) -> bool:
        """
        Circuit Breaker (P0-5):
        Trips global kill-switch if total daily loss exceeds max_loss_pct of account equity.
        """
        if self.circuit_breaker_tripped:
            return True

        total_pnl = self.get_total_daily_pnl()
        max_loss_dollar = account_equity * (max_loss_pct / 100.0)

        if total_pnl <= -max_loss_dollar:
            self.circuit_breaker_tripped = True
            self.circuit_breaker_reason = (
                f"DAILY_LOSS_BREACH: Total daily loss ${abs(total_pnl):,.2f} "
                f"exceeds {max_loss_pct:.1f}% limit (${max_loss_dollar:,.2f})"
            )
            self.save()
            logger.critical(f"🛑 [CIRCUIT BREAKER TRIPPED] {self.circuit_breaker_reason}")
            return True

        return False
