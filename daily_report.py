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
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("\n⚠️ Telegram credentials not found in .env file. Skipping Telegram notification.")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"```\n{message}\n```",
        "parse_mode": "MarkdownV2"
    }
    # Escape some markdown V2 reserved chars if present in raw string
    for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        if char != '`' and char != '\n': # Keep code block backticks
             payload["text"] = payload["text"].replace(char, f"\\{char}")
    # Re-wrap properly with backticks
    payload["text"] = f"```\n{message.replace('`', '')}\n```"

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
        symbols = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
    print(f"Scanning {len(symbols)} symbols on the 1-hour timeframe...\n")
    
    results = []
    
    for sym in symbols:
        df = provider.candles(sym, period='60d', interval='1h')
        if df.empty or len(df) < 100:
            continue
            
        engine = BreakoutEngine(cfg, sym)
        setup = engine.run(df)
        
        results.append({
            'Symbol': sym,
            'State': setup.state.name,
            'Trend': "UP" if setup.trend == 1 else "DOWN" if setup.trend == -1 else "FLAT",
            'Resistance': f"${setup.resistance:.2f}" if setup.resistance else "-",
            'Support': f"${setup.support:.2f}" if setup.support else "-"
        })
        
    # Group results
    df_results = pd.DataFrame(results)
    
    forming_box = df_results[df_results['State'] == 'CONSOLIDATION']
    breaking_out = df_results[df_results['State'] == 'ACCEPTANCE_WATCH']
    
    report_lines = []
    report_lines.append("🌅 PRE-MARKET BREAKOUT SCANNER")
    report_lines.append("==============================")
    
    report_lines.append("\n📦 STOCKS FORMING A BOX (CONSOLIDATION)")
    report_lines.append("==============================")
    if not forming_box.empty:
        report_lines.append(forming_box[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False))
    else:
        report_lines.append("None currently forming a box.")
        
    report_lines.append("\n💥 ACTIVELY BREAKING OUT (ACCEPTANCE)")
    report_lines.append("==============================")
    if not breaking_out.empty:
        report_lines.append(breaking_out[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False))
    else:
        report_lines.append("None currently breaking out.")
        
    final_report = "\n".join(report_lines)
    print("\n" + final_report)
    
    # Send to Telegram
    send_telegram_message(final_report)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Pre-Market Scanner")
    parser.add_argument('--watchlist', type=str, default='config/watchlist.txt', help='Path to watchlist file')
    args = parser.parse_args()
    
    run_pre_market_scan(args.watchlist)
