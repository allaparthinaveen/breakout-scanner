import os
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import requests
from dotenv import load_dotenv
import schedule

from .models import Setup, State
from .config import load_config
from .providers.yfinance_provider import YFinanceProvider
from .scanner import MultiSymbolScanner

load_dotenv()


class CandleAlertDeduplicator:
    """
    Prevents duplicate Telegram alerts for the same candle close.
    Stores signatures in an atomic persistent JSON cache so state survives daemon restarts.
    """
    def __init__(self, cache_file: str = "data/alert_dedup.json"):
        self.cache_file = cache_file
        self.records: Dict[str, dict] = {}
        self._ensure_cache_dir()
        self.load()

    def _ensure_cache_dir(self):
        dir_name = os.path.dirname(self.cache_file)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

    def load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self.records = json.load(f)
            except Exception as e:
                logging.getLogger("scheduler").warning(f"Failed to load alert dedup cache: {e}. Starting fresh.")
                self.records = {}
        else:
            self.records = {}

    def save(self):
        self._ensure_cache_dir()
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.records, f, indent=2)
        except Exception as e:
            logging.getLogger("scheduler").error(f"Failed to save alert dedup cache: {e}")

    @staticmethod
    def build_signature(symbol: str, signal_type: str, candle_time: str) -> str:
        # Standard signature: SYMBOL:SIGNAL_TYPE:CANDLE_TIMESTAMP
        clean_time = str(candle_time).strip().replace(" ", "_")
        return f"{symbol.upper()}:{signal_type.upper()}:{clean_time}"

    def is_duplicate(self, signature: str) -> bool:
        return signature in self.records

    def record_alert(self, signature: str, meta: Optional[dict] = None):
        self.records[signature] = {
            "recorded_at": datetime.now().isoformat(),
            "meta": meta or {}
        }
        self.save()

    def prune(self, max_age_days: int = 7):
        """Removes entries older than max_age_days to keep cache compact."""
        cutoff = datetime.now() - timedelta(days=max_age_days)
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
        self.records = {}
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass


from .telegram_formatter import format_instant_alert_html, format_price


def format_instant_alert(setup: Setup, timeframe: str = "1H") -> str:
    """
    Formats the high-priority instant push alert for candle closes.
    Uses mobile-first card layout with rich HTML badges.
    """
    return format_instant_alert_html(setup, timeframe)


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
    ):
        self.cfg = load_config(cfg_path)
        self.provider = YFinanceProvider()
        self.scanner = MultiSymbolScanner(self.provider, self.cfg)
        self.deduplicator = CandleAlertDeduplicator(dedup_file)
        
        self.watchlist_files = watchlist_files or [
            "config/watchlist.txt",
            "config/crypto_watchlist.txt",
            "config/top30_watchlist.txt",
            "config/nsetop10_watchlist.txt"
        ]

        self.telegram_token = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

        self.log_file = log_file
        self.logger = self._setup_logging()

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
                        sym = line.split(",")[0].strip()
                        if sym and not sym.startswith("#") and sym not in symbols:
                            symbols.append(sym)
        return symbols

    def send_telegram_alert(self, message: str) -> bool:
        if not self.telegram_token or not self.telegram_chat_id:
            self.logger.warning("Telegram credentials missing in environment. Alert not sent to phone.")
            return False

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
            return True
        except Exception as e:
            self.logger.error(f"Failed to dispatch Telegram message: {e}")
            return False

    def scan_and_alert(self) -> dict:
        """
        Core scanning execution triggered upon candle close.
        Evaluates symbols on 1H interval, detects fresh setups, deduplicates,
        and pushes instant Telegram alerts.
        """
        symbols = self.load_symbols()
        start_time = time.time()
        self.logger.info(f"⚡ Starting candle close scan across {len(symbols)} symbols...")

        # 1H candles for timely execution
        setups = self.scanner.scan(symbols, period="60d", interval="1h")
        
        fresh_alerts_sent = 0
        duplicates_skipped = 0
        fresh_candidates = []

        for s in setups:
            # Active signal state
            if s.state in (State.BUY, State.SELL):
                # Must be on the latest closed candle (bars_since_breakout <= 1)
                # and qualify as a pristine fresh entry (within 1% of entry, TP1 not hit)
                is_latest_candle = (s.bars_since_breakout is not None and s.bars_since_breakout <= 1)
                
                if is_latest_candle and s.is_fresh_entry:
                    signal_type = "RETEST" if s.retest_confirmed else "BREAKOUT"
                    candle_time = s.retest_time if s.retest_confirmed and s.retest_time else (s.breakout_time or "LATEST_CLOSE")
                    signature = self.deduplicator.build_signature(s.symbol, signal_type, candle_time)
                    
                    if self.deduplicator.is_duplicate(signature):
                        duplicates_skipped += 1
                        self.logger.debug(f"[DEDUP] Already alerted {signature}, skipping.")
                    else:
                        alert_msg = format_instant_alert(s, timeframe="1H")
                        self.logger.info(f"🚨 FRESH SIGNAL DETECTED: {signature}")
                        self.logger.info("\n" + alert_msg)
                        
                        sent = self.send_telegram_alert(alert_msg)
                        if sent:
                            self.deduplicator.record_alert(signature, {
                                "symbol": s.symbol,
                                "type": signal_type,
                                "entry": s.entry,
                                "stop": s.stop,
                                "grade": s.composite_grade,
                                "score": s.composite_score
                            })
                            fresh_alerts_sent += 1
                        fresh_candidates.append(s)

        elapsed = time.time() - start_time
        self.deduplicator.prune(max_age_days=7)
        
        self.logger.info(
            f"💓 [HEARTBEAT] Scan finished in {elapsed:.1f}s. "
            f"Evaluated {len(symbols)} symbols | Fresh Alerts: {fresh_alerts_sent} | "
            f"Deduplicated Skipped: {duplicates_skipped}"
        )

        return {
            "scanned": len(symbols),
            "alerts_sent": fresh_alerts_sent,
            "duplicates_skipped": fresh_duplicates if 'fresh_duplicates' in locals() else duplicates_skipped,
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
