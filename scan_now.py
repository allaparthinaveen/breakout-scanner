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
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
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

def build_report_string(setups):
    lines = []
    lines.append("<code>=======================")
    lines.append(" SECTION 1: VCP BASE")
    lines.append("=======================")
    
    base_setups = [s for s in setups if s.state == State.CONTRACTION]
    if not base_setups:
        lines.append("  (None)")
    else:
        for s in base_setups:
            bias = "Possible Upside" if s.trend == 1 else "Possible Downside"
            lines.append(f"{s.symbol:9} | Res: {s.resistance:.5g} | Sup: {s.support:.5g} | Bias: {bias}")

    lines.append("")
    lines.append("=======================")
    lines.append(" SECTION 2: NEW SIGNALS")
    lines.append("=======================")
    
    # ONLY show signals that triggered in the last 24 bars (last 24 hours of trading)
    active_setups = [s for s in setups if s.state in (State.BUY, State.SELL) and (s.bars_since_breakout is not None and s.bars_since_breakout <= 24)]
    active_setups.sort(key=lambda s: abs(s.entry - s.current_price) / s.entry if s.entry and s.current_price else float('inf'))
    
    if not active_setups:
        lines.append("  (None)")
    else:
        for s in active_setups:
            dir_str = "BUY " if s.direction == 1 else "SELL"
            lines.append(f"{s.symbol:6} | {dir_str} | In: {s.entry:.5g} | SL: {s.stop:.5g}")
            
    lines.append("</code>")
    return "\n".join(lines)

def scan_now():
    parser = argparse.ArgumentParser(description='Run breakout scanner')
    parser.add_argument('--watchlist', type=str, default='config/crypto_watchlist.txt', help='Path to watchlist file')
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
