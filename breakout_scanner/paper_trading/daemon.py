"""
Phase 5: Automated Paper Trading Daemon
=======================================
Runs real-time or shadow paper trading with strict closed-bar ingestion,
next-bar open virtual order execution, slippage audit logging, and performance telemetry.
"""

import os
import json
import uuid
import datetime
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from .models import (
    PaperOrder,
    ExecutionVarianceRecord,
    PerformanceLedgerRecord,
)
from ..models import Setup, State
from ..engine import BreakoutEngine
from ..portfolio_guard import DailyRiskBudgetEngine
from ..friction import MarketFrictionEngine
from ..rejection_gate import TradeRejectionGate


DEFAULT_ORDER_LEDGER = "data/paper_trading/orders.jsonl"
DEFAULT_PERF_LEDGER = "data/paper_trading/performance_ledger.jsonl"


class PaperTradingDaemon:
    """
    Automated Shadow Paper Trading Execution Daemon.
    """
    def __init__(
        self,
        cfg: Optional[Dict[str, Any]] = None,
        initial_equity: float = 100000.0,
        order_ledger_path: str = DEFAULT_ORDER_LEDGER,
        perf_ledger_path: str = DEFAULT_PERF_LEDGER,
    ):
        self.cfg = cfg or {}
        self.initial_equity = float(initial_equity)
        self.current_equity = float(initial_equity)
        self.peak_equity = float(initial_equity)
        self.max_drawdown_pct = 0.0

        self.order_ledger_path = order_ledger_path
        self.perf_ledger_path = perf_ledger_path

        os.makedirs(os.path.dirname(self.order_ledger_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.perf_ledger_path), exist_ok=True)

        self.portfolio_guard = DailyRiskBudgetEngine(self.cfg)
        self.friction_engine = MarketFrictionEngine(self.cfg)
        self.rejection_gate = TradeRejectionGate(self.cfg)

        self.pending_orders: Dict[str, PaperOrder] = {}
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.completed_trades: List[Dict[str, Any]] = []
        self.variance_records: List[ExecutionVarianceRecord] = []

        self.total_scanned_count = 0
        self.total_actionable_count = 0
        self.total_rejected_count = 0

    def ingest_closed_candle(
        self,
        symbol: str,
        candle: pd.Series,
        history_df: pd.DataFrame
    ) -> Optional[PaperOrder]:
        """
        Anti-wick Policy: Strictly processes confirmed, closed bars.
        1. Executes any pending order from the prior bar using the current bar's Open.
        2. Evaluates exit triggers on active positions.
        3. If idle, evaluates scanner for fresh breakout setups and stages orders for next-bar open.
        """
        cur_open = float(candle['Open'])
        cur_high = float(candle['High'])
        cur_low = float(candle['Low'])
        cur_close = float(candle['Close'])
        cur_time = str(candle.name)

        # -------------------------------------------------------------
        # 1. Fill Pending Orders at Current Bar Open
        # -------------------------------------------------------------
        if symbol in self.pending_orders:
            order = self.pending_orders.pop(symbol)
            slip_pct = self.friction_engine.calculate_dynamic_slippage_pct(
                cur_open, atr=order.metadata.get("atr")
            ) / 100.0

            if order.direction == 1:
                fill_price = cur_open * (1.0 + slip_pct)
            else:
                fill_price = cur_open * (1.0 - slip_pct)

            slippage_delta = fill_price - order.theoretical_price
            order.status = "FILLED"
            order.fill_price = round(fill_price, 4)
            order.fill_timestamp = cur_time
            order.slippage_delta = round(slippage_delta, 4)

            # Record Execution Variance Audit
            per_share_risk = abs(order.theoretical_price - order.stop_loss)
            slippage_r = abs(slippage_delta) / per_share_risk if per_share_risk > 1e-4 else 0.0

            variance = ExecutionVarianceRecord(
                order_id=order.order_id,
                symbol=symbol,
                signal_timestamp=order.signal_timestamp,
                theoretical_price=order.theoretical_price,
                fill_timestamp=cur_time,
                actual_fill_price=round(fill_price, 4),
                slippage_abs=round(abs(slippage_delta), 4),
                slippage_pct=round(abs(slippage_delta / order.theoretical_price) * 100.0, 3),
                slippage_r=round(float(slippage_r), 3)
            )
            self.variance_records.append(variance)
            self._log_order(order)

            # Open Position
            self.active_positions[symbol] = {
                "symbol": symbol,
                "direction": order.direction,
                "shares_total": order.shares,
                "shares_remaining": order.shares,
                "entry_price": fill_price,
                "current_stop": order.stop_loss,
                "tp1": order.take_profit_1,
                "tp2": order.take_profit_2,
                "tp1_hit": False,
                "open_time": cur_time,
                "bars_held": 0,
                "order_id": order.order_id,
                "risk_cash": order.shares * per_share_risk,
                "realized_pnl": 0.0,
                "atr": order.metadata.get("atr")
            }

        # -------------------------------------------------------------
        # 2. Manage Active Position (Stops, TP1 50% scale-out, TP2)
        # -------------------------------------------------------------
        if symbol in self.active_positions:
            pos = self.active_positions[symbol]
            pos["bars_held"] += 1
            d = pos["direction"]
            stop = pos["current_stop"]
            tp1 = pos["tp1"]
            tp2 = pos["tp2"]

            # Intra-bar pessimistic stop check
            hit_stop = (cur_low <= stop) if d == 1 else (cur_high >= stop)
            if hit_stop:
                exit_fill = stop
                exit_pnl = (exit_fill - pos["entry_price"]) * pos["shares_remaining"] * d
                total_trade_pnl = pos["realized_pnl"] + exit_pnl
                self._close_position(symbol, exit_fill, cur_time, "STOP_LOSS", total_trade_pnl, pos)
                return None

            # TP1 Scale-Out (50%)
            hit_tp1 = (cur_high >= tp1) if d == 1 else (cur_low <= tp1)
            if not pos["tp1_hit"] and hit_tp1:
                pos["tp1_hit"] = True
                shares_scaled = pos["shares_total"] * 0.5
                pos["shares_remaining"] -= shares_scaled
                tp1_pnl = (tp1 - pos["entry_price"]) * shares_scaled * d
                pos["realized_pnl"] += tp1_pnl
                # Trail stop to Breakeven
                pos["current_stop"] = pos["entry_price"]

            # TP2 Terminal Exit
            hit_tp2 = (cur_high >= tp2) if d == 1 else (cur_low <= tp2)
            if pos["tp1_hit"] and hit_tp2 and pos["shares_remaining"] > 0:
                tp2_pnl = (tp2 - pos["entry_price"]) * pos["shares_remaining"] * d
                total_trade_pnl = pos["realized_pnl"] + tp2_pnl
                self._close_position(symbol, tp2, cur_time, "TP2_HIT", total_trade_pnl, pos)
                return None

        # -------------------------------------------------------------
        # 3. Discover Fresh Breakouts on Closed Candle
        # -------------------------------------------------------------
        if symbol not in self.active_positions and symbol not in self.pending_orders:
            self.total_scanned_count += 1
            if len(history_df) >= 60:
                engine = BreakoutEngine(self.cfg, symbol)
                setup = engine.run(history_df)

                if setup.state in (State.BUY, State.SELL):
                    # Evaluate Rejection Gate
                    rejection = self.rejection_gate.evaluate_setup(setup)
                    if not rejection.approved:
                        self.total_rejected_count += 1
                        return None

                    # Size position via PortfolioGuard
                    slate = self.portfolio_guard.allocate_slate([setup])
                    if slate.actionable_trades:
                        alloc = slate.actionable_trades[0]
                        self.total_actionable_count += 1

                        order = PaperOrder(
                            order_id=str(uuid.uuid4())[:8],
                            symbol=symbol,
                            direction=setup.direction,
                            shares=alloc.shares,
                            order_type="MARKET_OPEN",
                            theoretical_price=float(setup.entry or cur_close),
                            signal_timestamp=cur_time,
                            stop_loss=float(setup.stop),
                            take_profit_1=float(setup.tp1 or (setup.entry + setup.direction * abs(setup.entry - setup.stop))),
                            take_profit_2=float(setup.tp2 or (setup.entry + 2.0 * setup.direction * abs(setup.entry - setup.stop))),
                            metadata={"composite_score": setup.composite_score, "atr": setup.atr}
                        )
                        self.pending_orders[symbol] = order
                        return order
                    else:
                        self.total_rejected_count += 1

        return None

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_time: str,
        reason: str,
        net_pnl: float,
        pos: Dict[str, Any]
    ):
        """Finalizes an active position and records performance."""
        del self.active_positions[symbol]
        self.current_equity += net_pnl
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        dd = (self.peak_equity - self.current_equity) / self.peak_equity * 100.0 if self.peak_equity > 0 else 0.0
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd

        risk_cash = max(1.0, float(pos.get("risk_cash", 1000.0)))
        realized_r = net_pnl / risk_cash

        trade_record = {
            "symbol": symbol,
            "direction": pos["direction"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "net_pnl": round(float(net_pnl), 2),
            "realized_r": round(float(realized_r), 3),
            "exit_reason": reason,
            "exit_time": exit_time,
            "bars_held": pos["bars_held"],
        }
        self.completed_trades.append(trade_record)
        self._record_telemetry(exit_time)

    def _log_order(self, order: PaperOrder):
        """Appends virtual order record to JSONL ledger."""
        try:
            with open(self.order_ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(order.__dict__, default=str) + "\n")
        except Exception:
            pass

    def _record_telemetry(self, timestamp: str):
        """Appends updated portfolio telemetry to performance ledger."""
        trades = self.completed_trades
        wins = [t for t in trades if t["net_pnl"] > 0]
        losses = [t for t in trades if t["net_pnl"] <= 0]
        win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

        gross_profit = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-4 else (99.0 if gross_profit > 0 else 0.0)

        r_vals = [t["realized_r"] for t in trades]
        avg_r = float(np.mean(r_vals)) if r_vals else 0.0

        slip_r_vals = [v.slippage_r for v in self.variance_records]
        avg_slip_r = float(np.mean(slip_r_vals)) if slip_r_vals else 0.0

        entry = PerformanceLedgerRecord(
            timestamp=timestamp,
            total_scanned=self.total_scanned_count,
            total_actionable=self.total_actionable_count,
            total_rejected=self.total_rejected_count,
            active_positions=len(self.active_positions),
            realized_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            realized_expectancy_r=round(avg_r, 3),
            total_net_pnl=round(self.current_equity - self.initial_equity, 2),
            max_drawdown_pct=round(self.max_drawdown_pct, 2),
            average_slippage_r=round(avg_slip_r, 3),
            system_health_status="HEALTHY"
        )

        try:
            with open(self.perf_ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.__dict__) + "\n")
        except Exception:
            pass

    def replay_dataset(
        self,
        symbols_data: Dict[str, pd.DataFrame],
        warmup_bars: int = 60
    ) -> Dict[str, Any]:
        """
        Executes a chronological, bar-by-bar paper simulation replay across multiple symbols.
        """
        all_timestamps = sorted(list({ts for df in symbols_data.values() for ts in df.index}))
        print(f"Replaying {len(symbols_data)} symbols across {len(all_timestamps)} chronological timestamps...")

        for ts in all_timestamps:
            for sym, df in symbols_data.items():
                if ts in df.index:
                    loc = df.index.get_loc(ts)
                    if loc >= warmup_bars:
                        candle = df.iloc[loc]
                        hist = df.iloc[:loc+1]
                        self.ingest_closed_candle(sym, candle, hist)

        return {
            "completed_trades": len(self.completed_trades),
            "net_pnl": round(self.current_equity - self.initial_equity, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "variance_count": len(self.variance_records)
        }

