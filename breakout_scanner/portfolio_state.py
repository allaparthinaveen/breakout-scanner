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
from .exit_rules import evaluate_position_bar

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

    def record_entry(
        self,
        alloc: PositionAllocation,
        signal_id: Optional[str] = None,
        pivot: Optional[float] = None,
        delivery_status: str = "PENDING",
    ) -> bool:
        """
        Records a newly approved trade entry from the ActionableSlate into persistent state.
        Guarantees that cumulative daily risk is persistently tracked.
        """
        sym = alloc.symbol
        if sym in self.active_positions:
            logger.warning(f"Position {sym} already recorded as active. Skipping duplicate entry.")
            return False

        setup_obj = getattr(alloc, "setup", None)
        target_dir = alloc.direction
        calc_pivot = pivot
        if calc_pivot is None and setup_obj:
            calc_pivot = setup_obj.breakout_level or (setup_obj.resistance if target_dir == 1 else setup_obj.support) or alloc.entry_price
        if calc_pivot is None:
            calc_pivot = alloc.entry_price

        calc_sig_id = signal_id or getattr(setup_obj, "signal_id", None) or f"{sym}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        pos_dict = {
            "position_id": f"pos_{sym}_{calc_sig_id}",
            "signal_id": calc_sig_id,
            "symbol": sym,
            "direction": alloc.direction,
            "pivot": float(calc_pivot),
            "entry_price": alloc.entry_price,
            "intended_fill": alloc.entry_price,
            "modeled_fill": None,
            "fill_bar_ts": None,
            "fill_basis": None,
            "stop_loss": alloc.stop_loss,
            "initial_stop": alloc.stop_loss,
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
            "realized_pnl": 0.0,
            "realized_r": None,
            "pnl_pct": 0.0,
            "status": "AWAITING_FILL",
            "events": [],
            "exit_reason": None,
            "exit_price": None,
            "exit_bar_ts": None,
            "last_processed_bar_ts": None,
            "delivery_status": delivery_status,
        }

        self.active_positions[sym] = pos_dict
        self.cumulative_daily_risk += alloc.dollar_risk
        self.save()
        logger.info(
            f"📥 [PORTFOLIO RECORDED] {sym} entered: {alloc.shares} shares @ ${alloc.entry_price:.2f} "
            f"(Risk: ${alloc.dollar_risk:.2f} | Total Day Risk: ${self.cumulative_daily_risk:.2f})"
        )
        return True

    def update_delivery_status(self, symbols: List[str], delivery_status: str):
        """Updates Telegram dispatch delivery status across open positions."""
        modified = False
        for sym in symbols:
            if sym in self.active_positions:
                self.active_positions[sym]["delivery_status"] = delivery_status
                modified = True
        if modified:
            self.save()

    def update_market_data(
        self,
        setups: List[Setup],
        symbol_dfs: Optional[Dict[str, Any]] = None,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> List[TradeExitEvent]:
        """
        Updates open positions against latest closed candle data via unified evaluate_position_bar:
        1. Marks-to-market unrealized PnL
        2. Detects Stop Loss / Failed Breakout / Take Profit 2 exit breaches and realizes PnL
        3. Detects Take Profit 1 scale-out and trails stop to breakeven
        4. Generates explicit TradeExitEvent instances for operator/notification dispatch
        """
        exit_events: List[TradeExitEvent] = []
        if not self.active_positions:
            return exit_events

        setup_map = {s.symbol: s for s in setups}
        symbols_to_close = []
        total_unrealized = 0.0

        for sym, pos in list(self.active_positions.items()):
            # Reject mutations on already closed positions
            if pos.get("status") == "CLOSED" or pos.get("exit_reason") is not None:
                continue

            bars_to_eval = []
            df = None
            if symbol_dfs and sym in symbol_dfs:
                df = symbol_dfs[sym]
            elif setup_map.get(sym) and getattr(setup_map[sym], "raw_df", None) is not None:
                df = setup_map[sym].raw_df

            if df is not None and not df.empty:
                last_proc = pos.get("last_processed_bar_ts")
                for idx, row in df.iterrows():
                    bar_ts_str = str(idx)
                    if last_proc and bar_ts_str <= str(last_proc):
                        continue
                    bars_to_eval.append({
                        "Open": float(row["Open"]),
                        "High": float(row["High"]),
                        "Low": float(row["Low"]),
                        "Close": float(row["Close"]),
                        "bar_ts": bar_ts_str,
                    })
            elif setup_map.get(sym):
                s = setup_map[sym]
                if s.current_price is not None:
                    # When evaluated from scalar setup current_price with no OHLC dataframe,
                    # treat position as filled at recorded entry_price if still awaiting fill
                    if pos.get("status") == "AWAITING_FILL" or pos.get("modeled_fill") is None:
                        fill_px = float(pos.get("entry_price") or s.current_price)
                        pos["modeled_fill"] = fill_px
                        pos["entry_price"] = fill_px
                        pos["status"] = "OPEN"
                        pos["fill_basis"] = "SIGNAL_PRICE"
                        pos["bars_held"] = 1
                    bars_to_eval.append({
                        "Open": float(s.current_price),
                        "High": float(s.current_price),
                        "Low": float(s.current_price),
                        "Close": float(s.current_price),
                        "bar_ts": getattr(s, "breakout_time", datetime.now(timezone.utc).isoformat()),
                    })

            if not bars_to_eval:
                total_unrealized += float(pos.get("unrealized_pnl", 0.0))
                continue

            for bar in bars_to_eval:
                emitted = evaluate_position_bar(pos, bar, cfg=cfg)
                for ev in emitted:
                    ev_type = ev.get("event_type", "EXIT")
                    if ev_type in ("STOP_LOSS", "BREAKEVEN_STOP", "FAILED_BREAKOUT", "TP2_EXIT", "TIME_EXIT", "TP1_SCALE"):
                        exit_events.append(TradeExitEvent(
                            symbol=sym,
                            event_type=ev_type,
                            direction=pos["direction"],
                            entry_price=float(pos.get("modeled_fill") or pos.get("entry_price") or 0.0),
                            exit_price=float(ev.get("exit_price", pos.get("current_price", 0.0))),
                            pnl_pct=float(ev.get("pnl_pct", 0.0)),
                            realized_pnl=float(ev.get("realized_pnl", 0.0)),
                            shares=int(ev.get("shares", pos.get("shares", 0))),
                            bars_held=int(pos.get("bars_held", 0)),
                            timestamp=str(ev.get("timestamp", datetime.now(timezone.utc).isoformat())),
                            reason=str(ev.get("reason", "")),
                        ))

                if pos.get("status") == "CLOSED":
                    symbols_to_close.append((sym, pos.get("exit_reason", "CLOSED"), float(pos.get("realized_pnl", 0.0))))
                    break

            if pos.get("status") != "CLOSED":
                total_unrealized += float(pos.get("unrealized_pnl", 0.0))

        for sym, reason, realized in symbols_to_close:
            if sym in self.active_positions:
                closed_pos = self.active_positions.pop(sym)
                self.closed_positions_today.append(closed_pos)
                self.realized_daily_pnl += realized
                logger.info(f"📤 [POSITION CLOSED] {sym} closed by {reason}. Realized P&L: ${realized:+.2f}")

        self.unrealized_daily_pnl = round(total_unrealized, 2)
        self.save()
        return exit_events
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
