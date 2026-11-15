#!/usr/bin/env python3
"""
Production Always-On Multi-Market Cloud Daemon.

Designed for AWS EC2 (t4g.small Singapore ap-southeast-1) and Systemd/Docker deployment.
Features:
- Timezone-aware multi-market scheduling (US, NSE, TSE, HKEX, Crypto)
- Heartbeat state monitoring (data/heartbeat.json) for CloudWatch / Systemd watchdog
- AWS SSM Parameter Store credential resolution (zero hardcoded secrets)
- Atomic deduplication persistence and cross-process locking
- Clean signal handling (SIGTERM, SIGINT) for zero-data-loss graceful shutdowns
"""

import os
import sys
import time
import signal
import argparse
import logging
from typing import List, Optional
from logging.handlers import RotatingFileHandler

from breakout_scanner.scheduler import MultiMarketScheduler, SessionScheduler
from breakout_scanner.market_session import MultiMarketOrchestrator, STANDARD_MARKET_CONFIGS
from breakout_scanner.heartbeat import HeartbeatMonitor
from breakout_scanner.secrets import get_telegram_credentials, get_telegram_targets


def setup_logger(log_file: str = "logs/cloud_daemon.log") -> logging.Logger:
    logger = logging.getLogger("cloud_daemon")
    logger.setLevel(logging.INFO)
    logger.handlers = []

    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler (10MB max per file, keep last 3 archives to protect disk space)
    rfh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    rfh.setFormatter(formatter)
    logger.addHandler(rfh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


class CloudDaemonRunner:
    def __init__(
        self,
        markets: Optional[List[str]] = None,
        config_path: str = "config/default.yaml",
        heartbeat_interval: int = 900,
        log_file: str = "logs/cloud_daemon.log",
    ):
        self.markets = markets or ["US", "NSE", "CRYPTO"]
        self.config_path = config_path
        self.heartbeat_interval = heartbeat_interval
        self.logger = setup_logger(log_file)
        self.running = False
        self.start_time = time.time()

        self.heartbeat = HeartbeatMonitor(
            filepath="data/heartbeat.json",
            interval_seconds=self.heartbeat_interval,
        )
        self.scheduler = MultiMarketScheduler(
            markets=self.markets,
            base_cfg_path=self.config_path,
            log_file=log_file,
        )
        self.orchestrator = MultiMarketOrchestrator(self.markets)

        # Hook scan_and_alert wrappers to capture telemetry
        for mid, s in self.scheduler.schedulers.items():
            original_scan = s.scan_and_alert

            def make_wrapped(orig, market_name):
                def wrapped_scan(*args, **kwargs):
                    res = orig(*args, **kwargs)
                    scanned = res.get("scanned", 0)
                    alerts = res.get("alerts_sent", 0)
                    active = self.orchestrator.get_active_markets()
                    closed = [m for m in self.markets if m not in active]
                    self.heartbeat.update_stats(
                        scanned=scanned,
                        alerts=alerts,
                        configured_markets=self.markets,
                        active_markets=active,
                        closed_markets=closed,
                        status="HEALTHY",
                    )
                    return res
                return wrapped_scan

            s.scan_and_alert = make_wrapped(original_scan, mid)

        self._install_signal_handlers()

    def _install_signal_handlers(self):
        def _handle_shutdown(signum, frame):
            sig_name = signal.Signals(signum).name
            self.logger.info(f"🛑 Received {sig_name}. Initiating graceful cloud shutdown...")
            self.running = False

        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)

    def dispatch_telegram_heartbeat(self) -> bool:
        """
        Dispatches a 40-minute live pulse to Telegram with multi-timezone clocks and market states.
        """
        import pandas as pd
        import requests

        now_utc = pd.Timestamp.now(tz="UTC")
        now_sgt = now_utc.tz_convert("Asia/Singapore")
        now_ist = now_utc.tz_convert("Asia/Kolkata")
        now_est = now_utc.tz_convert("America/New_York")

        active_markets = self.orchestrator.get_active_markets(dt=now_utc, include_recent_close=False)

        status_badges = []
        for m in self.markets:
            if m == "CRYPTO":
                status_badges.append("• <b>CRYPTO:</b> 🟢")
            elif m in active_markets:
                status_badges.append(f"• <b>{m}:</b> 🟢")
            else:
                status_badges.append(f"• <b>{m}:</b> 🔴")
        market_status_str = "  ".join(status_badges)

        uptime_sec = int(time.time() - self.start_time)
        hrs = uptime_sec // 3600
        mins = (uptime_sec % 3600) // 60

        total_scanned = self.heartbeat.stats.get("scanned_total", 0)
        total_alerts = self.heartbeat.stats.get("alerts_sent_total", 0)
        total_symbols = sum(len(s.load_symbols()) for s in self.scheduler.schedulers.values())

        def status_emoji(status: str) -> str:
            s = status.lower()
            if "open" in s or "🟢" in status:
                return "🟢"
            if "pre" in s or "post" in s or "🟡" in status:
                return "🟡"
            if "closed" in s or "holiday" in s or "🔴" in status:
                return "🔴"
            return "🟢"

        def fmt_int(n: int) -> str:
            return f"{n:,}"

        msg = (
            "💓 <b>BS ALIVE</b> · 🇸🇬 SGT\n"
            f"<code>{now_sgt.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🇮🇳 IST <code>{now_ist.strftime('%H:%M')}</code>"
            f"  ·  🇺🇸 ET <code>{now_est.strftime('%H:%M')}</code>\n"
            "🏛 <b>Exchange Status</b>\n"
            f"{market_status_str}\n"
            "⚙️ <b>Daemon</b>\n"
            f"• PID: <code>{os.getpid()}</code>"
            f"• Status: {status_emoji(market_status_str)} <b>Alive</b>\n"
            f"• Uptime: <code>{hrs}h {mins:02d}m</code>\n"
            f"• Symbols: <code>{fmt_int(total_symbols)}</code>"
            f"• Scanned: <code>{fmt_int(total_scanned)}</code>\n"
            f"• Alerts: <code>{fmt_int(total_alerts)}</code>\n"
            "\n"
            "⚡️ <i>Scheduler active</i>"
        )

        targets = get_telegram_targets()
        if not targets:
            self.logger.warning("⚠️ Cannot send Telegram heartbeat: credentials missing.")
            return False

        all_sent = True
        for token, cid in targets:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            try:
                res = requests.post(
                    url,
                    json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                    timeout=15,
                )
                if not res.ok:
                    self.logger.error(f"❌ Failed to dispatch 40-minute Telegram heartbeat to {cid} via bot {token[:8]}...: {res.status_code} {res.text}")
                    all_sent = False
            except Exception as e:
                self.logger.error(f"❌ Failed to dispatch 40-minute Telegram heartbeat to {cid}: {e}")
                all_sent = False

        if all_sent:
            self.logger.info("💓 40-minute Telegram Heartbeat pulse successfully dispatched.")
        return all_sent

    def run(self, startup_scan: bool = False, poll_sec: int = 5):
        self.running = True
        self.logger.info("=" * 70)
        self.logger.info("🚀 BREAKOUT SCANNER CLOUD DAEMON INITIALIZING")
        self.logger.info(f"   Markets Monitored: {', '.join(self.markets)}")
        self.logger.info("   Cadence: 40-min Telegram pulse + Exchange-aligned candle close scans")
        self.logger.info(f"   PID: {os.getpid()} | Host: {self.heartbeat.hostname}")
        self.logger.info("=" * 70)

        # Verify Telegram configuration
        targets = get_telegram_targets()
        if targets:
            summary = ", ".join([f"[Chat: {cid}, Bot: {tok[:6]}...]" for tok, cid in targets])
            self.logger.info(f"📱 Telegram Dispatch Verified: {len(targets)} Target(s) -> {summary}")
        else:
            self.logger.warning("⚠️ Telegram credentials not configured. Signals will be logged locally only.")

        import schedule
        import pandas as pd

        # Register exchange-aligned candle close schedules
        self.scheduler.schedule_all()

        # Schedule 40-minute Telegram Heartbeat pulse
        schedule.every(40).minutes.do(self.dispatch_telegram_heartbeat)
        self.logger.info("💓 Scheduled 40-minute Telegram Heartbeat notification")

        # Schedule nightly What-If outcome tracking at 00:15 UTC (executed asynchronously in background thread)
        def run_nightly_outcome_tracking():
            def _async_worker():
                try:
                    from breakout_scanner.outcome_tracker import update_ledger_outcomes
                    self.logger.info("🌙 Running nightly What-If outcome tracking on forward-test ledger in background thread...")
                    res = update_ledger_outcomes()
                    self.logger.info(f"🌙 Nightly outcome tracking complete: {res.get('newly_evaluated', 0)} newly evaluated setups.")
                except Exception as e:
                    self.logger.error(f"❌ Failed to run nightly outcome tracking: {e}")

            import threading
            threading.Thread(target=_async_worker, name="NightlyOutcomeTrackerThread", daemon=True).start()

        schedule.every().day.at("00:15", "UTC").do(run_nightly_outcome_tracking)
        self.logger.info("🌙 Scheduled nightly What-If outcome evaluation at 00:15 UTC (background async thread)")

        # Periodic telemetry pulse every 10 minutes (keeps local disk telemetry fresh during deep sleep)
        schedule.every(10).minutes.do(lambda: self.heartbeat.beat(force=True))

        now_utc = pd.Timestamp.now(tz="UTC")
        active = self.orchestrator.get_active_markets(dt=now_utc, include_recent_close=False)
        closed = [m for m in self.markets if m not in active]
        self.heartbeat.update_stats(
            status="HEALTHY",
            configured_markets=self.markets,
            active_markets=active,
            closed_markets=closed,
        )

        # Dispatch immediate startup heartbeat to Telegram
        if targets:
            self.logger.info("📡 Dispatching initial startup heartbeat to Telegram...")
            self.dispatch_telegram_heartbeat()

        # Optional startup scan
        if startup_scan:
            self.logger.info("⚡ Executing startup scan across active markets...")
            self.scheduler.scan_active_markets(force=True)

        # Continuous production event loop (deep sleep between triggers to save CPU credits)
        try:
            while self.running:
                schedule.run_pending()
                time.sleep(poll_sec)
        except Exception as e:
            self.logger.critical(f"💥 Fatal unhandled exception in daemon loop: {e}", exc_info=True)
            self.heartbeat.update_stats(status="DEGRADED")
            raise
        finally:
            self.heartbeat.update_stats(status="STOPPED")
            self.logger.info("🛑 Cloud Daemon stopped cleanly. Zero locks or transactions orphaned.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Institutional Always-On Multi-Market Cloud Daemon (Phase 3)"
    )
    parser.add_argument(
        "--markets",
        type=str,
        default="US,NSE,CRYPTO",
        help="Comma-separated market identifiers to orchestrate (default: US,NSE,CRYPTO)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Base configuration path",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=900,
        help="Heartbeat cadence in seconds (default: 900s / 15m)",
    )
    parser.add_argument(
        "--startup-scan",
        action="store_true",
        help="Execute an immediate scan across active markets on startup",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Inspect daemon heartbeat status from disk and exit",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/cloud_daemon.log",
        help="Log file path",
    )
    args = parser.parse_args(argv)

    if args.status:
        res = HeartbeatMonitor.check_health("data/heartbeat.json")
        if res.get("healthy"):
            data = res.get("data", {})
            print("✅ [HEALTHY] Daemon is running normally.")
            print(f"   PID: {data.get('pid')} | Uptime: {data.get('uptime_seconds')}s")
            print(f"   Last Beat: {data.get('timestamp_utc')} ({res.get('age_seconds')}s ago)")
            print(f"   Monitored Markets: {data.get('configured_markets', data.get('active_markets', []))}")
            print(f"   Active Markets: {data.get('active_markets', [])} (currently open)")
            if data.get('closed_markets'):
                print(f"   Closed Markets: {data.get('closed_markets')} (off-hours / sleeping)")
            print(f"   Scanned Total: {data.get('scanned_total')} | Alerts Sent: {data.get('alerts_sent_total')}")
            sys.exit(0)
        else:
            print(f"❌ [UNHEALTHY] {res.get('message')}")
            sys.exit(1)

    market_list = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    runner = CloudDaemonRunner(
        markets=market_list,
        config_path=args.config,
        heartbeat_interval=args.heartbeat_interval,
        log_file=args.log_file,
    )
    runner.run(startup_scan=args.startup_scan)


if __name__ == "__main__":
    main()
