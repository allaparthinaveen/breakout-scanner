"""
Cloud Health & Heartbeat Monitor.

Maintains an atomic health state on disk (data/heartbeat.json) and provides
vital telemetry for AWS CloudWatch heartbeat alarms, systemd watchdogs,
and Telegram operational heartbeat pings.
"""

import os
import time
import json
import socket
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("heartbeat")


class HeartbeatMonitor:
    def __init__(self, filepath: str = "data/heartbeat.json", interval_seconds: int = 900):
        self.filepath = filepath
        self.interval_seconds = interval_seconds
        self.start_time = time.time()
        self.last_beat_time = 0.0
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.stats: Dict[str, Any] = {
            "scanned_total": 0,
            "alerts_sent_total": 0,
            "last_scan_utc": None,
            "configured_markets": [],
            "active_markets": [],
            "closed_markets": [],
            "status": "STARTING",
        }
        self._ensure_dir()

    def _ensure_dir(self):
        d = os.path.dirname(self.filepath)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def update_stats(
        self,
        scanned: int = 0,
        alerts: int = 0,
        configured_markets: Optional[list] = None,
        active_markets: Optional[list] = None,
        closed_markets: Optional[list] = None,
        status: str = "HEALTHY",
    ):
        """Updates internal telemetry metrics."""
        self.stats["scanned_total"] += scanned
        self.stats["alerts_sent_total"] += alerts
        self.stats["last_scan_utc"] = datetime.now(timezone.utc).isoformat()
        if configured_markets is not None:
            self.stats["configured_markets"] = configured_markets
        if active_markets is not None:
            self.stats["active_markets"] = active_markets
        if closed_markets is not None:
            self.stats["closed_markets"] = closed_markets
        self.stats["status"] = status
        self.beat(force=True)

    def beat(self, force: bool = False) -> bool:
        """
        Emits a heartbeat if force=True or if interval_seconds have elapsed.
        Writes atomically to data/heartbeat.json via temporary file replace.
        """
        now = time.time()
        if not force and (now - self.last_beat_time < self.interval_seconds):
            return False

        uptime = round(now - self.start_time, 1)
        heartbeat_data = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": uptime,
            "pid": self.pid,
            "hostname": self.hostname,
            "status": self.stats["status"],
            "configured_markets": self.stats.get("configured_markets", []),
            "active_markets": self.stats["active_markets"],
            "closed_markets": self.stats.get("closed_markets", []),
            "scanned_total": self.stats["scanned_total"],
            "alerts_sent_total": self.stats["alerts_sent_total"],
            "last_scan_utc": self.stats["last_scan_utc"],
        }

        try:
            temp_path = f"{self.filepath}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(heartbeat_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.filepath)
            self.last_beat_time = now
            logger.debug(f"💓 Heartbeat emitted: uptime={uptime}s, status={self.stats['status']}")
            return True
        except Exception as e:
            logger.error(f"Failed to write heartbeat to {self.filepath}: {e}")
            return False

    @classmethod
    def check_health(cls, filepath: str = "data/heartbeat.json", max_stale_seconds: int = 4200) -> dict:
        """
        Inspects existing heartbeat file to determine daemon health.
        Used by CloudWatch alarms or health-check CLI tools.
        """
        if not os.path.exists(filepath):
            return {
                "healthy": False,
                "status": "MISSING",
                "message": f"Heartbeat file {filepath} does not exist.",
            }

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            ts_str = data.get("timestamp_utc")
            if not ts_str:
                return {"healthy": False, "status": "CORRUPT", "message": "Missing timestamp_utc"}

            ts = datetime.fromisoformat(ts_str)
            age = (datetime.now(timezone.utc) - ts).total_seconds()

            if age > max_stale_seconds:
                return {
                    "healthy": False,
                    "status": "STALE",
                    "age_seconds": round(age, 1),
                    "message": f"Daemon heartbeat is stale ({round(age, 1)}s > {max_stale_seconds}s limit)",
                    "data": data,
                }

            return {
                "healthy": True,
                "status": data.get("status", "HEALTHY"),
                "age_seconds": round(age, 1),
                "data": data,
            }
        except Exception as e:
            return {"healthy": False, "status": "ERROR", "message": str(e)}
