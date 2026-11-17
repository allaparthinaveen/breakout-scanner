"""
Run Logging and Audit Infrastructure (Production Session Diagnostics).
=====================================================================
Structured, analyzable logging for market scan cycles, per-symbol decision traces,
external calls, and daemon health observability.

Rules & Guarantees:
1. Fail-Safe: No logging call ever raises or crashes a scan. Unwritable targets warn to stderr once.
2. Secret Redaction: All Telegram bot tokens, chat IDs, API keys, and credentials are redacted.
3. String Truncation: Long string fields capped at 300 chars to prevent log bloat.
4. Buffered Thread-Safe Appends: Atomic appends with Lock protection.
5. Invariance: Zero changes to scoring, gates, sizing, or configuration hash.
"""

import os
import sys
import re
import json
import time
import gzip
import shutil
import socket
import logging
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger("breakout_scanner.run_logger")

MARKET_TIMEZONES: Dict[str, str] = {
    "us": "America/New_York",
    "nse": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "bse": "Asia/Kolkata",
    "crypto": "UTC",
    "tse": "Asia/Tokyo",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "hkex": "Asia/Hong_Kong",
    "hk": "Asia/Hong_Kong",
    "hongkong": "Asia/Hong_Kong",
}

# ---------------------------------------------------------------------------
# Secret Redaction Engine
# ---------------------------------------------------------------------------

_TG_TOKEN_RE = re.compile(r"\b(?:\d{8,12}:[A-Za-z0-9_-]{34,36})\b")
_TG_BOT_URL_RE = re.compile(r"api\.telegram\.org/bot([^/\s]+)")
_TG_CHAT_ID_KV_RE = re.compile(r"(?i)([\"']?chat_id[\"']?\s*[:=]\s*[\"']?)(-?\d{6,14})([\"']?)")
_CHAT_LABEL_RE = re.compile(r"(\[Chat:\s*)(-?\d{6,14})")
_ENV_SECRET_RE = re.compile(r"(?i)\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*\s*[:=]\s*[\"']?)([^\"'\s\r\n]{4,})([\"']?)")
_AWS_KEY_RE = re.compile(r"\b(AKIA[0-9A-Z]{16})\b")


