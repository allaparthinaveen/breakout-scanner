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
    
    forming_box = [s for s in setups if s.state == State.CONTRACTION]
    breaking_out = [s for s in setups if s.state in (State.BUY, State.SELL) and not s.retest_confirmed]
    retesting = [s for s in setups if s.state in (State.BUY, State.SELL) and s.retest_confirmed]
    idle = [s for s in setups if s.state in (State.IDLE, State.FAILED)]
    
    report_lines = []
    report_lines.append("<b>🌅 PRE-MARKET BREAKOUT SCANNER</b>\n")
    
    report_lines.append("<b>📦 STOCKS FORMING A VCP BASE (CONSOLIDATION)</b>")
    if forming_box:
        box_rows = []
        for s in forming_box:
            w_str = f"W: {s.base_width_pct:.1f}%" if s.base_width_pct is not None else ""
            vdu_str = f"💧 VDU: {s.vdu_ratio:.1f}x" if s.vdu_confirmed else ""
            htf_str = "⭐ 1D" if s.htf_confirmed else ""
            res = f"{s.resistance:.5g}" if s.resistance is not None else "-"
            sup = f"{s.support:.5g}" if s.support is not None else "-"
            parts = [f"{s.symbol:8}", f"R: {res}", f"S: {sup}"]
            if w_str: parts.append(w_str)
            if vdu_str: parts.append(vdu_str)
            parts.append(f"Bias: {s.bias_score}/3")
            if htf_str: parts.append(htf_str)
            box_rows.append(" | ".join(parts))
        report_lines.append("<pre>" + "\n".join(box_rows) + "</pre>")
    else:
        report_lines.append("<i>None currently forming a box.</i>")
        
    report_lines.append("\n<b>🚀 ACTIVELY BREAKING OUT (MOMENTUM)</b>")
    if breaking_out:
        bo_rows = []
        for s in breaking_out:
            dir_str = "BUY" if s.direction == 1 else "SELL"
            cp = f"{s.current_price:.5g}" if s.current_price is not None else "-"
            entry = f"{s.entry:.5g}" if s.entry is not None else "-"
            stop = f"{s.stop:.5g}" if s.stop is not None else "-"
            vol_str = f" | Vol: {s.rvol:.1f}x" if s.rvol is not None else ""
            bo_rows.append(f"{s.symbol:8} | {dir_str} | CP: {cp}{vol_str} | In: {entry} | SL: {stop}")
        report_lines.append("<pre>" + "\n".join(bo_rows) + "</pre>")
    else:
        report_lines.append("<i>None currently breaking out.</i>")
        
    report_lines.append("\n<b>🔄 CONFIRMED PIVOT RETESTS (Watch closely!)</b>")
    if retesting:
        rt_rows = []
        for s in retesting:
            dir_str = "BUY" if s.direction == 1 else "SELL"
            cp = f"{s.current_price:.5g}" if s.current_price is not None else "-"
            entry = f"{s.entry:.5g}" if s.entry is not None else "-"
            stop = f"{s.stop:.5g}" if s.stop is not None else "-"
            vol_str = f" | Vol: {s.rvol:.1f}x" if s.rvol is not None else ""
            rt_rows.append(f"{s.symbol:8} | {dir_str} [RETEST] | CP: {cp}{vol_str} | In: {entry} | SL: {stop}")
        report_lines.append("<pre>" + "\n".join(rt_rows) + "</pre>")
    else:
        report_lines.append("<i>None currently in pivot retest.</i>")
        
    report_lines.append("\n<b>💤 IDLE / FAILED STOCKS (Ignore today)</b>")
    if idle:
        report_lines.append("<code>" + ", ".join([s.symbol for s in idle]) + "</code>")
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
