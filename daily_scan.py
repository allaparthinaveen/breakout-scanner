#!/usr/bin/env python3
"""
Daily Automated Scanning Pipeline (daily_scan.py)
=================================================
Executes the institutional daily scanning pipeline:
1. Fetch the eligible stock universe (US, NSE, or custom)
2. Calculate liquidity rankings using completed historical sessions (ADV_20, price, data quality)
3. Apply hysteresis stability buffer and sector caps to generate today's 30-stock watchlist
4. Save the active watchlist, JSON ranking metadata, and historical reproducible snapshot
5. Run your existing breakout scanner with the selected configuration
6. Generate the final actionable trade slate with exact position sizing and risk controls

Usage:
    python3 daily_scan.py --market us
    python3 daily_scan.py --market nse --config config/default.yaml
    python3 daily_scan.py --market us --target-size 30 --no-tg
"""

import os
import sys
import argparse
import requests
import datetime
from typing import Optional, List, Dict, Any
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Ensure root in sys.path
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from breakout_scanner.config import load_config
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.universe.engine import DynamicLiquidityEngine
from breakout_scanner.universe.models import UniverseSelectionResult
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.portfolio_guard import generate_actionable_slate, format_trade_slate_cli
from breakout_scanner.telegram_formatter import format_scan_report_html

load_dotenv()


def send_telegram_notification(message: str):
    """Sends scan report to Telegram bot channel if credentials exist."""
    token = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("\n⚠️ Telegram credentials not found in .env file. Skipping Telegram notification.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
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

    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"\n❌ Telegram notification failed: {e}")
            return

    print("\n📱 Successfully sent report to Telegram!")


