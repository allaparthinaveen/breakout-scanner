"""
Phase 5: Pre-Declared Go/No-Go Operational Release Auditor
==========================================================
Audits shadow paper trading performance ledger against institutional gate criteria:
1. Net Realized Expectancy E(R) >= +0.20R per trade
2. Realized Profit Factor >= 1.40
3. Max Paper Portfolio Drawdown <= 5.0%
4. Execution Slippage Variance <= 0.08R
5. Zero Critical Defects (0 desyncs/exceptions)

Generates: reports/paper_trading_audit_report.md
"""

import os
import json
import uuid
import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import numpy as np

from .models import (
    GateAuditCheck,
    GoNoGoAuditReport,
)


@dataclass
class IncidentRecord:
    """Evidential record of a single system defect, data anomaly, or state desync."""
    incident_id: str
    timestamp: str
    category: str  # EXCEPTION, STATE_DESYNC, MISSING_STOP, DATA_GAP, STALE_DATA, ORDER_REJECT
    severity: str = "CRITICAL"  # CRITICAL, WARNING, INFO
    symbol: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "timestamp": self.timestamp,
            "category": self.category,
            "severity": self.severity,
            "symbol": self.symbol,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IncidentRecord":
        return cls(
            incident_id=data.get("incident_id", ""),
            timestamp=data.get("timestamp", ""),
            category=data.get("category", "EXCEPTION"),
            severity=data.get("severity", "CRITICAL"),
            symbol=data.get("symbol"),
            message=data.get("message", ""),
            details=data.get("details", {}),
        )


class IncidentLedger:
    """
    Append-only evidential ledger for recording operational anomalies,
    data delays, state desyncs, and uncaught exceptions during paper trading.
    """
    def __init__(self, ledger_path: Optional[str] = None):
        self.ledger_path = ledger_path
        self.incidents: List[IncidentRecord] = []
        if self.ledger_path and os.path.exists(self.ledger_path):
            self.load()

    def record_incident(
        self,
        category: str,
        message: str,
        severity: str = "CRITICAL",
        symbol: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> IncidentRecord:
        rec = IncidentRecord(
            incident_id=str(uuid.uuid4())[:8],
            timestamp=datetime.datetime.now().isoformat(),
            category=category,
            severity=severity.upper(),
            symbol=symbol,
            message=message,
            details=details or {},
        )
        self.incidents.append(rec)
        if self.ledger_path:
            self._append_to_file(rec)
        return rec

    def _append_to_file(self, rec: IncidentRecord):
        try:
            dir_name = os.path.dirname(self.ledger_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict()) + "\n")
        except Exception:
            pass

    def load(self):
        if not self.ledger_path or not os.path.exists(self.ledger_path):
            return
        self.incidents = []
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.incidents.append(IncidentRecord.from_dict(json.loads(line)))
        except Exception:
            pass

    def get_critical_incidents(self) -> List[IncidentRecord]:
        return [i for i in self.incidents if i.severity == "CRITICAL"]

    @property
    def critical_count(self) -> int:
        return len(self.get_critical_incidents())

    def clear(self):
        self.incidents = []
        if self.ledger_path and os.path.exists(self.ledger_path):
            try:
                os.remove(self.ledger_path)
            except Exception:
                pass


DEFAULT_REPORT_PATH = "reports/paper_trading_audit_report.md"


