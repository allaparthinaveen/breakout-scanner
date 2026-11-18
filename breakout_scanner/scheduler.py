import os
import time
import json
import logging
import tempfile
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Any
import requests
import pandas as pd
from dotenv import load_dotenv
import schedule

try:
    import fcntl
except ImportError:
    fcntl = None

from .models import Setup, State
from .config import load_config
from .providers.yfinance_provider import YFinanceProvider
from .scanner import MultiSymbolScanner
from .safeguards import ProductionSafeguards
from .run_logger import log_event, generate_scan_id, log_telegram_send, mask_token, mask_chat_id

load_dotenv()


@contextmanager
def file_lock(lock_path: str):
    """Advisory file lock context manager for cross-process concurrency safety."""
    if fcntl is None:
        yield
        return
    lock_file = open(lock_path, "a")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass


class CandleAlertDeduplicator:
    """
    Prevents duplicate Telegram alerts for the same candle close.
    Supports:
    1. Atomic JSON storage with advisory file locking (os.replace + fcntl.flock).
    2. SQLite database backend (.sqlite or .db) for high-concurrency production setups.
    """
    def __init__(self, cache_file: str = "data/alert_dedup.json"):
        self.cache_file = cache_file
        self.is_sqlite = self.cache_file.endswith((".db", ".sqlite"))
        self.lock_file = self.cache_file + ".lock"
        self.records: Dict[str, dict] = {}
        self._ensure_cache_dir()
        if self.is_sqlite:
            self._init_sqlite()
        else:
            self.load()

    def _ensure_cache_dir(self):
        dir_name = os.path.dirname(self.cache_file)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

    def _init_sqlite(self):
        with sqlite3.connect(self.cache_file, timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_dedup (
                    signature TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    meta TEXT
                );
            """)
            conn.commit()

    def load(self):
        if self.is_sqlite:
            return
        if os.path.exists(self.cache_file):
            try:
                with file_lock(self.lock_file):
                    with open(self.cache_file, "r") as f:
                        self.records = json.load(f)
            except Exception as e:
                logging.getLogger("scheduler").warning(f"Failed to load alert dedup cache: {e}. Starting fresh.")
                self.records = {}
        else:
            self.records = {}

    def save(self):
        if self.is_sqlite:
            return
        self._ensure_cache_dir()
        dir_name = os.path.dirname(self.cache_file) or "."
        temp_path = None
        try:
            with file_lock(self.lock_file):
                # Atomic persistence: write to temp file then atomic rename to prevent corruption on crash
                with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                    json.dump(self.records, tf, indent=2)
                    tf.flush()
                    os.fsync(tf.fileno())
                    temp_path = tf.name
                os.replace(temp_path, self.cache_file)
        except Exception as e:
            logging.getLogger("scheduler").error(f"Failed to save alert dedup cache: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def build_signature(symbol: str, signal_type: str, candle_time: str) -> str:
        # Standard signature: SYMBOL:SIGNAL_TYPE:CANDLE_TIMESTAMP
        clean_time = str(candle_time).strip().replace(" ", "_")
        return f"{symbol.upper()}:{signal_type.upper()}:{clean_time}"

    def is_duplicate(self, signature: str) -> bool:
        if self.is_sqlite:
            with sqlite3.connect(self.cache_file, timeout=10.0) as conn:
                row = conn.execute("SELECT 1 FROM alert_dedup WHERE signature = ?", (signature,)).fetchone()
                return row is not None
        return signature in self.records

    def record_alert(self, signature: str, meta: Optional[dict] = None):
        now_iso = datetime.now().isoformat()
        if self.is_sqlite:
            with sqlite3.connect(self.cache_file, timeout=10.0) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO alert_dedup (signature, recorded_at, meta) VALUES (?, ?, ?)",
                    (signature, now_iso, json.dumps(meta or {}))
                )
                conn.commit()
            return

        self.records[signature] = {
            "recorded_at": now_iso,
            "meta": meta or {}
        }
        self.save()

    def prune(self, max_age_days: int = 7):
        """Removes entries older than max_age_days to keep cache compact."""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        if self.is_sqlite:
            with sqlite3.connect(self.cache_file, timeout=10.0) as conn:
                conn.execute("DELETE FROM alert_dedup WHERE recorded_at < ?", (cutoff.isoformat(),))
                conn.commit()
            return

        to_delete = []
        for sig, data in self.records.items():
            rec_str = data.get("recorded_at")
            if rec_str:
                try:
                    rec_time = datetime.fromisoformat(rec_str)
                    if rec_time < cutoff:
                        to_delete.append(sig)
                except Exception:
                    pass
        for sig in to_delete:
            del self.records[sig]
        if to_delete:
            self.save()

    def clear(self):
        if self.is_sqlite:
            with sqlite3.connect(self.cache_file, timeout=10.0) as conn:
                conn.execute("DELETE FROM alert_dedup")
                conn.commit()
            return

        self.records = {}
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass
        if os.path.exists(self.lock_file):
            try:
                os.remove(self.lock_file)
            except Exception:
                pass


from .telegram_formatter import (
    format_instant_alert_html,
    format_price,
    format_actionable_slate_html,
    format_scan_report_html,
)
from .slate import SlatePipeline, EffectiveConfigSummary, ActionableSlate
from .market_session import (
    MarketSessionManager,
    MarketSessionConfig,
    CandleBoundaryVerifier,
    MultiMarketOrchestrator,
    STANDARD_MARKET_CONFIGS,
)
from .secrets import get_telegram_credentials, get_telegram_targets


def format_instant_alert(setup: Setup, timeframe: str = "1H", allocation: Optional[Any] = None) -> str:
    """
    Formats the high-priority instant push alert for candle closes.
    Uses mobile-first card layout with rich HTML badges.
    """
    return format_instant_alert_html(setup, timeframe, allocation=allocation)


class SessionScheduler:
    """
    Automated Session-Aware Background Daemon.
    Executes scans right as candles close (e.g. :01 past every hour).
    Dispatches instant alerts to Telegram within 60 seconds with strict deduplication.
    """
    def __init__(
        self,
        cfg_path: str = "config/default.yaml",
        watchlist_files: Optional[List[str]] = None,
        dedup_file: str = "data/alert_dedup.json",
        log_file: str = "logs/scanner.log",
        market_id: str = "US",
        enforce_market_hours: bool = False,
    ):
        self.market_id = market_id
        self.enforce_market_hours = enforce_market_hours
        try:
            self.market_session = MarketSessionManager.from_market_id(self.market_id)
        except Exception:
            self.market_session = None

        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)
        self.provider = YFinanceProvider()
        self.scanner = MultiSymbolScanner(self.provider, self.cfg)
        self.deduplicator = CandleAlertDeduplicator(dedup_file)
        self.pipeline = SlatePipeline(cfg=self.cfg, scanner=self.scanner)
        
        self.watchlist_files = watchlist_files or [
            "config/watchlist.txt",
            "config/crypto_watchlist.txt",
            "config/top30_watchlist.txt",
            "config/nsetop10_watchlist.txt",
            "config/hk_top30_watchlist.txt",
            "config/japan_top30_watchlist.txt"
        ]

        token, chat_id = get_telegram_credentials()
        self.telegram_token = token
        self.telegram_chat_id = chat_id
        self.telegram_chat_ids = [cid.strip() for cid in (chat_id or "").replace(";", ",").split(",") if cid.strip()]
        self.telegram_targets = get_telegram_targets()

        self.safeguards = ProductionSafeguards(self.cfg)
        if os.getenv("KILL_SWITCH", "").lower() in ("true", "1", "yes"):
            self.safeguards.engage_kill_switch("KILL_SWITCH environment variable is set")

        # Persistent Portfolio State Engine (P0-4 & P0-5)
        mkt_suffix = f"_{self.market_id.lower()}" if self.market_id else ""
        state_path = f"data/portfolio_state{mkt_suffix}.json"
        from .portfolio_state import PortfolioStateManager
        self.portfolio_state = PortfolioStateManager(state_file=state_path)

        self.log_file = log_file
        self.logger = self._setup_logging()
        EffectiveConfigSummary.log_matrix(self.cfg, self.logger)

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("breakout_daemon")
        logger.setLevel(logging.INFO)
        logger.handlers = []

        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

        # File handler
        fh = logging.FileHandler(self.log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Stream handler
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        return logger

    def load_symbols(self) -> List[str]:
        symbols = []
        for filename in self.watchlist_files:
            if os.path.exists(filename):
                with open(filename, "r") as f:
                    for line in f:
                        # Strip inline comments, commas, whitespace and normalize to uppercase
                        sym = line.split("#")[0].split(",")[0].strip().upper()
                        if sym and sym not in symbols:
                            symbols.append(sym)
        return symbols

    def send_telegram_alert(self, message: str) -> bool:
        targets = self.telegram_targets if hasattr(self, "telegram_targets") and self.telegram_targets else get_telegram_targets()
        if not targets:
            self.logger.warning("Telegram credentials missing in environment. Alert not sent to phone.")
            return False

        # Split message into chunks if larger than 4000 characters (Telegram 4096 character limit)
        chunks = []
        if len(message) <= 4000:
            chunks = [message]
        else:
            current = []
            curr_len = 0
            for block in message.split("\n\n"):
                if curr_len + len(block) + 2 > 3900 and current:
                    chunks.append("\n\n".join(current))
                    current = [block]
                    curr_len = len(block)
                else:
                    current.append(block)
                    curr_len += len(block) + 2
            if current:
                chunks.append("\n\n".join(current))

        all_sent = True
        for token, chat_id in targets:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            for chunk in chunks:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML"
                }
                t0 = time.time()
                try:
                    res = requests.post(url, json=payload, timeout=10)
                    lat_ms = (time.time() - t0) * 1000.0
                    try:
                        from breakout_scanner.run_logger import log_telegram_send, mask_token, mask_chat_id
                        log_telegram_send(
                            bot_label=mask_token(token),
                            chat_label=mask_chat_id(chat_id),
                            message_type="signal" if ("SCAN_REPORT" in message or "SECTION" in message) else "alert",
                            chunk_count=len(chunks),
                            http_status=res.status_code,
                            latency_ms=lat_ms,
                            error_text=res.text if not res.ok else None,
                            market=self.market_id,
                        )
                    except Exception:
                        pass
                    if not res.ok:
                        self.logger.error(f"Failed to dispatch Telegram message to {chat_id} via bot {token[:8]}...: {res.status_code} {res.text}")
                        all_sent = False
                except Exception as e:
                    lat_ms = (time.time() - t0) * 1000.0
                    try:
                        from breakout_scanner.run_logger import log_telegram_send, mask_token, mask_chat_id
                        log_telegram_send(
                            bot_label=mask_token(token),
                            chat_label=mask_chat_id(chat_id),
                            message_type="alert",
                            chunk_count=len(chunks),
                            http_status=getattr(getattr(e, "response", None), "status_code", None),
                            latency_ms=lat_ms,
                            error_text=str(e),
                            market=self.market_id,
                        )
                    except Exception:
                        pass
                    self.logger.error(f"Failed to dispatch Telegram message to {chat_id}: {e}")
                    all_sent = False
        return all_sent

    def scan_and_alert(self, force: bool = False) -> dict:
        """
        Core scanning execution triggered upon candle close.
        Evaluates symbols on 1H interval, detects fresh setups, deduplicates,
        and pushes instant Telegram alerts.
        """
        symbols = self.load_symbols()
        start_time = time.time()
        now_dt = datetime.now(timezone.utc)
        sched_anchor = now_dt.replace(second=0, microsecond=0)
        drift_s = round(abs((now_dt - sched_anchor).total_seconds()), 2)

        scan_id = generate_scan_id(self.market_id)

        try:
            log_event(
                event="job_fire",
                market=self.market_id,
                scan_id=scan_id,
                job_name=f"candle_scan_{self.market_id}",
                scheduled_time=sched_anchor.isoformat(),
                actual_time=now_dt.isoformat(),
                drift_seconds=drift_s,
            )
        except Exception:
            pass

        self.logger.info(f"⚡ Starting candle close scan across {len(symbols)} symbols...")

        # Market Session Open Check (when enforce_market_hours is enabled)
        if self.enforce_market_hours and not force and self.market_session is not None:
            now_utc = pd.Timestamp.now(tz="UTC")
            if not self.market_session.is_session_active_or_recent_close(now_utc):
                reason_msg = f"Market {self.market_id} is currently CLOSED or in lunch break. Skipping live scan."
                self.logger.info(f"⏸️ [SESSION] {reason_msg}")
                try:
                    log_event(
                        event="job_skipped",
                        market=self.market_id,
                        scan_id=scan_id,
                        reason=reason_msg,
                    )
                except Exception:
                    pass
                return {
                    "scanned": 0,
                    "fresh_alerts": 0,
                    "duplicates_skipped": 0,
                    "safeguards_rejected": 0,
                    "status": "MARKET_CLOSED",
                    "candidates": [],
                }

        # Production Safeguards Check 1: Kill Switch
        if self.safeguards.kill_switch_active:
            reason_msg = "Global Kill Switch is ACTIVE. Halting scan and alert pipeline."
            self.logger.critical(f"🛑 [SAFEGUARDS] {reason_msg}")
            try:
                log_event(
                    event="job_skipped",
                    market=self.market_id,
                    scan_id=scan_id,
                    reason=reason_msg,
                )
            except Exception:
                pass
            return {
                "scanned": 0,
                "fresh_alerts": 0,
                "duplicates_skipped": 0,
                "safeguards_rejected": 0,
                "status": "HALTED_BY_KILL_SWITCH",
                "candidates": []
            }

        # Production Safeguards Check 2: Daily Loss Limit (P0-5)
        account_size = float(self.cfg.get('risk', {}).get('account_size', 100000.0))
        if not self.safeguards.check_daily_loss_limit(account_size, portfolio_state=self.portfolio_state):
            reason_msg = "Daily loss limit breached. Circuit breaker tripped."
            self.logger.critical(f"🛑 [SAFEGUARDS] {reason_msg}")
            try:
                log_event(
                    event="job_skipped",
                    market=self.market_id,
                    scan_id=scan_id,
                    reason=reason_msg,
                )
            except Exception:
                pass
            return {
                "scanned": 0,
                "fresh_alerts": 0,
                "duplicates_skipped": 0,
                "safeguards_rejected": 0,
                "status": "HALTED_BY_DAILY_LOSS",
                "candidates": []
            }

        # 1H candles for timely execution
        setups = self.scanner.scan(symbols, period="60d", interval="1h", scan_id=scan_id, market=self.market_id, auto_flush=False)

        # Persistent Portfolio State: Mark-to-market and exit check (P0-4 & P1-3)
        exit_events = self.portfolio_state.update_market_data(setups)

        # Re-check daily loss circuit breaker post-MTM update
        if not self.safeguards.check_daily_loss_limit(account_size, portfolio_state=self.portfolio_state):
            self.logger.critical("🛑 [SAFEGUARDS] Daily loss limit breached post-market update. Halting.")
            return {
                "scanned": len(setups),
                "fresh_alerts": 0,
                "duplicates_skipped": 0,
                "safeguards_rejected": 0,
                "status": "HALTED_BY_DAILY_LOSS",
                "candidates": []
            }

        # Phase 1: Authoritative ActionableSlate evaluation with persistent open positions
        # Mark final bar breakouts as next-session candidates if scanning after session close
        if self.market_session is not None:
            now_utc = pd.Timestamp.now(tz="UTC")
            market_ts = self.market_session.to_market_tz(now_utc)
            session_label = market_ts.strftime("%Y-%m-%d")
            _, close_dt = self.market_session.get_session_bounds(session_label)
            if close_dt is not None and market_ts >= close_dt:
                for s in setups:
                    if s.state in (State.BUY, State.SELL) and s.is_fresh_entry:
                        s.is_next_session_candidate = True

        open_positions = self.portfolio_state.get_open_positions()
        slate = self.pipeline.evaluate_setups(setups, total_scanned=len(symbols), open_positions=open_positions)
        
        fresh_alerts_sent = 0
        duplicates_skipped = 0
        safeguards_rejected = 0
        fresh_candidates = []

        for t in slate.selected_trades:
            s = t.setup
            if not s:
                continue

            # Must be on the latest closed candle (bars_since_breakout <= 1)
            is_latest_candle = (s.bars_since_breakout is None or s.bars_since_breakout <= 1)
            if not is_latest_candle:
                continue

            candle_epoch = time.time()
            if hasattr(s, 'breakout_time') and s.breakout_time:
                try:
                    candle_epoch = datetime.fromisoformat(str(s.breakout_time)).timestamp()
                except Exception:
                    pass

            # P1-1: Fetch reliable quote or synthetic spread for execution price validation
            quote = {}
            if hasattr(self.provider, "get_ticker_quote"):
                try:
                    quote = self.provider.get_ticker_quote(
                        s.symbol,
                        fallback_price=s.current_price or s.entry,
                        atr=getattr(s, "atr", None)
                    )
                except Exception:
                    quote = {}

            # Production Safeguards Check 3: Pre-trade validation gate
            if not self.safeguards.pre_trade_validation(
                s,
                current_capital=account_size,
                current_timestamp=candle_epoch,
                timeframe_seconds=3600,
                bid=quote.get("bid"),
                ask=quote.get("ask"),
                volume=quote.get("volume")
            ):
                safeguards_rejected += 1
                s.safeguard_rejected = True
                rej_reason = getattr(self.safeguards, "last_rejection_reason", "Pre-trade validation gate failed")
                self.logger.warning(f"🛡️ [SAFEGUARDS REJECTED] {s.symbol} blocked by pre-trade validation gate: {rej_reason}")
                # Demote from slate.selected_trades so it does not appear in Telegram Section 2A
                slate.selected_trades = [tr for tr in slate.selected_trades if tr.symbol != s.symbol]
                from .slate import RejectionRecord
                slate.rejected_trades.append(
                    RejectionRecord(
                        symbol=s.symbol,
                        rejection_code="SAFEGUARD_REJECTED",
                        reason=rej_reason,
                        setup=s,
                    )
                )
                continue

            fresh_candidates.append(s)

        # Active setups across entire scan (both Section 2A Fresh and Section 2B In Progress)
        active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]

        # Determine notification mode (default is "scan_report" for complete Section 2A + 2B without filters)
        notification_mode = self.cfg.get("notifications", {}).get("mode", "scan_report") if self.cfg else "scan_report"

        if notification_mode == "scan_report":
            # Anchor scan signature to the exact completed candle close boundary
            now_utc = pd.Timestamp.now(tz="UTC")
            if self.market_session is not None:
                market_ts = self.market_session.to_market_tz(now_utc)
                last_candle = self.market_session.get_last_closed_candle(now_utc, timeframe="1h")
                if last_candle is not None:
                    candle_slot_key = last_candle.strftime("%Y-%m-%d_%H:%M")
                    report_ts = last_candle
                else:
                    candle_slot_key = market_ts.strftime("%Y-%m-%d_%H:%M")
                    report_ts = market_ts
            else:
                market_ts = now_utc
                candle_slot_key = market_ts.strftime("%Y-%m-%d_%H:00")
                report_ts = now_utc

            market_label = self.market_id or "MARKET"
            report_signature = f"SCAN_REPORT:{market_label.upper()}:{candle_slot_key}"

            scan_stats = getattr(self.scanner, "last_scan_stats", None)
            if scan_stats:
                tot_s = scan_stats.get("total", 0)
                wd_s = scan_stats.get("with_data", 0)
                min_p = scan_stats.get("min_scan_completeness_pct", 90.0)
                if tot_s > 0 and ((wd_s / tot_s * 100) < min_p or scan_stats.get("aborted", False)):
                    incomplete_sig = f"INCOMPLETE_ALERT:{market_label.upper()}:{candle_slot_key}"
                    if not self.deduplicator.is_duplicate(incomplete_sig):
                        self.send_telegram_alert(f"⚠️ SCAN INCOMPLETE {market_label.upper()} {wd_s}/{tot_s}")
                        self.deduplicator.record_alert(incomplete_sig, {"market": market_label})

            sent = False
            if self.deduplicator.is_duplicate(report_signature) and not force:
                duplicates_skipped += 1
                self.logger.debug(f"[DEDUP] Already alerted scan report for {report_signature}, skipping.")
            else:
                report_html = format_scan_report_html(
                    setups,
                    market=self.market_id,
                    timestamp=report_ts,
                    slate=slate,
                    exit_events=exit_events,
                    scan_stats=scan_stats,
                )
                self.logger.info(f"🚨 DISPATCHING MARKET SCAN REPORT (2A + 2B) for {market_label}: {report_signature}")
                self.logger.info("\n" + report_html)
                sent = self.send_telegram_alert(report_html)
                if sent:
                    self.deduplicator.record_alert(report_signature, {
                        "market": market_label,
                        "active_count": len(active_setups),
                        "candle_slot": candle_slot_key
                    })
                    # Record fresh playable entries into persistent portfolio state (P0-4)
                    for t in slate.selected_trades:
                        if t.setup and t.setup.is_fresh_entry and not getattr(t.setup, "requires_confirmation", False):
                            if not getattr(t.setup, "is_next_session_candidate", False):
                                self.portfolio_state.record_entry(t)
                else:
                    self.logger.warning(f"⚠️ Telegram dispatch failed for {report_signature}, recorded locally.")
                    self.deduplicator.record_alert(report_signature, {
                        "market": market_label,
                        "active_count": len(active_setups),
                        "delivery_failed": True
                    })

            # Record individual setup signatures in deduplication cache for state tracking
            for s in active_setups:
                signal_type = "RETEST" if s.retest_confirmed else "BREAKOUT"
                c_time = s.retest_time if s.retest_confirmed and s.retest_time else (s.breakout_time or "LATEST_CLOSE")
                sig = self.deduplicator.build_signature(s.symbol, signal_type, c_time)
                self.deduplicator.record_alert(sig, {
                    "symbol": s.symbol,
                    "type": signal_type,
                    "entry": s.entry,
                    "stop": s.stop,
                    "grade": s.composite_grade,
                    "score": s.composite_score,
                    "delivery_failed": not sent
                })
        else:
            # Legacy/Instant single-trade card mode
            for t in slate.selected_trades:
                s = t.setup
                if not s or s not in fresh_candidates:
                    continue

                signal_type = "RETEST" if s.retest_confirmed else "BREAKOUT"
                candle_time = s.retest_time if s.retest_confirmed and s.retest_time else (s.breakout_time or "LATEST_CLOSE")
                signature = self.deduplicator.build_signature(s.symbol, signal_type, candle_time)

                if self.deduplicator.is_duplicate(signature):
                    duplicates_skipped += 1
                    self.logger.debug(f"[DEDUP] Already alerted {signature}, skipping.")
                else:
                    alert_msg = format_instant_alert(s, timeframe="1H", allocation=t)
                    self.logger.info(f"🚨 FRESH SIGNAL DETECTED: {signature}")
                    self.logger.info("\n" + alert_msg)

                    sent = self.send_telegram_alert(alert_msg)
                    if sent:
                        self.portfolio_state.record_entry(t)
                        self.deduplicator.record_alert(signature, {
                            "symbol": s.symbol,
                            "type": signal_type,
                            "entry": s.entry,
                            "stop": s.stop,
                            "shares": t.shares,
                            "dollar_risk": t.dollar_risk,
                            "grade": s.composite_grade,
                            "score": s.composite_score
                        })
                        fresh_alerts_sent += 1
                    else:
                        self.logger.warning(f"⚠️ Telegram dispatch failed for {signature}, recorded locally.")
                        self.deduplicator.record_alert(signature, {
                            "symbol": s.symbol,
                            "type": signal_type,
                            "entry": s.entry,
                            "stop": s.stop,
                            "shares": t.shares,
                            "dollar_risk": t.dollar_risk,
                            "delivery_failed": True
                        })

        # Flush per-symbol traces with slate allocations and scan summary (§B, §C)
        telegram_dispatched = bool(sent if notification_mode == "scan_report" else fresh_alerts_sent > 0)
        try:
            self.scanner.flush_symbol_traces(
                slate=slate,
                market=self.market_id,
                scan_id=scan_id,
                scheduled_time=sched_anchor,
                telegram_sent=telegram_dispatched,
            )
        except Exception:
            pass

        if notification_mode == "scan_report":
            if sent:
                summary_fresh = int(getattr(self.scanner, "last_scan_summary", {}).get("signals_fresh", 0) or 0)
                fresh_alerts_sent = summary_fresh if summary_fresh > 0 else len(fresh_candidates)
            else:
                fresh_alerts_sent = 0

        elapsed = time.time() - start_time
        self.deduplicator.prune(max_age_days=7)
        
        self.logger.info(
            f"💓 [HEARTBEAT] Scan finished in {elapsed:.1f}s. "
            f"Evaluated {len(symbols)} symbols | Slate Actionable: {len(slate.selected_trades)} | "
            f"Fresh Alerts: {fresh_alerts_sent} | Deduplicated Skipped: {duplicates_skipped}"
        )

        # Log scan metrics telemetry (Phase 0 Baseline & Safety Net)
        try:
            from .telemetry import log_scan_metrics
            log_scan_metrics(setups, market="multi", timeframe="1h", config_name="live_daemon")
        except Exception:
            pass

        return {
            "scanned": len(symbols),
            "slate": slate,
            "actionable_trades": len(slate.selected_trades),
            "suppressed_trades": len(slate.suppressed_trades),
            "rejected_trades": len(slate.rejected_trades),
            "alerts_sent": fresh_alerts_sent,
            "duplicates_skipped": duplicates_skipped,
            "safeguards_rejected": safeguards_rejected,
            "elapsed_seconds": round(elapsed, 2)
        }

    def schedule_hourly(self, minute_offset: int = 1):
        """
        Schedules the scan to run right after the hourly candle closes (e.g. :01).
        """
        minute_str = f":{minute_offset:02d}"
        schedule.every().hour.at(minute_str).do(self.scan_and_alert)
        self.logger.info(f"⏱️ Scheduled hourly candle close scanner at {minute_str} past every hour.")

    def schedule_session_times(self, times: Optional[List[str]] = None):
        """
        Optionally schedules market-session aligned checks (e.g. NSE / US open & power hours).
        """
        session_times = times or ["09:35", "13:30", "15:15"]
        for t in session_times:
            schedule.every().day.at(t).do(self.scan_and_alert)
            self.logger.info(f"⏱️ Scheduled session-aligned scan at {t}.")

    def schedule_market_sessions(self, timeframe: str = "1h"):
        """
        Schedules exchange-aligned scans dynamically using exchange_calendars.
        Respects market open/close, lunch breaks, and DST transitions.
        """
        if self.market_session is None:
            self.logger.warning("No market session configured. Falling back to hourly schedule.")
            self.schedule_hourly()
            return

        now_utc = pd.Timestamp.now(tz="UTC")
        today_in_market = self.market_session.to_market_tz(now_utc).date()
        events = self.market_session.get_scheduled_events(today_in_market, timeframe=timeframe)
        if not events:
            # If weekend or holiday, resolve the upcoming trading day to register regular schedule
            for offset in range(1, 8):
                cand = today_in_market + timedelta(days=offset)
                if self.market_session.is_trading_day(cand):
                    events = self.market_session.get_scheduled_events(cand, timeframe=timeframe)
                    if events:
                        break

        market_tz = self.market_session.config.timezone
        self.logger.info(f"📅 Setting up exchange-aligned schedule for {self.market_id} ({market_tz}):")
        for ev_type, ev_time in events:
            if ev_type == "CANDLE_SCAN":
                time_str = ev_time.strftime("%H:%M")
                schedule.every().day.at(time_str, tz=market_tz).do(self.scan_and_alert)
                self.logger.info(f"   ⏱️ Scheduled CANDLE_SCAN at {time_str} ({market_tz})")

    def start(self, run_immediately: bool = True, poll_sec: int = 1):
        """
        Runs the daemon loop continuously.
        """
        self.logger.info("🚀 Background Session Scheduler daemon started. Press Ctrl+C to terminate.")
        if run_immediately:
            self.scan_and_alert()

        try:
            while True:
                schedule.run_pending()
                time.sleep(poll_sec)
        except (KeyboardInterrupt, SystemExit):
            self.logger.info("🛑 Background Session Scheduler daemon stopped gracefully.")


class MultiMarketScheduler:
    """
    Orchestrates multiple market schedulers (e.g. US, NSE, TSE, HKEX, CRYPTO)
    in a single continuous background daemon process with timezone-aware scheduling.
    """
    def __init__(
        self,
        markets: Optional[List[str]] = None,
        base_cfg_path: str = "config/default.yaml",
        log_file: str = "logs/multi_market.log",
    ):
        self.market_names = markets or ["US", "NSE", "TSE", "HKEX", "CRYPTO"]
        self.base_cfg_path = base_cfg_path
        self.log_file = log_file
        self.schedulers: Dict[str, SessionScheduler] = {}
        self.orchestrator = MultiMarketOrchestrator(self.market_names)

        for m in self.market_names:
            m_cfg = STANDARD_MARKET_CONFIGS.get(m)
            profile_path = m_cfg.profile_path if (m_cfg and m_cfg.profile_path and os.path.exists(m_cfg.profile_path)) else base_cfg_path
            # Resolve all configured watchlists (supports multi-watchlist lists)
            wl_files = []
            if m_cfg:
                if getattr(m_cfg, "watchlist_files", None):
                    wl_files.extend([f for f in m_cfg.watchlist_files if os.path.exists(f)])
                elif m_cfg.watchlist_file and os.path.exists(m_cfg.watchlist_file):
                    wl_files.append(m_cfg.watchlist_file)
            if not wl_files:
                wl_files = None
            dedup_path = f"data/alert_dedup_{m.lower()}.json"
            
            self.schedulers[m] = SessionScheduler(
                cfg_path=profile_path,
                watchlist_files=wl_files,
                dedup_file=dedup_path,
                log_file=self.log_file,
                market_id=m,
                enforce_market_hours=True,
            )

    def schedule_all(self, timeframe: str = "1h"):
        """Configures market-session aligned schedules for all target markets."""
        for mid, sched in self.schedulers.items():
            sched.schedule_market_sessions(timeframe=timeframe)

    def scan_active_markets(self, force: bool = False) -> Dict[str, dict]:
        """Runs scans across all currently open exchanges."""
        active = self.orchestrator.get_active_markets()
        results = {}
        for mid in active:
            if mid in self.schedulers:
                results[mid] = self.schedulers[mid].scan_and_alert(force=force)
        return results

    def start(self, poll_sec: int = 1):
        """Runs the multi-market event loop."""
        self.schedule_all()
        try:
            while True:
                schedule.run_pending()
                time.sleep(poll_sec)
        except (KeyboardInterrupt, SystemExit):
            pass