def run_daily_scan_pipeline(
    market: str = "US",
    config_path: str = "config/default.yaml",
    target_size: int = 30,
    interval: Optional[str] = None,
    period: Optional[str] = None,
    custom_universe: Optional[str] = None,
    send_tg: bool = True,
    dry_run: bool = False
):
    print("=" * 70)
    print(" 🏛️  INSTITUTIONAL DAILY SCANNING PIPELINE")
    print("=" * 70)
    print(f"  • Date:            {datetime.date.today().isoformat()}")
    print(f"  • Market:          {market.upper()}")
    print(f"  • Target Size:     Top {target_size} High-Liquidity Stocks")
    print(f"  • Strategy Config: {config_path}")
    print("=" * 70)

    cfg = load_config(config_path)
    cfg.setdefault("dynamic_universe", {})["target_size"] = target_size
    provider = YFinanceProvider()

    # -----------------------------------------------------------------
    # Step 1, 2, 3, 4: Dynamic Liquidity Universe Generation
    # -----------------------------------------------------------------
    print("\n[STEP 1 & 2] Fetching eligible broad universe & screening liquidity...")
    engine = DynamicLiquidityEngine(cfg)
    universe_result: UniverseSelectionResult = engine.run_selection(
        provider=provider,
        market=market,
        custom_watchlist=custom_universe,
    )

    print("\n" + "=" * 70)
    print(f" [STEP 3 & 4] DYNAMIC TOP {target_size} WATCHLIST GENERATED")
    print("=" * 70)
    print(f"  • Total Broad Eligible:   {universe_result.total_eligible}")
    print(f"  • Qualified Liquidity:    {universe_result.qualified_count}")
    print(f"  • Retained from Prior:    {len(universe_result.retained_symbols)} (Stability Buffer)")
    print(f"  • New Additions:          {len(universe_result.added_symbols)}")
    print(f"  • Dropped from Top 30:    {len(universe_result.removed_symbols)}")
    print(f"  • Churn / Turnover:       {universe_result.turnover_rate_pct:.1f}%")
    print(f"  • Watchlist Saved To:     {universe_result.watchlist_path}")
    print(f"  • Historical Snapshot:    {universe_result.snapshot_path}")

    if universe_result.added_symbols:
        print(f"  • 🟢 New Inclusions:      {', '.join(universe_result.added_symbols)}")
    if universe_result.removed_symbols:
        print(f"  • 🔴 Dropped Constituents: {', '.join(universe_result.removed_symbols)}")

    print("\n  📊 Sector Diversification Breakdown:")
    for sec, cnt in sorted(universe_result.sector_distribution.items(), key=lambda x: x[1], reverse=True):
        pct = universe_result.sector_concentration_pct.get(sec, 0.0)
        print(f"     - {sec:<24} : {cnt:>2} stocks ({pct:.1f}%)")

    selected_watchlist = universe_result.selected_symbols
    if not selected_watchlist:
        print("\n❌ No symbols qualified for liquidity watchlist. Aborting scan.")
        return

    # -----------------------------------------------------------------
    # Step 5: Existing Breakout Scanner Execution
    # -----------------------------------------------------------------
    scan_interval = interval or cfg.get('timeframe', {}).get('interval', '1h')
    scan_period = period or cfg.get('timeframe', {}).get('period', '60d')

    print("\n" + "=" * 70)
    print(f" [STEP 5] RUNNING BREAKOUT ENGINE ON TOP {len(selected_watchlist)} LIQUID CANDIDATES")
    print("=" * 70)
    print(f"  • Scanning {len(selected_watchlist)} stocks on {scan_interval.upper()} (Lookback: {scan_period})...")

    scanner = MultiSymbolScanner(provider, cfg)
    setups = scanner.scan(selected_watchlist, period=scan_period, interval=scan_interval)

    # Print standard multi-section CLI breakout report
    scanner.report(setups)

    # -----------------------------------------------------------------
    # Step 6: Actionable Trade Slate Generation
    # -----------------------------------------------------------------
    print("\n" + "=" * 70)
    print(" [STEP 6] INSTITUTIONAL ACTIONABLE TRADE SLATE (RISK GOVERNANCE)")
    print("=" * 70)

    slate = generate_actionable_slate(setups, config=cfg)
    print(format_trade_slate_cli(slate))

    # Send Telegram notification if enabled
    if send_tg and not dry_run:
        report_html = format_scan_report_html(setups)
        # Append liquidity universe summary header to Telegram text
        tg_header = (
            f"<b>🏛️ DAILY SCAN: TOP {target_size} {market.upper()} LIQUIDITY UNIVERSE</b>\n"
            f"📅 <i>{datetime.date.today().isoformat()}</i> | Churn: {universe_result.turnover_rate_pct:.1f}%\n"
            f"🎯 Actionable Trades: {len(slate.actionable_trades)} | Suppressed: {len(slate.suppressed_trades)}\n\n"
        )
        send_telegram_notification(tg_header + report_html)
    elif dry_run:
        print("\n[DRY RUN] Notifications skipped.")
    else:
        print("\n🔕 Telegram notification skipped (--no-tg specified).")

    print(f"\n✅ Pipeline Complete for {datetime.date.today().isoformat()}.")


def main():
    parser = argparse.ArgumentParser(description="Institutional Daily Breakout Pipeline with Dynamic Liquidity Universe")
    parser.add_argument("--market", type=str, default="us", choices=["us", "nse", "US", "NSE"], help="Target exchange market (default: us)")
    parser.add_argument("--config", type=str, default="config/default.yaml", help="Path to config yaml")
    parser.add_argument("--target-size", type=int, default=30, help="Target high-liquidity watchlist size (default: 30)")
    parser.add_argument("--interval", type=str, default=None, help="Candle timeframe (e.g. 1h, 1d)")
    parser.add_argument("--period", type=str, default=None, help="Lookback period (e.g. 60d, 730d)")
    parser.add_argument("--custom-universe", type=str, default=None, help="Optional custom universe watchlist file")
    parser.add_argument("--no-tg", action="store_true", help="Suppress Telegram alerts")
    parser.add_argument("--dry-run", action="store_true", help="Execute without sending notifications or writing persistent files")
    args = parser.parse_args()

    run_daily_scan_pipeline(
        market=args.market,
        config_path=args.config,
        target_size=args.target_size,
        interval=args.interval,
        period=args.period,
        custom_universe=args.custom_universe,
        send_tg=not args.no_tg,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
