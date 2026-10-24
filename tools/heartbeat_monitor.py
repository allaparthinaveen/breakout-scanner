"""
Phase 5 — Heartbeat Monitor & Candidate Drought Alert
=======================================================
Instruments zero-candidate heartbeat detection for consecutive scan sessions.
When every session in a rolling window produces zero READY/BREAKOUT candidates,
a Telegram alert is fired automatically.

Run standalone or import into scheduler:
    python3 tools/heartbeat_monitor.py --sessions 3 --notify

Integrated into scheduler via telemetry data (no live market data needed).
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from breakout_scanner.telemetry import ScanMetricsLogger, DEFAULT_TELEMETRY_DIR

HEARTBEAT_THRESHOLDS = {
    "consecutive_zero_candidate_sessions": 3,   # Fire alert after N consecutive dry sessions
    "min_expected_candidates_healthy": 1,       # Minimum candidates per session to be considered healthy
    "max_scan_duration_seconds": 300,           # Alert if scan takes longer than this
}


class HeartbeatMonitor:
    """
    Checks recent scan telemetry for consecutive zero-candidate sessions
    and emits structured alert payloads.
    """

    def __init__(
        self,
        telemetry_dir: str = DEFAULT_TELEMETRY_DIR,
        consecutive_threshold: int = 3,
    ):
        self.logger = ScanMetricsLogger(output_dir=telemetry_dir)
        self.consecutive_threshold = consecutive_threshold

    def check_candidate_drought(self, sessions: int = 10) -> Dict:
        """
        Inspect the last N scan sessions. If every session has 0 candidates,
        return an alert payload. Otherwise, return a healthy status.
        """
        history = self.logger.get_history(limit=sessions)
        if not history:
            return {
                "status": "UNKNOWN",
                "alert": False,
                "message": "No scan history found. Has the scanner run yet?",
                "sessions_checked": 0,
                "consecutive_zeros": 0,
            }

        # Count trailing consecutive zeros from newest → oldest
        consecutive_zeros = 0
        for record in reversed(history):
            total_candidates = (
                record.get("candidates", 0) +
                record.get("ready_count", 0) +
                record.get("breakout_count", 0)
            )
            if total_candidates == 0:
                consecutive_zeros += 1
            else:
                break

        alert_triggered = consecutive_zeros >= self.consecutive_threshold
        last_session = history[-1] if history else {}
        last_ts = last_session.get("timestamp", "N/A")
        last_market = last_session.get("market", "N/A")
        last_scanned = last_session.get("total_scanned", 0)

        if alert_triggered:
            status = "ALERT_DROUGHT"
            message = (
                f"⚠️ CANDIDATE DROUGHT DETECTED: {consecutive_zeros} consecutive sessions with 0 candidates "
                f"(threshold: {self.consecutive_threshold}). "
                f"Last scan: {last_ts} | Market: {last_market} | Symbols scanned: {last_scanned}."
            )
        elif consecutive_zeros > 0:
            status = "WARNING_LOW"
            message = (
                f"⚡ Low signal warning: {consecutive_zeros} consecutive zero-candidate sessions "
                f"(threshold: {self.consecutive_threshold}). Monitoring."
            )
        else:
            status = "HEALTHY"
            cands = history[-1].get("candidates", 0) if history else 0
            message = f"✅ System healthy. Last session: {cands} candidate(s) detected at {last_ts}."

        return {
            "status": status,
            "alert": alert_triggered,
            "message": message,
            "sessions_checked": len(history),
            "consecutive_zeros": consecutive_zeros,
            "consecutive_threshold": self.consecutive_threshold,
            "last_timestamp": last_ts,
            "last_market": last_market,
            "last_scanned": last_scanned,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def send_telegram_alert(self, alert_payload: Dict) -> bool:
        """Send alert via Telegram if credentials are available."""
        import os
        token = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("⚠️ Telegram credentials not configured. Skipping alert.")
            return False

        try:
            import requests
            message = (
                f"🛡️ <b>BREAKOUT SCANNER HEARTBEAT ALERT</b>\n\n"
                f"Status: <code>{alert_payload['status']}</code>\n"
                f"{alert_payload['message']}\n\n"
                f"Consecutive zero sessions: <code>{alert_payload['consecutive_zeros']}</code>\n"
                f"Sessions checked: <code>{alert_payload['sessions_checked']}</code>\n"
                f"Checked at: <code>{alert_payload['checked_at']}</code>"
            )
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"❌ Failed to send Telegram heartbeat alert: {e}")
            return False


def run_heartbeat_check(
    sessions: int = 10,
    consecutive_threshold: int = 3,
    notify: bool = False,
    verbose: bool = True,
) -> Dict:
    """
    Convenience function for running a heartbeat check.
    Returns the alert payload dict.
    """
    monitor = HeartbeatMonitor(consecutive_threshold=consecutive_threshold)
    payload = monitor.check_candidate_drought(sessions=sessions)

    if verbose:
        print(f"\n🫀 HEARTBEAT CHECK — {payload['checked_at']}")
        print(f"   Status:               {payload['status']}")
        print(f"   Sessions Checked:     {payload['sessions_checked']}")
        print(f"   Consecutive Zeros:    {payload['consecutive_zeros']} / {payload['consecutive_threshold']} threshold")
        print(f"   Last Scan:            {payload['last_timestamp']} ({payload['last_market'].upper()})")
        print(f"   Message:              {payload['message']}")

    if notify and payload["alert"]:
        monitor.send_telegram_alert(payload)

    return payload


def main():
    parser = argparse.ArgumentParser(description="Breakout Scanner Heartbeat Monitor")
    parser.add_argument("--sessions", type=int, default=10,
                        help="Number of recent sessions to inspect (default: 10)")
    parser.add_argument("--threshold", type=int, default=3,
                        help="Consecutive zero-candidate sessions before alert fires (default: 3)")
    parser.add_argument("--notify", action="store_true",
                        help="Send Telegram alert if drought is detected")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON payload")
    args = parser.parse_args()

    payload = run_heartbeat_check(
        sessions=args.sessions,
        consecutive_threshold=args.threshold,
        notify=args.notify,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(payload, indent=2))

    # Exit code 1 on alert so CI/cron can detect failures
    sys.exit(1 if payload["alert"] else 0)


if __name__ == "__main__":
    main()
