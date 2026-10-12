import os
import argparse
import logging
from typing import Optional, Union

from breakout_scanner.models import Setup, State
from breakout_scanner.scheduler import SessionScheduler, format_instant_alert


class LiveScanner(SessionScheduler):
    """
    Backwards-compatible wrapper around SessionScheduler for live background scanning.
    """
    def __init__(self, cfg_path: str = "config/default.yaml", **kwargs):
        super().__init__(cfg_path=cfg_path, **kwargs)

    def format_telegram_message(self, setup: Setup) -> str:
        return format_instant_alert(setup, timeframe="1H")

    def send_telegram_alert(self, setup_or_msg: Union[Setup, str]) -> bool:
        if isinstance(setup_or_msg, Setup):
            # If current price is not set, fallback to entry
            if setup_or_msg.current_price is None and setup_or_msg.entry is not None:
                setup_or_msg.current_price = setup_or_msg.entry
            msg = format_instant_alert(setup_or_msg, timeframe="1H")
        else:
            msg = str(setup_or_msg)
        return super().send_telegram_alert(msg)

    def run_scan(self) -> dict:
        return self.scan_and_alert()


def main():
    parser = argparse.ArgumentParser(
        description="Phase 2 Automated Session Scheduler & Background Daemon (Timing is Edge)"
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously as a background daemon at :01 past every hour",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="Execute an immediate scan right now",
    )
    parser.add_argument(
        "--minute-offset",
        type=int,
        default=1,
        help="Minute past the hour to run candle close scan (default: 1 for :01)",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="Enable NSE & US market session specific schedules in addition to hourly :01",
    )
    parser.add_argument(
        "--watchlists",
        type=str,
        default=None,
        help="Comma-separated paths to watchlist files (e.g. config/watchlist.txt,config/crypto_watchlist.txt)",
    )
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Send a test instant push alert to Telegram to verify formatting and connection",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the deduplication alert cache",
    )
    args = parser.parse_args()

    watchlists = [f.strip() for f in args.watchlists.split(",")] if args.watchlists else None
    scanner = LiveScanner(watchlist_files=watchlists)

    if args.clear_cache:
        scanner.deduplicator.clear()
        print("🧹 Alert deduplication cache cleared.")

    if args.test_alert:
        print("📨 Sending test instant push alert to Telegram...")
        test_setup = Setup(
            symbol="DOT-USD",
            state=State.BUY,
            direction=1,
            entry=1.2815,
            stop=1.2650,
            current_price=1.2818,
            risk_pct=1.2,
            composite_grade="B",
            composite_score=79,
            tp1=1.3012,
            tp2=1.3209,
            sector_name="Crypto",
            sector_rs_pct=1.4,
            rs_trend="↗",
            rvol=2.1,
            htf_confirmed=True,
            breakout_type="BREAKOUT"
        )
        success = scanner.send_telegram_alert(test_setup)
        if success:
            print("✅ Test alert successfully dispatched to Telegram!")
        else:
            print("❌ Failed to dispatch test alert. Check .env credentials.")
        return

    # If --daemon is specified, set up hourly schedule and run
    if args.daemon:
        scanner.schedule_hourly(minute_offset=args.minute_offset)
        if args.sessions:
            scanner.schedule_session_times()
        run_now = args.now or True  # By default run on startup so user sees immediate feedback
        scanner.start(run_immediately=run_now)
    else:
        # One-off scan mode
        print("⚡ Running immediate candle scan...")
        res = scanner.run_scan()
        print(f"✅ Scan finished: {res}")


def start_scheduler():
    scanner = LiveScanner()
    scanner.schedule_hourly(minute_offset=1)
    scanner.start(run_immediately=True)


if __name__ == "__main__":
    main()
