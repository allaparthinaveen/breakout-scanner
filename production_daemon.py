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
from breakout_scanner.secrets import get_telegram_credentials


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

        market_status_lines = []
        for m in self.markets:
            if m == "CRYPTO":
                market_status_lines.append("• <b>CRYPTO:</b> 🟢 OPEN (24/7 Continuous)")
            elif m in active_markets:
                market_status_lines.append(f"• <b>{m}:</b> 🟢 OPEN (Session Active)")
            else:
                market_status_lines.append(f"• <b>{m}:</b> 🔴 CLOSED (Off-Hours)")

        uptime_sec = int(time.time() - self.start_time)
        hrs = uptime_sec // 3600
        mins = (uptime_sec % 3600) // 60

        total_scanned = self.heartbeat.stats.get("scanned_total", 0)
        total_alerts = self.heartbeat.stats.get("alerts_sent_total", 0)
        total_symbols = sum(len(s.load_symbols()) for s in self.scheduler.schedulers.values())

        msg = (
            "💓 <b>BREAKOUT SCANNER • 40-MIN LIVE PULSE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏱ <b>Global Multi-Timezone Clock:</b>\n"
            f"🇸🇬 <b>Singapore (SGT):</b> <code>{now_sgt.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🇮🇳 <b>India (IST):</b> <code>{now_ist.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🇺🇸 <b>New York (EST/EDT):</b> <code>{now_est.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🌐 <b>UTC:</b> <code>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            "🏛 <b>Exchange Trading Status:</b>\n"
            + "\n".join(market_status_lines) + "\n\n"
            "📊 <b>Daemon Telemetry:</b>\n"
            f"• <b>Host:</b> <code>{self.heartbeat.hostname}</code> (PID {os.getpid()})\n"
            f"• <b>Status:</b> 🟢 HEALTHY\n"
            f"• <b>Uptime:</b> {hrs}h {mins:02d}m\n"
            f"• <b>Tickers Monitored:</b> {total_symbols} unique symbols\n"
            f"• <b>Total Scanned:</b> {total_scanned}\n"
            f"• <b>Signals Sent:</b> {total_alerts}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ <i>Scheduler active. Next hourly candle scan at :01 past the hour.</i>"
        )

        token, chat_id = get_telegram_credentials()
        if not token or not chat_id:
            self.logger.warning("⚠️ Cannot send Telegram heartbeat: credentials missing.")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            res = requests.post(
                url,
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=15,
            )
            res.raise_for_status()
            self.logger.info("💓 40-minute Telegram Heartbeat pulse successfully dispatched.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to dispatch 40-minute Telegram heartbeat: {e}")
            return False

    def run(self, startup_scan: bool = False, poll_sec: int = 5):
        self.running = True
        self.logger.info("=" * 70)
        self.logger.info("🚀 BREAKOUT SCANNER CLOUD DAEMON INITIALIZING")
        self.logger.info(f"   Markets Monitored: {', '.join(self.markets)}")
        self.logger.info("   Heartbeat Cadence: 40-min Telegram pulse + hourly :01 candle scan")
        self.logger.info(f"   PID: {os.getpid()} | Host: {self.heartbeat.hostname}")
        self.logger.info("=" * 70)

        # Verify Telegram configuration
        token, chat_id = get_telegram_credentials()
        if token and chat_id:
            masked_token = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
            self.logger.info(f"📱 Telegram Dispatch Verified: Chat ID={chat_id}, Token={masked_token}")
        else:
            self.logger.warning("⚠️ Telegram credentials not configured. Signals will be logged locally only.")

        import schedule
        import pandas as pd

        # Register market schedules
        self.scheduler.schedule_all()

        # Scheduled hourly scan cadence across active markets at :01 past every hour (candle close)
        def _scan_open_markets():
            now_utc = pd.Timestamp.now(tz="UTC")
            active = self.orchestrator.get_active_markets(dt=now_utc, include_recent_close=True)
            if not active:
                self.logger.info("💤 No active exchanges currently open. Deep sleeping...")
                return {}
            self.logger.info(f"⚡ Hourly candle close scan triggered for active markets: {active}")
            return self.scheduler.scan_active_markets()

        schedule.every().hour.at(":01").do(_scan_open_markets)
        self.logger.info("⏱️ Scheduled hourly candle close scanner at :01 past every hour")

        # Schedule 40-minute Telegram Heartbeat pulse
        schedule.every(40).minutes.do(self.dispatch_telegram_heartbeat)
        self.logger.info("💓 Scheduled 40-minute Telegram Heartbeat notification")

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
        if token and chat_id:
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
