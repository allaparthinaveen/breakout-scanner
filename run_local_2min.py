#!/usr/bin/env python3
"""
Local 2-Minute Accelerated Validation Runner (run_local_2min.py)
==============================================================
Runs the breakout scheduler locally every 2 minutes with force=True,
dispatching Section 2A + 2B signals to Telegram so you can validate 
alerts end-to-end without waiting for 1-hour cloud candle closes.

Usage:
    ./venv/bin/python run_local_2min.py
    ./venv/bin/python run_local_2min.py --market US
    ./venv/bin/python run_local_2min.py --market NSE
"""

import os
import sys
import time
import argparse
import schedule
from breakout_scanner.scheduler import SessionScheduler

def main():
    parser = argparse.ArgumentParser(description="Accelerated 2-minute local validation runner")
    parser.add_argument("--market", type=str, default="US", help="Market to scan (US, NSE, CRYPTO)")
    parser.add_argument("--config", type=str, default="config/default.yaml", help="Config file path")
    parser.add_argument("--interval", type=int, default=2, help="Interval in minutes between scans (default: 2)")
    args = parser.parse_args()

    market = args.market.upper()
    print("=" * 65)
    print(f"🚀 LOCAL ACCELERATED SCANNER ({args.interval}-MIN TEST LOOP)")
    print(f"• Market:     {market}")
    print(f"• Config:     {args.config}")
    print(f"• Cadence:    Every {args.interval} minutes")
    print("• Press Ctrl+C anytime to stop.")
    print("=" * 65)

    scheduler = SessionScheduler(
        cfg_path=args.config,
        market_id=market
    )

    def _execute_scan():
        print(f"\n⚡ [{time.strftime('%H:%M:%S')}] Triggering accelerated {args.interval}m validation scan...")
        res = scheduler.scan_and_alert(force=True)
        print(f"✅ Scan finished: Scanned={res.get('scanned')} | Alerts Dispatched={res.get('alerts_sent')} in {res.get('elapsed_seconds')}s")

    # 1. Run immediately on start
    _execute_scan()

    # 2. Schedule recurring every N minutes
    schedule.every(args.interval).minutes.do(_execute_scan)
    print(f"\n⏳ Scheduled to repeat every {args.interval} minutes. Waiting for next trigger...")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopped accelerated validation loop.")

if __name__ == "__main__":
    main()
