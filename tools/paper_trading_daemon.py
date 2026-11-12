"""
Phase 5: Paper Trading Daemon Runner & Auditor Tool
===================================================
Runs shadow paper execution across historical feeds or live intervals,
records orders, slippage, and performance ledgers, and generates the
Go/No-Go release audit report.

Usage:
    python3 tools/paper_trading_daemon.py --replay
    python3 tools/paper_trading_daemon.py --audit
"""

import os
import sys
import argparse
from typing import Dict
import pandas as pd

# Ensure project root in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from breakout_scanner.config import load_config
from breakout_scanner.paper_trading.daemon import PaperTradingDaemon
from breakout_scanner.paper_trading.audit import GoNoGoAuditor


def run_paper_replay(
    cache_dir: str = "backtest/cache",
    fixture_dir: str = "tests/fixtures/golden_dataset",
    max_symbols: int = 10,
    report_output: str = "reports/paper_trading_audit_report.md"
):
    """Replays historical closed candles through PaperTradingDaemon and audits results."""
    cfg = load_config("config/default.yaml")
    cfg['portfolio_guard']['enabled'] = True
    cfg['rejection_gate']['enabled'] = True
    cfg.setdefault('breakout', {})['use_extension_filter'] = True
    cfg['breakout']['max_chase_pct'] = 2.0

    daemon = PaperTradingDaemon(cfg)
    symbols_data: Dict[str, pd.DataFrame] = {}

    preferred = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "QCOM", "CSCO", "ORCL"]

    if os.path.isdir(cache_dir):
        for sym in preferred:
            for fname in sorted(os.listdir(cache_dir)):
                if fname.startswith(f"{sym}_") and fname.endswith(".pkl"):
                    p = os.path.join(cache_dir, fname)
                    try:
                        df = pd.read_pickle(p)
                        if not df.empty:
                            symbols_data[sym] = df.iloc[-280:]
                            break
                    except Exception:
                        pass
            if len(symbols_data) >= max_symbols:
                break

    if len(symbols_data) < 7 and os.path.isdir(fixture_dir):
        for fname in sorted(os.listdir(fixture_dir)):
            if fname.endswith("_1h.pkl") and not fname.startswith("QQQ"):
                sym = fname.split("_")[0]
                p = os.path.join(fixture_dir, fname)
                try:
                    df = pd.read_pickle(p)
                    if not df.empty and sym not in symbols_data:
                        symbols_data[sym] = df.iloc[-280:]
                except Exception:
                    pass
            if len(symbols_data) >= max_symbols:
                break

    print(f"Loaded {len(symbols_data)} symbols for Paper Trading Replay.")
    summary = daemon.replay_dataset(symbols_data)
    print(f"Paper Replay Complete: {summary}")

    auditor = GoNoGoAuditor(min_completed_trades=8)
    audit_report = auditor.evaluate_performance(
        completed_trades=daemon.completed_trades,
        variance_records=daemon.variance_records,
        incident_ledger=daemon.incident_ledger
    )

    report_md = auditor.write_markdown_report(
        audit_report,
        output_path=report_output,
        incident_ledger=daemon.incident_ledger
    )
    print(f"Paper Trading Audit Report generated at: {report_output}")
    print(f"Overall Release Verdict: {audit_report.overall_verdict}")
    return report_md


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase 5 Paper Trading Daemon and Release Gate Audit")
    parser.add_argument("--replay", action="store_true", default=True, help="Run shadow paper replay simulation")
    parser.add_argument("--output", type=str, default="reports/paper_trading_audit_report.md")
    parser.add_argument("--max-symbols", type=int, default=10)
    args = parser.parse_args()

    run_paper_replay(report_output=args.output, max_symbols=args.max_symbols)

