import os
import argparse
import requests
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
    
    # Split message into chunks if larger than 4000 characters
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
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            print(f"\n❌ Failed to send Telegram message: {e}")
            return
            
    print("\n📱 Successfully sent report to Telegram!")

from breakout_scanner.telegram_formatter import format_scan_report_html

def build_report_string(setups):
    return format_scan_report_html(setups)

def scan_now():
    parser = argparse.ArgumentParser(description="Run breakout scanner on a watchlist")
    parser.add_argument(
        "--watchlist",
        type=str,
        default="config/watchlist.txt",
        help="Path to watchlist file (defaults to config/watchlist.txt)",
    )
    args = parser.parse_args()

    print("Loading config...")
    cfg = load_config('config/default.yaml')
    provider = YFinanceProvider()
    
    filename = args.watchlist
    symbols = []
    
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            for line in f:
                sym = line.split(',')[0].strip()
                if sym and not sym.startswith('#') and sym not in symbols:
                    symbols.append(sym)
    else:
        print(f"File {filename} not found.")
        return

    print(f"Scanning {len(symbols)} symbols from {filename}...")
    
    from breakout_scanner.scanner import MultiSymbolScanner
    scanner = MultiSymbolScanner(provider, cfg)
    
    # Run scanner and grab results
    setups = scanner.scan(symbols, period='60d', interval='1h')
    
    # Print the CLI colored report
    scanner.report(setups)
    
    # Build HTML string and send via Telegram
    report_html = build_report_string(setups)
    send_telegram_message(report_html)
            
    print("Scan complete.")

if __name__ == "__main__":
    scan_now()
