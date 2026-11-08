"""
Phase 4: Trade Rejection Gate Layer (breakout_scanner/rejection_gate.py)
======================================================================
Automated pre-trade disqualification gate enforcing institutional discipline:
1. Structural Risk Exceeded: (Entry - Stop) / Entry > max_stop_pct (default: 5.0%)
2. Excessive Friction: Round-trip statutory friction > max_friction_r (default: 0.15R)
3. Unfavorable Payoff: Distance to TP1 < 1.0 * Risk Distance (R:R < 1.0)
4. Imminent Earnings: Event scheduled within <= 3 trading sessions
5. Missing Structural Stop: Zero/negative/None stop loss (no synthetic fallbacks)
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import math
import numpy as np

from .models import Setup, State
from .friction import MarketFrictionEngine, FrictionBreakdown


@dataclass
class RejectionDecision:
    approved: bool
    rejection_code: Optional[str] = None
    reason: Optional[str] = None
    details: Dict[str, Any] = None


class TradeRejectionGate:
    """
    Evaluates candidate setups against hard institutional disqualification gates.
    """
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        cfg = cfg or {}
        risk_cfg = cfg.get("risk", {})
        gate_cfg = cfg.get("rejection_gate", {})

        self.enabled = bool(gate_cfg.get("enabled", False))
        self.max_stop_pct = float(gate_cfg.get("max_stop_pct", risk_cfg.get("max_stop_pct", 5.0)))
        self.max_friction_r = float(gate_cfg.get("max_friction_r", risk_cfg.get("max_friction_r", 0.15)))
        self.min_rr_ratio = float(gate_cfg.get("min_rr_ratio", 1.0))
        self.earnings_buffer_days = int(gate_cfg.get("earnings_buffer_days", 3))
        self.enforce_earnings_check = bool(gate_cfg.get("enforce_earnings_check", True))
        self.enforce_anti_chase = bool(gate_cfg.get("enforce_anti_chase", True))
        self.max_extension_pct = float(gate_cfg.get("max_extension_pct", 1.0))
        self.max_failed_breakouts = gate_cfg.get("max_failed_breakouts_in_base", None)

        self.friction_engine = MarketFrictionEngine(cfg)

    def evaluate_setup(self, setup: Setup) -> RejectionDecision:
        """
        Runs comprehensive trade rejection checks on a single Setup candidate.
        """
        if not self.enabled:
            return RejectionDecision(approved=True, details={})
        # 1. Missing Structural Stop / Non-Positive Prices (Rule 1.3: No synthetic fallbacks)
        if setup.entry is None or setup.stop is None or setup.entry <= 0 or setup.stop <= 0:
            return RejectionDecision(
                approved=False,
                rejection_code="MISSING_STRUCTURAL_STOP",
                reason="MISSING_STRUCTURAL_STOP: Setup lacks positive structural entry or stop price.",
                details={"entry": setup.entry, "stop": setup.stop}
            )

        if setup.entry == setup.stop:
            return RejectionDecision(
                approved=False,
                rejection_code="ZERO_RISK_COLLAPSE",
                reason="ZERO_RISK_COLLAPSE: Entry and stop prices are identical.",
                details={"entry": setup.entry, "stop": setup.stop}
            )

        per_share_risk = abs(setup.entry - setup.stop)
        risk_pct = (per_share_risk / setup.entry) * 100.0

        # 2. Structural Risk Exceeded (> 5.0%)
        if risk_pct > (self.max_stop_pct + 1e-4):
            return RejectionDecision(
                approved=False,
                rejection_code="STRUCTURAL_RISK_EXCEEDED",
                reason=f"STRUCTURAL_RISK_EXCEEDED: Risk {risk_pct:.2f}% exceeds maximum limit {self.max_stop_pct:.2f}%.",
                details={"risk_pct": risk_pct, "max_stop_pct": self.max_stop_pct}
            )

        # 3. Unfavorable Payoff (Distance to TP1 < 1.0 * Risk)
        if setup.tp1 is not None and setup.tp1 > 0:
            tp1_distance = abs(setup.tp1 - setup.entry)
            rr_ratio = tp1_distance / per_share_risk
            if rr_ratio < (self.min_rr_ratio - 1e-4):
                return RejectionDecision(
                    approved=False,
                    rejection_code="UNFAVORABLE_PAYOFF",
                    reason=f"UNFAVORABLE_PAYOFF: Reward:Risk ratio {rr_ratio:.2f} is below minimum {self.min_rr_ratio:.2f}R.",
                    details={"rr_ratio": rr_ratio, "min_rr": self.min_rr_ratio}
                )

        # 4. Imminent Earnings Risk (<= 3 sessions)
        if self.enforce_earnings_check:
            days_earnings = getattr(setup, 'days_to_earnings', None)
            earnings_warning = getattr(setup, 'earnings_warning', False)
            if earnings_warning or (days_earnings is not None and 0 <= days_earnings <= self.earnings_buffer_days):
                return RejectionDecision(
                    approved=False,
                    rejection_code="IMMINENT_EARNINGS",
                    reason=f"IMMINENT_EARNINGS: High-volatility earnings event within {days_earnings if days_earnings is not None else '<=3'} sessions.",
                    details={"days_to_earnings": days_earnings}
                )

        # 5. Excessive Friction (> 0.15R)
        friction_breakdown = self.friction_engine.calculate_round_trip_friction(
            symbol=setup.symbol,
            entry=setup.entry,
            stop=setup.stop,
            shares=100.0,  # normalized shares
            atr=setup.atr
        )

        friction_r = setup.friction_r if setup.friction_r is not None else friction_breakdown.friction_r
        if friction_r > (self.max_friction_r + 1e-4):
            return RejectionDecision(
                approved=False,
                rejection_code="FRICTION_EXCESSIVE",
                reason=f"FRICTION_EXCESSIVE: Round-trip friction {friction_r:.3f}R exceeds maximum threshold {self.max_friction_r:.2f}R.",
                details={"friction_r": friction_r, "max_friction_r": self.max_friction_r}
            )

        # 6. Anti-Chase Extension Control (Disqualify late chase > max tolerance)
        if self.enforce_anti_chase and setup.state in (State.BUY, State.SELL):
            curr_p = setup.current_price if setup.current_price is not None else setup.entry
            if curr_p is not None and curr_p > 0:
                target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.state == State.BUY else -1)
                pivot = setup.breakout_level or (setup.resistance if target_dir == 1 else setup.support) or setup.entry
                if pivot is not None and pivot > 0:
                    max_tol = self.max_extension_pct
                    if setup.atr is not None and setup.atr > 0:
                        atr_tol = (0.5 * setup.atr / pivot) * 100.0
                        max_tol = min(self.max_extension_pct, atr_tol)

                    if target_dir == 1 and curr_p > pivot:
                        ext_pct = (curr_p - pivot) / pivot * 100.0
                        if ext_pct > (max_tol + 1e-4):
                            return RejectionDecision(
                                approved=False,
                                rejection_code="LATE_CHASE",
                                reason=f"LATE_CHASE: Price extension {ext_pct:.2f}% exceeds tolerance {max_tol:.2f}%.",
                                details={"extension_pct": round(ext_pct, 2), "max_allowed": round(max_tol, 2)}
                            )
                    elif target_dir == -1 and curr_p < pivot:
                        ext_pct = (pivot - curr_p) / pivot * 100.0
                        if ext_pct > (max_tol + 1e-4):
                            return RejectionDecision(
                                approved=False,
                                rejection_code="LATE_CHASE",
                                reason=f"LATE_CHASE: Price extension {ext_pct:.2f}% exceeds tolerance {max_tol:.2f}%.",
                                details={"extension_pct": round(ext_pct, 2), "max_allowed": round(max_tol, 2)}
                            )

        # 7. Trap Detection & Base Piercing Memory
        if self.max_failed_breakouts is not None:
            failed_count = getattr(setup, "failed_breakouts_in_base", 0)
            if failed_count > self.max_failed_breakouts:
                return RejectionDecision(
                    approved=False,
                    rejection_code="BASE_TRAP_CHURN",
                    reason=f"BASE_TRAP_CHURN: {failed_count} failed breakouts in base exceeds limit {self.max_failed_breakouts} (high trap risk).",
                    details={"failed_breakouts": failed_count, "max_allowed": self.max_failed_breakouts}
                )

        return RejectionDecision(
            approved=True,
            rejection_code=None,
            reason=None,
            details={
                "risk_pct": round(risk_pct, 2),
                "friction_r": round(float(friction_r), 3),
                "market": friction_breakdown.market
            }
        )

    def filter_setups(
        self, setups: List[Setup]
    ) -> Tuple[List[Setup], List[Tuple[Setup, RejectionDecision]]]:
        """Partitions candidate setups into approved and rejected sets."""
        approved: List[Setup] = []
        rejected: List[Tuple[Setup, RejectionDecision]] = []

        for s in setups:
            decision = self.evaluate_setup(s)
            if decision.approved:
                approved.append(s)
            else:
                rejected.append((s, decision))

        return approved, rejected
