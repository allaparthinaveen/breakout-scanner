#!/usr/bin/env python3
"""
One-Command Market Session Log Bundle Collector (§F.2).
======================================================
Bundles all logs, decision traces, audit ledgers, configuration snapshots,
system journal logs, and environment metadata for a market session into a
single redacted zip archive for reviewer analysis.

Usage:
  python3 scripts/collect_logs.py --market nse --date YYYY-MM-DD
"""

import os
import sys

# Ensure project root is in sys.path when executed directly as a script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import zipfile
import subprocess
import argparse
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from breakout_scanner.run_logger import (
    get_exchange_date_str,
    redact_secrets,
)
from scripts.print_effective_config import get_effective_config
from scripts.eod_summary import generate_eod_summary, format_eod_markdown


def collect_journalctl(date_str: str) -> str:
    """Attempts to capture the last 2000 lines of systemd journalctl, skipping gracefully if unavailable."""
    try:
        cmd = ["journalctl", "-u", "breakout-scanner", "-n", "2000"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout
        return f"journalctl returned code {res.returncode}: {res.stderr or 'No entries found.'}"
    except FileNotFoundError:
        return "journalctl not available on this system (macOS / non-systemd environment)."
    except Exception as e:
        return f"Failed to execute journalctl: {e}"


def collect_pip_freeze() -> str:
    """Captures pip freeze output."""
    try:
        res = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return "pip freeze unavailable"


def collect_git_info() -> str:
    """Captures git SHA and status."""
    info = []
    try:
        res_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3)
        info.append(f"GIT_SHA: {res_sha.stdout.strip() if res_sha.returncode == 0 else 'unknown'}")
        res_stat = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, timeout=3)
        info.append("\nGIT_STATUS:\n" + (res_stat.stdout.strip() if res_stat.returncode == 0 else 'unknown'))
    except Exception as e:
        info.append(f"git info error: {e}")
    return "\n".join(info)


def create_log_bundle(
    market: str,
    date_str: str,
    output_zip: Optional[str] = None,
    base_run_dir: str = "logs/run",
) -> str:
    market_upper = market.upper()
    zip_filename = output_zip or f"logs_bundle_{market.lower()}_{date_str}.zip"
    total_redactions = 0

    session_run_dir = os.path.join(base_run_dir, date_str)
    os.makedirs(session_run_dir, exist_ok=True)

    # Ensure EOD summary exists inside session_run_dir
    eod_json_path = os.path.join(session_run_dir, "eod_summary.json")
    eod_md_path = os.path.join(session_run_dir, "eod_summary.md")
    if not os.path.exists(eod_json_path) or not os.path.exists(eod_md_path):
        summary_data = generate_eod_summary(market=market, date_str=date_str, base_dir=base_run_dir)
        import json
        with open(eod_json_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, default=str)
        with open(eod_md_path, "w", encoding="utf-8") as f:
            f.write(format_eod_markdown(summary_data))

    # Read and sanitize files into memory
    archive_files = {}

    # 1. Run directory files
    if os.path.exists(session_run_dir):
        for fname in os.listdir(session_run_dir):
            fpath = os.path.join(session_run_dir, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    sanitized, count = redact_secrets(content)
                    total_redactions += count
                    archive_files[f"run/{date_str}/{fname}"] = sanitized
                except Exception as e:
                    sys.stderr.write(f"Warning reading {fpath}: {e}\n")

    # 2. Forward-test ledger and outcomes
    for ledger_f in ("logs/forward_test_ledger.jsonl", "logs/forward_test_outcomes.jsonl"):
        if os.path.exists(ledger_f):
            try:
                with open(ledger_f, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                sanitized, count = redact_secrets(content)
                total_redactions += count
                archive_files[os.path.basename(ledger_f)] = sanitized
            except Exception as e:
                sys.stderr.write(f"Warning reading {ledger_f}: {e}\n")

    # 3. Journalctl output
    jctl_text = collect_journalctl(date_str)
    jctl_clean, count = redact_secrets(jctl_text)
    total_redactions += count
    archive_files["journalctl_recent.txt"] = jctl_clean

    # 4. Effective config snapshot
    profile_map = {
        "NSE": "config/profiles/nse_production.yaml",
        "US": "config/profiles/us_production.yaml",
        "CRYPTO": "config/profiles/crypto_production.yaml",
    }
    cfg_path = profile_map.get(market_upper, "config/default.yaml")
    if not os.path.exists(cfg_path):
        cfg_path = "config/default.yaml"
    cfg_snapshot = get_effective_config(config_path=cfg_path)
    import json
    cfg_json = json.dumps(cfg_snapshot, indent=2)
    cfg_clean, count = redact_secrets(cfg_json)
    total_redactions += count
    archive_files["effective_config_snapshot.json"] = cfg_clean

    # 5. Pip freeze
    pip_text = collect_pip_freeze()
    pip_clean, count = redact_secrets(pip_text)
    total_redactions += count
    archive_files["pip_freeze.txt"] = pip_clean

    # 6. Git info
    git_text = collect_git_info()
    git_clean, count = redact_secrets(git_text)
    total_redactions += count
    archive_files["git_info.txt"] = git_clean

    # 7. README.txt
    readme_lines = [
        f"BREAKOUT SCANNER SESSION AUDIT BUNDLE",
        f"=====================================",
        f"Market: {market_upper}",
        f"Session Date: {date_str}",
        f"Generated At: {datetime.now(timezone.utc).isoformat()}",
        f"Redacted Secrets Masked: {total_redactions}",
        "",
        "CONTENTS:",
    ]
    for arcname in sorted(archive_files.keys()):
        readme_lines.append(f"  • {arcname}")
    readme_lines.extend([
        "",
        "PRIMARY FILES FOR REVIEW:",
        f"  1. run/{date_str}/eod_summary.md  - Executive session report & funnel totals",
        f"  2. run/{date_str}/scan_summary.jsonl - Per-scan completeness, latency, and stats",
        f"  3. run/{date_str}/symbol_trace.jsonl - Per-symbol 'why no signal' decision traces",
        f"  4. run/{date_str}/events.jsonl       - Chronological event timeline (calls, jobs, daemon)",
        f"  5. forward_test_ledger.jsonl         - Persistent signal ledger",
        f"  6. effective_config_snapshot.json    - Verified parameter hash & strategy rules",
    ])
    archive_files["README.txt"] = "\n".join(readme_lines)

    # Write zip file
    with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in archive_files.items():
            zf.writestr(arcname, data)

    file_size_bytes = os.path.getsize(zip_filename)
    file_size_mb = file_size_bytes / (1024 * 1024)

    print(f"\n🔒 Secrets redacted: {total_redactions} instances masked.")
    print(f"📦 Bundle created: {os.path.abspath(zip_filename)} ({file_size_mb:.2f} MB / {file_size_bytes:,} bytes)")
    return zip_filename


def main():
    parser = argparse.ArgumentParser(description="Collect all session logs into a redacted zip bundle")
    parser.add_argument("--market", type=str, default="nse", help="Market identifier (e.g. nse, us, crypto)")
    parser.add_argument("--date", type=str, default=None, help="Calendar date YYYY-MM-DD (defaults to today in market tz)")
    parser.add_argument("--output", type=str, default=None, help="Optional output zip file path")
    args = parser.parse_args()

    date_str = args.date or get_exchange_date_str(args.market)
    create_log_bundle(market=args.market, date_str=date_str, output_zip=args.output)


if __name__ == "__main__":
    main()
