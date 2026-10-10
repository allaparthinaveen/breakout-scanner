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
    report_lines.append("<b>🌅 PRE-MARKET BREAKOUT SCANNER</b>\n")
    
    report_lines.append("<b>📦 STOCKS FORMING A BOX (CONSOLIDATION)</b>")
    if not forming_box.empty:
        report_lines.append("<pre>" + forming_box[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False) + "</pre>")
    else:
        report_lines.append("<i>None currently forming a box.</i>")
        
    report_lines.append("\n<b>💥 ACTIVELY BREAKING OUT (ACCEPTANCE)</b>")
    if not breaking_out.empty:
        report_lines.append("<pre>" + breaking_out[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False) + "</pre>")
    else:
        report_lines.append("<i>None currently breaking out.</i>")
        
    awaiting_retest = df_results[df_results['State'] == 'ACCEPTED_RETEST_WATCH']
    report_lines.append("\n<b>⏳ AWAITING RETEST (Watch closely!)</b>")
    if not awaiting_retest.empty:
        report_lines.append("<pre>" + awaiting_retest[['Symbol', 'Trend', 'Resistance', 'Support']].to_string(index=False) + "</pre>")
    else:
        report_lines.append("<i>None currently awaiting retest.</i>")

    entry_signals = df_results[df_results['State'].isin(['BUY', 'SELL'])]
    report_lines.append("\n<b>🎯 ENTRY SIGNALS TRIGGERED!</b>")
    if not entry_signals.empty:
        report_lines.append("<pre>" + entry_signals[['Symbol', 'State', 'Resistance', 'Support']].to_string(index=False) + "</pre>")
    else:
        report_lines.append("<i>No active entry triggers.</i>")
        
    idle = df_results[df_results['State'].isin(['IDLE', 'FAILED'])]
    report_lines.append("\n<b>💤 IDLE / FAILED STOCKS (Ignore today)</b>")
    if not idle.empty:
        report_lines.append("<code>" + ", ".join(idle['Symbol'].tolist()) + "</code>")
    else:
        report_lines.append("<i>None.</i>")
        
    final_report = "\n".join(report_lines)
    clean_terminal = final_report.replace('<b>', '').replace('</b>', '').replace('<pre>', '\n').replace('</pre>', '').replace('<i>', '').replace('</i>', '').replace('<code>', '').replace('</code>', '')
    print("\n" + clean_terminal)
    
    # Send to Telegram
    send_telegram_message(final_report)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Pre-Market Scanner")
    parser.add_argument('--watchlist', type=str, default='config/watchlist.txt', help='Path to watchlist file')
    args = parser.parse_args()
    
    run_pre_market_scan(args.watchlist)
