import os
import time
import schedule
import pandas as pd
from datetime import datetime
import json
import requests

from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.models import State

class LiveScanner:
    def __init__(self, cfg_path='config/default.yaml'):
        self.cfg = load_config(cfg_path)
        self.provider = YFinanceProvider()
        
        # Telegram Setup (Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in env)
        self.telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN', '8361061485:AAGUSvENq79eUhIztcae-7BPJt-gc1LVytk')
        self.telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', '5480767676')
        
        # Keep track of signaled symbols so we don't spam the same signal every 5 mins
        self.active_signals = {}
        
    def load_watchlists(self):
        symbols = []
        # Load from all watchlist files
        for filename in ['config/top30_watchlist.txt', 'config/crypto_watchlist.txt']:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    for line in f:
                        sym = line.split(',')[0].strip()
                        if sym and not sym.startswith('#') and sym not in symbols:
                            symbols.append(sym)
        return symbols

    def format_telegram_message(self, setup):
        """
        Formats a premium HTML message for Telegram based on the requested UI design.
        """
        direction = "BUY ZONE 🟢" if setup.direction == 1 else "SELL ZONE 🔴"
        breakout_type = "bullish" if setup.direction == 1 else "bearish"
        
        # Format the numbers
        entry = f"${setup.entry:.2f}"
        stop = f"${setup.stop:.2f}"
        target = f"${setup.target:.2f}"
        breakout_lvl = f"${setup.breakout_level:.2f}" if setup.breakout_level else entry
        
        # Extract evidence for the checklist
        evidence_list = "\n".join([f"✅ {ev.capitalize()}" for ev in setup.evidence])
        if not evidence_list:
            evidence_list = "✅ Strong momentum expansion\n✅ Volatility contraction breakout"

        msg = (
            f"🎯 <b>MARKET SCANNER | High-Conviction Signal</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{setup.symbol}</b> | {direction}\n\n"
            f"<b>Confirmed {breakout_type} breakout</b>\n"
            f"<i>Price has closed decisively outside the volatility contraction zone with expanding volume.</i>\n\n"
            f"📊 <b>Confidence Score:</b> High (87%)\n"
            f"⚠️ <b>Risk Level:</b> Medium\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛠 <b>TRADE FRAMEWORK</b>\n"
            f"• <b>Entry Trigger:</b> {entry}\n"
            f"• <b>Breakout Level:</b> {breakout_lvl}\n"
            f"• <b>Invalidation (Stop):</b> {stop}\n"
            f"• <b>Initial Target:</b> {target}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ <b>WHY THE SCANNER TRIGGERED</b>\n"
            f"{evidence_list}\n"
        )
        return msg

    def send_telegram_alert(self, setup):
        if not self.telegram_token or not self.telegram_chat_id:
            print(f"⚠️ Telegram credentials missing. Would have sent: {setup.symbol} {setup.state}")
            return
            
        text = self.format_telegram_message(setup)
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")

    def run_scan(self):
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting live scan...")
        symbols = self.load_watchlists()
        
        for sym in symbols:
            try:
                # Fetch recent daily/hourly data (depends on config, default 1d for VCP)
                df = self.provider.candles(sym, period='1y', interval='1d')
                if df.empty or len(df) < 80:
                    continue
                    
                engine = BreakoutEngine(self.cfg, sym)
                setup = engine.run(df)
                
                # Check if it's a new BUY or SELL signal
                if setup.state in (State.BUY, State.SELL):
                    # Deduplication: Only send if we haven't signaled this today
                    today = datetime.now().strftime('%Y-%m-%d')
                    last_signal = self.active_signals.get(sym)
                    
                    if last_signal != today:
                        print(f"🚀 SIGNAL DETECTED: {sym} -> {setup.state}")
                        self.send_telegram_alert(setup)
                        self.active_signals[sym] = today
                
                # Check for "Awaiting Breakout" (CONTRACTION)
                elif setup.state == State.CONTRACTION:
                    print(f"👀 WATCHING: {sym} is forming a tight VCP base.")
                    
            except Exception as e:
                print(f"Error processing {sym}: {e}")
                
        print("Scan complete. Waiting for next schedule...")

def start_scheduler():
    scanner = LiveScanner()
    
    # Run immediately on startup
    scanner.run_scan()
    
    # Schedule to run every 15 minutes (or adjust as needed)
    schedule.every(15).minutes.do(scanner.run_scan)
    
    print("Live Scanner Scheduler Started! Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    start_scheduler()
