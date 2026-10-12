import argparse
import pandas as pd
import requests
import os
from dotenv import load_dotenv
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.models import State

load_dotenv()

def send_telegram_message(message: str):
    token = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("\n⚠️ Telegram credentials not found in .env file. Skipping Telegram notification.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("\n📱 Successfully sent report to Telegram!")
    except Exception as e:
        print(f"\n❌ Failed to send Telegram message: {e}")

def run_pre_market_scan(watchlist_file: str):
    print("🌅 Running Pre-Market Breakout Scanner...")
    
    cfg = load_config('config/default.yaml')
    provider = YFinanceProvider()
    
    with open(watchlist_file, 'r') as f:
        symbols = [line.split(',')[0].strip() for line in f if line.strip() and not line.strip().startswith('#')]
        
    print(f"Scanning {len(symbols)} symbols on the 1-hour timeframe...\n")
    
    from breakout_scanner.scanner import MultiSymbolScanner
    scanner = MultiSymbolScanner(provider, cfg)
    setups = scanner.scan(symbols, period='60d', interval='1h')
    
    forming_box = [s for s in setups if s.state == State.CONTRACTION and s.bias_score >= 3]
    forming_box.sort(key=lambda s: (s.composite_score, s.bias_score), reverse=True)
    breaking_out = [s for s in setups if s.state in (State.BUY, State.SELL) and not s.retest_confirmed]
    breaking_out.sort(key=lambda s: (s.composite_score, s.bias_score), reverse=True)
    retesting = [s for s in setups if s.state in (State.BUY, State.SELL) and s.retest_confirmed]
    retesting.sort(key=lambda s: (s.composite_score, s.bias_score), reverse=True)
    idle = [s for s in setups if s.state in (State.IDLE, State.FAILED)]
    
    from breakout_scanner.telegram_formatter import format_daily_briefing_html
    final_report = format_daily_briefing_html(forming_box, breaking_out, retesting, idle)
    clean_terminal = final_report.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '').replace('<code>', '').replace('</code>', '')
    print("\n" + clean_terminal)
    
    # Send to Telegram
    send_telegram_message(final_report)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Pre-Market Scanner")
    parser.add_argument('--watchlist', type=str, default='config/watchlist.txt', help='Path to watchlist file')
    args = parser.parse_args()
    
    run_pre_market_scan(args.watchlist)