def get_known_secrets() -> List[str]:
    """Retrieves secret tokens and chat IDs from environment variables."""
    secrets = []
    for var in (
        "TG_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TG_CHAT_ID",
        "TELEGRAM_CHAT_ID",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        val = os.getenv(var)
        if val:
            for part in str(val).split(","):
                part = part.strip()
                if len(part) >= 4:
                    secrets.append(part)
    return list(set(secrets))


def redact_secrets(text: str) -> Tuple[str, int]:
    """
    Redacts tokens, chat IDs, and credentials from a text string.
    Returns (sanitized_text, redaction_count).
    """
    if not isinstance(text, str):
        return str(text), 0
    count = 0

    # 1. Standalone Telegram Bot Token pattern (e.g. 8361061485:AAGUSvENq...)
    def _sub_token(m):
        nonlocal count
        count += 1
        return "[REDACTED_TG_TOKEN]"
    res = _TG_TOKEN_RE.sub(_sub_token, text)

    # 2. Telegram URL bot token pattern (e.g. api.telegram.org/bot<token>/sendMessage)
    def _sub_bot_url(m):
        nonlocal count
        count += 1
        return "api.telegram.org/bot[REDACTED_TG_TOKEN]"
    res = _TG_BOT_URL_RE.sub(_sub_bot_url, res)

    # 3. Chat IDs in key-value context (e.g. "chat_id": 5480767676)
    def _sub_chat_id(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}[REDACTED_CHAT_ID]{m.group(3)}"
    res = _TG_CHAT_ID_KV_RE.sub(_sub_chat_id, res)

    def _sub_chat_lbl(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}[REDACTED_CHAT_ID]"
    res = _CHAT_LABEL_RE.sub(_sub_chat_lbl, res)

    # 4. AWS Access Keys
    def _sub_aws(m):
        nonlocal count
        count += 1
        return "[REDACTED_AWS_KEY]"
    res = _AWS_KEY_RE.sub(_sub_aws, res)

    # 5. Env-style credentials (e.g. API_KEY=xyz, SECRET_KEY="abc")
    def _sub_env_secret(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}[REDACTED]{m.group(3)}"
    res = _ENV_SECRET_RE.sub(_sub_env_secret, res)

    # 6. Explicitly known secret values from environment
    for secret in get_known_secrets():
        if secret in res:
            res = res.replace(secret, "[REDACTED]")
            count += 1

    return res, count


def mask_token(tok: str) -> str:
    """Masks a bot token for safe logging (e.g. bot_836106***)."""
    if not tok:
        return "none"
    tok_clean = str(tok).strip()
    if len(tok_clean) > 8:
        return f"bot_{tok_clean[:6]}***"
    return "bot_masked"


def mask_chat_id(cid: Union[str, int]) -> str:
    """Masks a chat ID for safe logging (e.g. ***67676)."""
    if cid is None:
        return "none"
    s = str(cid).strip()
    if len(s) > 4:
        return f"***{s[-4:]}"
    return "***"


# ---------------------------------------------------------------------------
# Field Truncation & Formatting
# ---------------------------------------------------------------------------

def truncate_val(val: Any, max_len: int = 300) -> Any:
    """Recursively truncates any string field in a nested data structure."""
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + "..."
        return val
    elif isinstance(val, dict):
        return {k: truncate_val(v, max_len) for k, v in val.items()}
    elif isinstance(val, list):
        return [truncate_val(x, max_len) for x in val]
    elif isinstance(val, tuple):
        return tuple(truncate_val(x, max_len) for x in val)
    return val


def get_market_tz(market: Optional[str] = None) -> ZoneInfo:
    """Resolves ZoneInfo timezone for a market identifier."""
    m = (market or "UTC").lower()
    tz_str = MARKET_TIMEZONES.get(m, "UTC")
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("UTC")


def get_exchange_date_str(market: Optional[str] = None, dt_utc: Optional[datetime] = None) -> str:
    """
    Returns the exchange-local calendar date string (YYYY-MM-DD) for log directory rotation.
    """
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    elif dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    tz = get_market_tz(market)
    local_dt = dt_utc.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d")


def get_iso_timestamps(dt: Optional[datetime] = None, market: Optional[str] = None) -> Tuple[str, str]:
    """Returns (ts_utc, ts_local) ISO-8601 strings with explicit offset/Z."""
    if dt is None:
        dt_utc = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt.astimezone(timezone.utc)

    tz = get_market_tz(market)
    local_dt = dt_utc.astimezone(tz)

    ts_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ts_local = local_dt.isoformat()
    return ts_utc, ts_local


def generate_scan_id(market: Optional[str] = None) -> str:
    """Generates a unique, sortable scan ID."""
    m = (market or "scan").lower()
    now_utc = datetime.now(timezone.utc)
    ts_compact = now_utc.strftime("%Y%m%d_%H%M%S")
    rand_suffix = hashlib.sha256(f"{time.time()}_{os.getpid()}".encode()).hexdigest()[:6]
    return f"{m}_{ts_compact}_{rand_suffix}"


# ---------------------------------------------------------------------------
# Thread-Safe File Append Helpers
# ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()
_STDERR_WARNED: set = set()


def safe_append_jsonl(filepath: str, data: dict):
    """
    Thread-safe append of a single JSON line to filepath with secret redaction and field capping.
    Fail-safe: never raises, warns to stderr once per file path on failure.
    """
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        truncated = truncate_val(data, max_len=300)
        raw_json = json.dumps(truncated, default=str)
        redacted_json, _ = redact_secrets(raw_json)
        line = redacted_json + "\n"

        with _LOG_LOCK:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception as e:
        msg = f"⚠️ [RUN_LOGGER ERROR] Failed to write log to {filepath}: {e}\n"
        if filepath not in _STDERR_WARNED:
            sys.stderr.write(msg)
            sys.stderr.flush()
            _STDERR_WARNED.add(filepath)


def safe_append_jsonl_batch(filepath: str, rows: List[dict]):
    """
    Thread-safe buffered write of multiple JSON lines to filepath with secret redaction.
    """
    if not rows:
        return
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        lines = []
        for r in rows:
            truncated = truncate_val(r, max_len=300)
            raw_json = json.dumps(truncated, default=str)
            redacted_json, _ = redact_secrets(raw_json)
            lines.append(redacted_json + "\n")
        payload = "".join(lines)

        with _LOG_LOCK:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
    except Exception as e:
        msg = f"⚠️ [RUN_LOGGER ERROR] Failed to write trace batch to {filepath}: {e}\n"
        if filepath not in _STDERR_WARNED:
            sys.stderr.write(msg)
            sys.stderr.flush()
            _STDERR_WARNED.add(filepath)


# ---------------------------------------------------------------------------
# Global State & Telemetry Tracker
# ---------------------------------------------------------------------------

class RunLoggerState:
    """Maintains running telemetry counters and process state."""
    def __init__(self):
        self.lock = threading.Lock()
        self.run_id: str = f"run_{int(time.time())}_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]}"
        self.daily_429_count: Dict[str, int] = {}
        self.scan_429_count: Dict[str, int] = {}
        self.total_attempts: Dict[str, int] = {}
        self.total_retry_sleep_s: Dict[str, float] = {}
        self.last_tick_time: Optional[float] = None
        self.last_successful_scans: Dict[str, str] = {}
        self.consecutive_failed_scans: Dict[str, int] = {}

    def get_run_id(self) -> str:
        return self.run_id

    def record_429(self, date_str: str, scan_id: Optional[str] = None):
        with self.lock:
            self.daily_429_count[date_str] = self.daily_429_count.get(date_str, 0) + 1
            if scan_id:
                self.scan_429_count[scan_id] = self.scan_429_count.get(scan_id, 0) + 1

    def record_attempt(self, scan_id: Optional[str], sleep_sec: float = 0.0):
        if not scan_id:
            return
        with self.lock:
            self.total_attempts[scan_id] = self.total_attempts.get(scan_id, 0) + 1
            if sleep_sec > 0:
                self.total_retry_sleep_s[scan_id] = self.total_retry_sleep_s.get(scan_id, 0.0) + sleep_sec

    def get_scan_telemetry(self, scan_id: str, date_str: str) -> Tuple[int, int, int, float]:
        with self.lock:
            s_429 = self.scan_429_count.get(scan_id, 0)
            d_429 = self.daily_429_count.get(date_str, 0)
            att = self.total_attempts.get(scan_id, 0)
            slp = round(self.total_retry_sleep_s.get(scan_id, 0.0), 2)
            return s_429, d_429, att, slp

    def check_daemon_gap(self, threshold_sec: float = 120.0) -> Optional[float]:
        """Detects if the main daemon loop stalled or slept for > threshold_sec."""
        now = time.time()
        with self.lock:
            if self.last_tick_time is None:
                self.last_tick_time = now
                return None
            elapsed = now - self.last_tick_time
            self.last_tick_time = now
            if elapsed > threshold_sec:
                return round(elapsed, 2)
            return None


_STATE = RunLoggerState()


def get_current_run_id() -> str:
    return _STATE.get_run_id()


# ---------------------------------------------------------------------------
# Structured Logging API (§A, §B, §C, §D, §E)
# ---------------------------------------------------------------------------

def log_event(
    event: str,
    market: Optional[str] = None,
    run_id: Optional[str] = None,
    scan_id: Optional[str] = None,
    level: str = "INFO",
    component: str = "general",
    message: str = "",
    base_dir: str = "logs/run",
    dt: Optional[datetime] = None,
    **kwargs
):
    """
    Appends an event object to logs/run/<date>/events.jsonl.
    """
    ts_utc, ts_local = get_iso_timestamps(dt=dt, market=market)
    date_str = get_exchange_date_str(market=market, dt_utc=dt)
    target_path = os.path.join(base_dir, date_str, "events.jsonl")

    record = {
        "ts_utc": ts_utc,
        "ts_local": ts_local,
        "market": (market or "ALL").upper(),
        "run_id": run_id or get_current_run_id(),
        "scan_id": scan_id,
        "level": level.upper(),
        "component": component,
        "event": event,
        "message": message,
    }
    record.update(kwargs)
    safe_append_jsonl(target_path, record)


def log_yf_call(
    symbol: str,
    interval: str,
    period: str,
    attempt: int,
    latency_ms: float,
    outcome: str,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    rows_returned: int = 0,
    scan_id: Optional[str] = None,
    market: Optional[str] = None,
    base_dir: str = "logs/run",
):
    """Logs an external yfinance call event (§D)."""
    date_str = get_exchange_date_str(market=market)
    is_429 = False
    if error_class and ("429" in error_class or "TooManyRequests" in error_class):
        is_429 = True
    elif error_message and ("429" in error_message or "Too Many Requests" in error_message):
        is_429 = True

    if is_429:
        _STATE.record_429(date_str, scan_id)

    _STATE.record_attempt(scan_id, sleep_sec=0.0)

    log_event(
        event="yf_call",
        market=market,
        scan_id=scan_id,
        level="ERROR" if outcome == "ERROR" else "INFO",
        component="yfinance",
        message=f"yfinance call {symbol} ({interval}, {period}): {outcome}",
        base_dir=base_dir,
        symbol=symbol,
        interval=interval,
        period=period,
        attempt=attempt,
        latency_ms=round(latency_ms, 2),
        outcome=outcome,
        error_class=error_class,
        error_message=error_message[:200] if error_message else None,
        rows_returned=rows_returned,
    )


def log_yf_retry_sleep(
    symbol: str,
    sleep_seconds: float,
    attempt: int,
    scan_id: Optional[str] = None,
    market: Optional[str] = None,
    base_dir: str = "logs/run",
):
    """Logs a yfinance retry backoff sleep event (§D)."""
    _STATE.record_attempt(scan_id, sleep_sec=sleep_seconds)
    log_event(
        event="yf_retry_sleep",
        market=market,
        scan_id=scan_id,
        level="WARNING",
        component="yfinance",
        message=f"Backoff sleep {sleep_seconds}s for {symbol} (attempt {attempt})",
        base_dir=base_dir,
        symbol=symbol,
        sleep_seconds=sleep_seconds,
        attempt=attempt,
    )


def log_telegram_send(
    bot_label: str,
    chat_label: str,
    message_type: str,
    chunk_count: int,
    http_status: Optional[int],
    latency_ms: float,
    error_text: Optional[str] = None,
    scan_id: Optional[str] = None,
    market: Optional[str] = None,
    base_dir: str = "logs/run",
):
    """Logs an external Telegram dispatch event (§D)."""
    success = http_status is not None and 200 <= http_status < 300
    level = "INFO" if success else "ERROR"
    log_event(
        event="telegram_send",
        market=market,
        scan_id=scan_id,
        level=level,
        component="telegram",
        message=f"Telegram send ({message_type}) to {chat_label}: {'OK' if success else 'FAILED'}",
        base_dir=base_dir,
        bot_label=bot_label,
        chat_label=chat_label,
        message_type=message_type,
        chunk_count=chunk_count,
        http_status=http_status,
        latency_ms=round(latency_ms, 2),
        error_text=error_text[:200] if error_text else None,
    )


def log_symbol_trace_batch(
    rows: List[dict],
    market: str,
    scan_id: str,
    base_dir: str = "logs/run",
    dt: Optional[datetime] = None,
):
    """
    Appends full per-symbol decision traces for a scan cycle to logs/run/<date>/symbol_trace.jsonl (§B).
    """
    if not rows:
        return
    date_str = get_exchange_date_str(market=market, dt_utc=dt)
    target_path = os.path.join(base_dir, date_str, "symbol_trace.jsonl")
    safe_append_jsonl_batch(target_path, rows)


def log_scan_summary(
    summary: dict,
    market: str,
    base_dir: str = "logs/run",
    dt: Optional[datetime] = None,
):
    """
    Appends a scan cycle summary row to logs/run/<date>/scan_summary.jsonl (§C).
    """
    date_str = get_exchange_date_str(market=market, dt_utc=dt)
    target_path = os.path.join(base_dir, date_str, "scan_summary.jsonl")
    safe_append_jsonl(target_path, summary)


# ---------------------------------------------------------------------------
# Startup Snapshot (§A)
# ---------------------------------------------------------------------------

def log_startup_snapshot(
    market: str = "ALL",
    config_path: str = "config/default.yaml",
    cfg: Optional[Dict[str, Any]] = None,
    watchlist_file: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    component: str = "daemon",
    base_dir: str = "logs/run",
    schedule_jobs: Optional[List[Any]] = None,
    market_session: Optional[Any] = None,
):
    """
    Logs the comprehensive startup snapshot to events.jsonl under event 'daemon_start'.
    """
    try:
        import pandas as pd
        pandas_version = getattr(pd, "__version__", "unknown")
    except Exception:
        pandas_version = "unknown"

    try:
        import yfinance as yf
        yfinance_version = getattr(yf, "__version__", "unknown")
    except Exception:
        yfinance_version = "unknown"

    try:
        import schedule
        schedule_version = getattr(schedule, "__version__", "unknown")
    except Exception:
        schedule_version = "unknown"

    try:
        import pytz
        pytz_version = getattr(pytz, "__version__", "unknown")
    except Exception:
        pytz_version = "unknown"

    # Git SHA
    git_sha = "unknown"
    try:
        import subprocess
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            git_sha = res.stdout.strip()
    except Exception:
        pass

    # Hostname (not IP)
    hostname = socket.gethostname()

    # Timezone & UTC Offset
    local_now = datetime.now()
    system_tz = time.tzname[time.daylight] if (time.daylight and len(time.tzname) > 1) else time.tzname[0]
    utc_offset = (datetime.now() - datetime.utcnow()).total_seconds() / 3600.0

    # Config & Config Hash
    if cfg is None:
        try:
            from breakout_scanner.config import load_config
            cfg = load_config(config_path) if os.path.exists(config_path) else {}
        except Exception:
            cfg = {}

    from breakout_scanner.audit_ledger import compute_config_hash
    config_hash = compute_config_hash(cfg)

    # Effective Config Subset
    c_timeframe = cfg.get("timeframe", {})
    c_break = cfg.get("breakout", {})
    c_risk = cfg.get("risk", {})
    c_qual = cfg.get("signal_quality", {})
    c_earn = cfg.get("earnings", {})
    c_health = cfg.get("data_health", {})
    c_conf = cfg.get("confirmation_policy", {})
    c_safe = cfg.get("safeguards", {})
    c_rej = cfg.get("rejection_gate", {})

    min_auto_score = int(c_qual.get("min_auto_entry_score", c_conf.get("min_auto_entry_score", 70)))
    stale_cap = int(c_safe.get("max_stale_data_seconds", c_risk.get("max_stale_data_seconds", 600)))
    conf_enabled = bool(c_conf.get("enabled", c_break.get("use_open_confirmation", False)))
    shorts_enabled = bool(c_break.get("allow_shorts", True))

    effective_config = {
        "timeframe": c_timeframe.get("interval", "1h"),
        "allow_shorts": shorts_enabled,
        "shorts_enabled": shorts_enabled,
        "slippage_pct": c_risk.get("slippage_pct", 0.05),
        "commission_pct": c_risk.get("commission_pct", 0.05),
        "max_friction_r": c_risk.get("max_friction_r", 0.15),
        "cost_model": {
            "slippage_pct": float(c_risk.get("slippage_pct", 0.05)),
            "commission_pct": float(c_risk.get("commission_pct", 0.05)),
            "max_friction_r": float(c_risk.get("max_friction_r", 0.15)),
        },
        "min_auto_entry_score": min_auto_score,
        "confirmation_policy": {
            "enabled": conf_enabled,
            "min_auto_entry_score": int(c_conf.get("min_auto_entry_score", min_auto_score)),
        },
        "max_stale_data_seconds": stale_cap,
        "extension_tolerance": {
            "enforce_anti_chase": bool(c_rej.get("enforce_anti_chase", True)),
            "max_extension_pct": float(c_rej.get("max_extension_pct", 1.0)),
        },
        "earnings_unverified_policy": c_earn.get("unverified_policy", "warn"),
        "data_health": {
            "min_scan_completeness_pct": c_health.get("min_scan_completeness_pct", 90),
            "abort_after_consecutive_empty": c_health.get("abort_after_consecutive_empty", 8),
        },
        "risk_settings": {
            "max_stop_pct": c_risk.get("max_stop_pct", 5.0),
            "min_stop_pct": c_risk.get("min_stop_pct", 0.5),
            "atr_multiplier": c_risk.get("atr_multiplier", 1.5),
        },
        "universe_size": len(symbols) if symbols is not None else 0,
    }

    # Watchlist File & SHA256
    wl_path = watchlist_file or "config/watchlist.txt"
    wl_count = len(symbols) if symbols is not None else 0
    wl_sha256 = "unknown"
    if os.path.exists(wl_path):
        try:
            with open(wl_path, "r", encoding="utf-8") as f:
                content = f.read()
                wl_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if symbols is None:
                    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
                    wl_count = len(lines)
        except Exception:
            pass

    # Next 5 jobs from schedule
    next_jobs = []
    if schedule_jobs:
        next_jobs = [str(j) for j in schedule_jobs[:5]]
    else:
        try:
            import schedule
            next_jobs = [str(j) for j in schedule.jobs[:5]]
        except Exception:
            pass

    # Market session flags
    is_trading_day = True
    session_open_str = None
    session_close_str = None
    if market_session is not None:
        try:
            now_utc = datetime.now(timezone.utc)
            is_trading_day = market_session.is_trading_day(now_utc.date())
        except Exception:
            pass

    snapshot_data = {
        "git_sha": git_sha,
        "versions": {
            "python": sys.version.split()[0],
            "pandas": pandas_version,
            "yfinance": yfinance_version,
            "schedule": schedule_version,
            "pytz": pytz_version,
        },
        "hostname": hostname,
        "system_timezone": system_tz,
        "utc_offset_hours": utc_offset,
        "config_file_path": config_path,
        "config_hash": config_hash,
        "effective_config": effective_config,
        "watchlist": {
            "path": wl_path,
            "symbol_count": wl_count,
            "sha256": wl_sha256,
        },
        "schedule": {
            "next_5_jobs": next_jobs,
            "is_trading_day": is_trading_day,
            "session_open": session_open_str,
            "session_close": session_close_str,
        },
    }

    log_event(
        event="daemon_start",
        market=market,
        level="INFO",
        component=component,
        message=f"Daemon startup initialized on {hostname} (Git: {git_sha[:8]}, Hash: {config_hash})",
        base_dir=base_dir,
        **snapshot_data,
    )


# ---------------------------------------------------------------------------
# Log Retention & Compression (§G)
# ---------------------------------------------------------------------------

def rotate_and_clean_logs(base_dir: str = "logs/run", retention_days: int = 14):
    """
    Compresses .jsonl files older than 1 day to .jsonl.gz and deletes runs older than retention_days.
    """
    if not os.path.exists(base_dir):
        return

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    try:
        entries = os.listdir(base_dir)
        for entry in entries:
            entry_path = os.path.join(base_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            # Verify if directory matches YYYY-MM-DD
            try:
                entry_date = datetime.strptime(entry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            age_days = (now_utc - entry_date).days

            # Delete directories older than retention_days
            if age_days > retention_days:
                shutil.rmtree(entry_path, ignore_errors=True)
                continue

            # Gzip .jsonl files for sessions completed prior to today
            if entry != today_str:
                for fname in os.listdir(entry_path):
                    if fname.endswith(".jsonl"):
                        fpath = os.path.join(entry_path, fname)
                        gz_path = fpath + ".gz"
                        if not os.path.exists(gz_path):
                            try:
                                with open(fpath, "rb") as f_in:
                                    with gzip.open(gz_path, "wb") as f_out:
                                        shutil.copyfileobj(f_in, f_out)
                                os.remove(fpath)
                            except Exception:
                                pass
    except Exception as e:
        sys.stderr.write(f"⚠️ [RUN_LOGGER ERROR] Failed to rotate logs in {base_dir}: {e}\n")
        sys.stderr.flush()