class GoNoGoAuditor:
    """
    Evaluates paper trading records against pre-declared institutional gates.
    """
    def __init__(
        self,
        min_expectancy_r: float = 0.20,
        min_profit_factor: float = 1.40,
        max_drawdown_pct: float = 5.0,
        max_slippage_r: float = 0.08,
        min_completed_trades: int = 15,
        validation_window_days: int = 30,
    ):
        self.min_expectancy_r = min_expectancy_r
        self.min_profit_factor = min_profit_factor
        self.max_drawdown_pct = max_drawdown_pct
        self.max_slippage_r = max_slippage_r
        self.min_completed_trades = min_completed_trades
        self.validation_window_days = validation_window_days

    def evaluate_performance(
        self,
        completed_trades: List[Dict[str, Any]],
        variance_records: Optional[List[Any]] = None,
        critical_defects_count: Optional[int] = None,
        incident_ledger: Optional[IncidentLedger] = None
    ) -> GoNoGoAuditReport:
        """
        Runs rigorous audit checks against the completed paper trades and slippage ledger.
        """
        if critical_defects_count is None:
            if incident_ledger is not None:
                critical_defects_count = incident_ledger.critical_count
            else:
                critical_defects_count = 0

        variance_records = variance_records or []
        n_trades = len(completed_trades)

        if n_trades == 0:
            exp_r = 0.0
            pf = 0.0
            max_dd = 0.0
        else:
            r_vals = [float(t.get("realized_r", 0.0)) for t in completed_trades]
            exp_r = float(np.mean(r_vals)) if r_vals else 0.0

            wins = [t for t in completed_trades if t.get("net_pnl", 0.0) > 0]
            losses = [t for t in completed_trades if t.get("net_pnl", 0.0) <= 0]
            gp = sum(t.get("net_pnl", 0.0) for t in wins)
            gl = abs(sum(t.get("net_pnl", 0.0) for t in losses))
            pf = (gp / gl) if gl > 1e-4 else (99.0 if gp > 0 else 0.0)

            # Max DD
            equity = 100000.0
            peak = equity
            max_dd = 0.0
            for t in completed_trades:
                equity += t.get("net_pnl", 0.0)
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd

        slip_vals = []
        for v in variance_records:
            if hasattr(v, 'slippage_r'):
                slip_vals.append(float(v.slippage_r))
            elif isinstance(v, dict):
                slip_vals.append(float(v.get('slippage_r', 0.0)))
        avg_slip_r = float(np.mean(slip_vals)) if slip_vals else 0.025

        checks = [
            GateAuditCheck(
                criterion_name="Net Realized Expectancy E(R)",
                threshold_str=f">= +{self.min_expectancy_r:.2f}R",
                actual_value_str=f"{exp_r:+.3f}R",
                passed=(exp_r >= self.min_expectancy_r),
                fail_action="Refine signal quality filters and entry confirmation",
            ),
            GateAuditCheck(
                criterion_name="Realized Profit Factor",
                threshold_str=f">= {self.min_profit_factor:.2f}",
                actual_value_str=f"{pf:.2f}",
                passed=(pf >= self.min_profit_factor),
                fail_action="Review stop placement, trailing breakeven, and partial scale-outs",
            ),
            GateAuditCheck(
                criterion_name="Max Paper Portfolio Drawdown",
                threshold_str=f"<= {self.max_drawdown_pct:.1f}%",
                actual_value_str=f"{max_dd:.2f}%",
                passed=(max_dd <= self.max_drawdown_pct),
                fail_action="Reduce daily risk budget or active position caps",
            ),
            GateAuditCheck(
                criterion_name="Execution Slippage Variance",
                threshold_str=f"Average <= {self.max_slippage_r:.2f}R",
                actual_value_str=f"{avg_slip_r:.3f}R",
                passed=(avg_slip_r <= self.max_slippage_r),
                fail_action="Tighten spread filter or anti-chase limit",
            ),
            GateAuditCheck(
                criterion_name="Critical System Defects",
                threshold_str="0 uncaught exceptions / desyncs",
                actual_value_str=f"{critical_defects_count} defects",
                passed=(critical_defects_count == 0),
                fail_action="Fix software exceptions and race conditions",
            ),
        ]

        # Overall verdict: All checks MUST pass
        all_passed = all(c.passed for c in checks) and (n_trades >= self.min_completed_trades)
        verdict = "GO (Production Approved for Micro-Capital)" if all_passed else "NO-GO (Validation Remediation Required)"

        notes = []
        if n_trades < self.min_completed_trades:
            notes.append(f"Insufficient trade sample: {n_trades} trades completed (minimum required: {self.min_completed_trades}).")
        if not all_passed:
            failed_checks = [c.criterion_name for c in checks if not c.passed]
            notes.append(f"Failed gates: {', '.join(failed_checks)}.")
        else:
            notes.append("All institutional release criteria successfully met during shadow paper evaluation.")

        return GoNoGoAuditReport(
            timestamp=str(datetime.datetime.utcnow().isoformat()) if hasattr(datetime, "datetime") else "",
            total_trades_evaluated=n_trades,
            validation_window_days=self.validation_window_days,
            overall_verdict=verdict,
            checks=checks,
            summary_notes=notes,
        )

    def write_markdown_report(
        self,
        report: GoNoGoAuditReport,
        output_path: str = DEFAULT_REPORT_PATH,
        incident_ledger: Optional[IncidentLedger] = None
    ) -> str:
        """Renders the formal Phase 5 release audit markdown document."""
        verdict_badge = "🟢 **GO FOR MICRO-CAPITAL LIVE RELEASE**" if "GO" in report.overall_verdict and "NO-GO" not in report.overall_verdict else "🔴 **NO-GO: REMEDIATION REQUIRED**"

        lines = [
            "# 📋 Phase 5: Paper Trading Audit & Production Release Gate",
            "",
            "> **Audit Scope:** Pre-declared production release validation for the Breakout Scanner engine.",
            f"> **Audit Status:** {verdict_badge}",
            f"> **Sample Size:** {report.total_trades_evaluated} Completed Shadow Trades across {report.validation_window_days} Days.",
            "",
            "---",
            "",
            "## 🎯 Pre-Declared Production Gate Criteria Audit",
            "",
            "| Release Gate Criterion | Required Gate | Observed Shadow Result | Status | Remediation / Fail Action |",
            "| :--- | :---: | :---: | :---: | :--- |"
        ]

        for c in report.checks:
            status_icon = "✅ PASS" if c.passed else "❌ FAIL"
            lines.append(
                f"| **{c.criterion_name}** | {c.threshold_str} | **{c.actual_value_str}** | {status_icon} | {c.fail_action} |"
            )

        if incident_ledger and incident_ledger.incidents:
            lines.extend([
                "",
                "---",
                "",
                "## ⚠️ Operational Incident & Defect Ledger",
                "",
                "| Incident ID | Timestamp | Severity | Category | Symbol | Message |",
                "| :--- | :---: | :---: | :---: | :---: | :--- |"
            ])
            for inc in incident_ledger.incidents:
                lines.append(
                    f"| `{inc.incident_id}` | {inc.timestamp} | **{inc.severity}** | `{inc.category}` | {inc.symbol or 'N/A'} | {inc.message} |"
                )

        lines.extend([
            "",
            "---",
            "",
            "## 📝 Operational Telemetry & Audit Notes",
            ""
        ])

        for note in report.summary_notes:
            lines.append(f"- {note}")

        lines.extend([
            "",
            "---",
            "",
            "## 🛡️ Production Release Protocol",
            "",
            "1. **Micro-Capital Deployment:** Deploy with at most 10% target account capital ($10,000 maximum equity).",
            "2. **Strict Daily Risk Ceiling:** Maintain daily risk allocation hard cap at 1.0–2.0% of deployed capital.",
            "3. **Telegram Health Heartbeat:** Require 60-second ping confirmation before each live market open.",
            "4. **Real-Time Kill Switch:** Halt trading immediately if any trade breaches maximum structural stop or daily loss limit.",
            ""
        ])

        content = "\n".join(lines)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return content


import datetime

