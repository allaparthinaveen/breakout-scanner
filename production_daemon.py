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

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def is_env_truthy(val: Optional[str]) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("true", "1", "yes", "on", "t", "y")


def is_env_falsy(val: Optional[str]) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("false", "0", "no", "off", "f", "n")


def is_telegram_heartbeat_enabled() -> bool:
    """
    Checks whether Telegram heartbeat alerts are enabled from .env / os.environ.
    Defaults to True (BAU). Set ENABLE_HEARTBEAT_ALERT=false in .env to disable.
    """
    for key in (
        "ENABLE_HEARTBEAT_ALERT",
        "HEARTBEAT_ALERT_ENABLED",
        "ENABLE_TELEGRAM_HEARTBEAT",
        "TELEGRAM_HEARTBEAT_ENABLED",
        "ENABLE_HEARTBEAT",
    ):
        val = os.getenv(key)
        if val is not None:
            if is_env_falsy(val):
                return False
            if is_env_truthy(val):
                return True
    return True


def resolve_configured_markets(cli_markets: Optional[List[str]] = None) -> List[str]:
    """
    Resolves the list of active markets to monitor and scan from .env / os.environ.
    Supports both list variables (ENABLED_MARKETS, ACTIVE_MARKETS, MARKETS) and
    individual boolean switches (ENABLE_CRYPTO=false, ENABLE_NSE=true, etc.).
    Explicit cli_markets (if passed by caller) takes highest precedence.
    """
    if cli_markets is not None:
        return [m.strip().upper() for m in cli_markets if m.strip()]

    # 1. Determine base markets list from environment (.env)
    env_markets_str = os.getenv("ENABLED_MARKETS") or os.getenv("ACTIVE_MARKETS") or os.getenv("MARKETS")
    if env_markets_str and env_markets_str.strip():
        base = [m.strip().upper() for m in env_markets_str.split(",") if m.strip()]
    else:
        base = ["US", "NSE", "CRYPTO"]

    canonical = []
    alias_map = {
        "BTC": "CRYPTO",
        "NIFTY": "NSE",
        "INDIA": "NSE",
        "USA": "US",
        "SPY": "US",
        "HK": "HKEX",
        "HONGKONG": "HKEX",
        "JAPAN": "TSE",
        "TOKYO": "TSE",
    }
    for m in base:
        c_m = alias_map.get(m, m)
        if c_m not in canonical:
            canonical.append(c_m)

    # 2. Check individual boolean switches
    market_switch_map = {
        "CRYPTO": ["ENABLE_CRYPTO", "CRYPTO_ENABLED"],
        "NSE": ["ENABLE_NSE", "NSE_ENABLED"],
        "US": ["ENABLE_US", "US_ENABLED"],
        "HKEX": ["ENABLE_HK", "HK_ENABLED", "ENABLE_HKEX", "HKEX_ENABLED"],
        "TSE": ["ENABLE_JAPAN", "JAPAN_ENABLED", "ENABLE_TSE", "TSE_ENABLED"],
    }
    disable_switch_map = {
        "CRYPTO": ["DISABLE_CRYPTO"],
        "NSE": ["DISABLE_NSE"],
        "US": ["DISABLE_US"],
        "HKEX": ["DISABLE_HK", "DISABLE_HKEX"],
        "TSE": ["DISABLE_JAPAN", "DISABLE_TSE"],
    }

    final_markets = list(canonical)

    for mkt, enable_keys in market_switch_map.items():
        for k in enable_keys:
            val = os.getenv(k)
            if val is not None:
                if is_env_falsy(val) and mkt in final_markets:
                    final_markets.remove(mkt)
                elif is_env_truthy(val) and mkt not in final_markets:
                    final_markets.append(mkt)

    for mkt, disable_keys in disable_switch_map.items():
        for k in disable_keys:
            val = os.getenv(k)
            if val is not None:
                if is_env_truthy(val) and mkt in final_markets:
                    final_markets.remove(mkt)
                elif is_env_falsy(val) and mkt not in final_markets:
                    final_markets.append(mkt)

    return final_markets


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
        args: Optional[argparse.Namespace] = None,
    ):
        self.args = args or argparse.Namespace(test=False)
        self.markets = resolve_configured_markets(markets)
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
            f"• PID: <code>{os.getpid()}</code>\n"
            f"• Status: {status_emoji(market_status_str)} <b>Alive</b>\n"
            f"• Uptime: <code>{hrs}h {mins:02d}m</code>\n"
            f"• Symbols: <code>{fmt_int(total_symbols)}</code>\n"
            f"• Scanned: <code>{fmt_int(total_scanned)}</code>\n"
            f"• Alerts: <code>{fmt_int(total_alerts)}</code>\n"
            "\n"
            "⚡️ <i>Scheduler active</i>"
        )

        all_sent = True
        if not is_telegram_heartbeat_enabled():
            self.logger.info("🔕 Telegram heartbeat alert disabled via .env (ENABLE_HEARTBEAT_ALERT=false). Skipping Telegram dispatch.")
        else:
            targets = get_telegram_targets()
            if not targets:
                self.logger.warning("⚠️ Cannot send Telegram heartbeat: credentials missing.")
                all_sent = False
            else:
                for token, cid in targets:
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    t0 = time.time()
                    try:
                        res = requests.post(
                            url,
                            json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                            timeout=15,
                        )
                        lat_ms = (time.time() - t0) * 1000.0
                        try:
                            from breakout_scanner.run_logger import log_telegram_send, mask_token, mask_chat_id
                            log_telegram_send(
                                bot_label=mask_token(token),
                                chat_label=mask_chat_id(cid),
                                message_type="heartbeat",
                                chunk_count=1,
                                http_status=res.status_code,
                                latency_ms=lat_ms,
                                error_text=res.text if not res.ok else None,
                                market="ALL",
                            )
                        except Exception:
                            pass
                        if not res.ok:
                            self.logger.error(f"❌ Failed to dispatch 40-minute Telegram heartbeat to {cid} via bot {token[:8]}...: {res.status_code} {res.text}")
                            all_sent = False
                    except Exception as e:
                        lat_ms = (time.time() - t0) * 1000.0
                        try:
                            from breakout_scanner.run_logger import log_telegram_send, mask_token, mask_chat_id
                            log_telegram_send(
                                bot_label=mask_token(token),
                                chat_label=mask_chat_id(cid),
                                message_type="heartbeat",
                                chunk_count=1,
                                http_status=getattr(getattr(e, "response", None), "status_code", None),
                                latency_ms=lat_ms,
                                error_text=str(e),
                                market="ALL",
                            )
                        except Exception:
                            pass
                        self.logger.error(f"❌ Failed to dispatch 40-minute Telegram heartbeat to {cid}: {e}")
                        all_sent = False

                if all_sent:
                    self.logger.info("💓 40-minute Telegram Heartbeat pulse successfully dispatched.")

        # Heartbeat Telemetry Logging (§E)
        try:
            from breakout_scanner.run_logger import log_event, _STATE
            rss_mb = 0.0
            open_fds = 0
            disk_free_gb = 0.0
            logs_size_bytes = 0
            try:
                import resource
                max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                rss_mb = round(max_rss / (1024 * 1024), 2) if sys.platform == "darwin" else round(max_rss / 1024, 2)
            except Exception:
                pass

            try:
                import shutil
                total, used, free = shutil.disk_usage(".")
                disk_free_gb = round(free / (1024 ** 3), 2)
            except Exception:
                pass

            try:
                if os.path.exists("logs"):
                    logs_size_bytes = sum(
                        os.path.getsize(os.path.join(dirpath, f))
                        for dirpath, dirnames, filenames in os.walk("logs")
                        for f in filenames
                    )
            except Exception:
                pass

            try:
                if sys.platform != "win32" and os.path.exists("/dev/fd"):
                    open_fds = len(os.listdir("/dev/fd"))
            except Exception:
                pass

            log_event(
                event="heartbeat_telemetry",
                market="ALL",
                component="daemon",
                level="INFO",
                message=f"40-min heartbeat: {uptime_sec}s uptime, {rss_mb} MB RSS, {disk_free_gb} GB disk free",
                uptime_seconds=uptime_sec,
                last_successful_scan_time=_STATE.last_successful_scans,
                consecutive_failed_scans=_STATE.consecutive_failed_scans,
                memory_mb=rss_mb,
                open_file_count=open_fds,
                disk_free_gb=disk_free_gb,
                log_directory_size_bytes=logs_size_bytes,
            )
        except Exception:
            pass

        return all_sent

    def emit_startup_snapshots(self, base_dir: str = "logs/run"):
        """
        Emits one daemon_start startup snapshot per active market,
        using its resolved per-market production profile and watchlist.
        """
        from breakout_scanner.run_logger import log_startup_snapshot, rotate_and_clean_logs
        import schedule
        rotate_and_clean_logs(base_dir=base_dir, retention_days=14)

        for m in self.markets:
            sched = self.scheduler.schedulers.get(m)
            if sched:
                cfg_p = sched.cfg_path
                m_cfg = sched.cfg
                m_session = sched.market_session
                wl_files = sched.watchlist_files
                symbols = sched.load_symbols()
            else:
                cfg_p = self.config_path
                m_cfg = None
                m_session = None
                wl_files = None
                symbols = None

            log_startup_snapshot(
                market=m,
                config_path=cfg_p,
                cfg=m_cfg,
                watchlist_file=wl_files[0] if (wl_files and len(wl_files) > 0) else None,
                symbols=symbols,
                component="daemon",
                base_dir=base_dir,
                schedule_jobs=schedule.jobs,
                market_session=m_session,
            )

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

        # Startup Snapshot (§A) - Emit one per active market
        try:
            self.emit_startup_snapshots()
        except Exception as e:
            self.logger.warning(f"Failed to write startup snapshot: {e}")

        # Schedule 40-minute Telegram Heartbeat pulse
        schedule.every(40).minutes.do(self.dispatch_telegram_heartbeat)
        if is_telegram_heartbeat_enabled():
            self.logger.info("💓 Scheduled 40-minute Telegram Heartbeat notification")
        else:
            self.logger.info("🔕 Scheduled 40-minute Heartbeat telemetry (Telegram alerts disabled via .env)")

        # Schedule nightly What-If outcome tracking at 00:15 UTC (executed asynchronously in background thread)
        def run_nightly_outcome_tracking():
            def _async_worker():
                try:
                    from breakout_scanner.outcome_tracker import update_ledger_outcomes
                    from breakout_scanner.run_logger import log_event
                    t_ot0 = time.time()
                    try:
                        log_event(event="outcome_tracker_start", market="ALL", component="outcome_tracker", message="Nightly outcome tracking started")
                    except Exception:
                        pass
                    self.logger.info("🌙 Running nightly What-If outcome tracking on forward-test ledger in background thread...")
                    res = update_ledger_outcomes()
                    dur_s = round(time.time() - t_ot0, 2)
                    self.logger.info(f"🌙 Nightly outcome tracking complete: {res.get('newly_evaluated', 0)} newly evaluated setups.")
                    try:
                        log_event(
                            event="outcome_tracker_end",
                            market="ALL",
                            component="outcome_tracker",
                            message=f"Nightly outcome tracking completed in {dur_s}s",
                            records_read=res.get("records_read", 0),
                            newly_evaluated=res.get("newly_evaluated", 0),
                            expired=res.get("expired", 0),
                            per_symbol_fetch_failures=res.get("per_symbol_fetch_failures", {}),
                            duration_seconds=dur_s,
                        )
                    except Exception:
                        pass
                except Exception as e:
                    self.logger.error(f"❌ Failed to run nightly outcome tracking: {e}", exc_info=True)
                    try:
                        from breakout_scanner.run_logger import log_event
                        import traceback
                        log_event(
                            event="outcome_tracker_error",
                            market="ALL",
                            level="ERROR",
                            component="outcome_tracker",
                            message=f"Outcome tracker exception: {e}",
                            error_trace=traceback.format_exc()[:300],
                        )
                    except Exception:
                        pass

            import threading
            threading.Thread(target=_async_worker, name="NightlyOutcomeTrackerThread", daemon=True).start()

        schedule.every().day.at("00:15", "UTC").do(run_nightly_outcome_tracking)
        self.logger.info("🌙 Scheduled nightly What-If outcome evaluation at 00:15 UTC (background async thread)")

        # Periodic telemetry pulse every 10 minutes (keeps local disk telemetry fresh during deep sleep)
        schedule.every(10).minutes.do(lambda: self.heartbeat.beat(force=True))

        now_utc = pd.Timestamp.now(tz="UTC")
        active = self.orchestrator.get_active_markets(dt=now_utc, include_recent_close=False)
        closed = [m for m in self.markets if m not in active]
        self.previous_active_markets = set(active)
        self.heartbeat.update_stats(
            status="HEALTHY",
            configured_markets=self.markets,
            active_markets=active,
            closed_markets=closed,
        )

        # Dispatch immediate startup heartbeat to Telegram
        if targets:
            if is_telegram_heartbeat_enabled():
                self.logger.info("📡 Dispatching initial startup heartbeat to Telegram...")
                self.dispatch_telegram_heartbeat()
            else:
                self.logger.info("🔕 Initial Telegram startup heartbeat skipped (ENABLE_HEARTBEAT_ALERT=false).")

        # Optional startup scan
        if startup_scan:
            self.logger.info("⚡ Executing startup scan across active markets...")
            self.scheduler.scan_active_markets(force=True)

        # Continuous production event loop (deep sleep between triggers to save CPU credits)
        try:
            from breakout_scanner.run_logger import _STATE, log_event
            while self.running:
                gap = _STATE.check_daemon_gap(threshold_sec=120.0)
                if gap:
                    self.logger.warning(f"⚠️ [DAEMON_GAP] Main loop tick gap detected: {gap}s")
                    try:
                        log_event(
                            event="daemon_gap",
                            market="ALL",
                            level="WARNING",
                            component="daemon",
                            message=f"Main loop stalled/slept for {gap}s (> 120s threshold)",
                            gap_seconds=gap,
                        )
                    except Exception:
                        pass

                # Session transitions check
                try:
                    now_check = pd.Timestamp.now(tz="UTC")
                    curr_active = set(self.orchestrator.get_active_markets(dt=now_check, include_recent_close=False))
                    opened = curr_active - self.previous_active_markets
                    closed_now = self.previous_active_markets - curr_active
                    for op_m in opened:
                        log_event(event="session_open", market=op_m, component="market_session", message=f"Market session opened for {op_m}")
                    for cl_m in closed_now:
                        log_event(event="session_close", market=cl_m, component="market_session", message=f"Market session closed for {cl_m}")
                    self.previous_active_markets = curr_active
                except Exception:
                    pass

                schedule.run_pending()
                time.sleep(poll_sec)
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            self.logger.critical(f"💥 Fatal unhandled exception in daemon loop: {e}", exc_info=True)
            try:
                from breakout_scanner.run_logger import log_event
                log_event(
                    event="daemon_exception",
                    market="ALL",
                    level="CRITICAL",
                    component="daemon",
                    message=f"Fatal exception in daemon loop: {e}",
                    error_trace=tb_str[:300],
                )
            except Exception:
                pass
            self.heartbeat.update_stats(status="DEGRADED")
            raise
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
        default=None,
        help="Comma-separated market identifiers to orchestrate (default: resolved from .env or US,NSE,CRYPTO)",
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

    cli_markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()] if args.markets else None
    market_list = resolve_configured_markets(cli_markets)
    runner = CloudDaemonRunner(
        markets=market_list,
        config_path=args.config,
        heartbeat_interval=args.heartbeat_interval,
        log_file=args.log_file,
    )
    runner.run(startup_scan=args.startup_scan)


if __name__ == "__main__":
    main()
