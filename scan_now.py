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

def build_report_string(setups, slate=None, market=None, timeframe="1h"):
    return format_scan_report_html(setups, slate=slate, market=market, timeframe=timeframe)

def scan_now():
    parser = argparse.ArgumentParser(description="Run breakout scanner on a watchlist")
    parser.add_argument(
        "--watchlist",
        type=str,
        default=None,
        help="Path to watchlist file (defaults to config/watchlist.txt)",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="us",
        choices=["us", "nse", "US", "NSE"],
        help="Target exchange market for dynamic universe (default: us)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to strategy config yaml (e.g. config/intraday_1h.yaml, config/options_trading.yaml)",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=None,
        help="Candle timeframe interval: 1h, 1d, 15m (overrides config setting)",
    )
    parser.add_argument(
        "--period",
        type=str,
        default=None,
        help="Lookback period: 60d, 730d, 2y (overrides config setting)",
    )
    parser.add_argument(
        "--no-tg",
        action="store_true",
        help="Suppress Telegram notification (CLI terminal report only)",
    )
    parser.add_argument(
        "--slate",
        action="store_true",
        help="Display Phase 4 Institutional Actionable Trade Slate with exact sizing & risk budgeting",
    )
    parser.add_argument(
        "--dynamic-universe",
        action="store_true",
        help="Screen broad market dynamically for Top 30 high-liquidity stocks using ADV_20 & stability buffer",
    )
    args = parser.parse_args()

    print(f"Loading config from {args.config}...")
    cfg = load_config(args.config)
    provider = YFinanceProvider()
    
    # Resolve interval and period from CLI args, config, or sensible defaults
    interval = args.interval or cfg.get('timeframe', {}).get('interval', '1h')
    period = args.period or cfg.get('timeframe', {}).get('period', '60d')
    
    if args.dynamic_universe:
        from breakout_scanner.universe.engine import DynamicLiquidityEngine
        u_engine = DynamicLiquidityEngine(cfg)
        market = args.market.lower()
        custom_wl = args.watchlist if (args.watchlist and os.path.exists(args.watchlist)) else None
        if custom_wl and ('nifty' in custom_wl.lower() or 'nse' in custom_wl.lower()):
            market = 'nse'
        u_result = u_engine.run_selection(provider, market=market, custom_watchlist=custom_wl)
        symbols = u_result.selected_symbols
        filename = u_result.watchlist_path or f"data/universe/{market}_top30_active.txt"
        print(f"  • Dynamic Universe: Screened {u_result.total_eligible} -> Selected Top {len(symbols)} (Turnover: {u_result.turnover_rate_pct:.1f}%)")
    else:
        filename = args.watchlist or "config/watchlist.txt"
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

    print("=" * 65)
    print(f"  BREAKOUT SCANNER: {os.path.basename(args.config).upper()}")
    print("=" * 65)
    print(f"  • Strategy Config: {args.config}")
    print(f"  • Watchlist:       {filename} ({len(symbols)} symbols)")
    print(f"  • Timeframe:       {interval.upper()} (Period: {period})")
    print(f"  • Long-Only:       {'Yes' if not cfg.get('breakout', {}).get('allow_shorts', True) else 'No (Bi-directional)'}")
    print(f"  • Cost Filter:     {'Enabled (Max ' + str(cfg.get('risk', {}).get('max_friction_r', 0.15)) + 'R)' if cfg.get('risk', {}).get('use_cost_filter') else 'Disabled'}")
    if 'option' in args.config.lower():
        print("  ⚠️  OPTIONS SCREENING NOTICE:")
        print("  • Signals represent UNDERLYING stock momentum candidates only.")
        print("  • Manual option chain required: Delta 0.40–0.60 | 14–30 DTE | Spread < 10% | IV < 50%.")
        print("  • NEVER use underlying stop distance directly as option contract stop.")
    print("=" * 65)
    
    from breakout_scanner.scanner import MultiSymbolScanner
    scanner = MultiSymbolScanner(provider, cfg)
    
    # Run scanner and grab results
    setups = scanner.scan(symbols, period=period, interval=interval)
    
    # Print the CLI colored report
    scanner.report(setups)

    # Authoritative Institutional Actionable Trade Slate Pipeline
    slate = None
    try:
        from breakout_scanner.slate import SlatePipeline
        from breakout_scanner.portfolio_guard import format_trade_slate_cli
        pipeline = SlatePipeline(cfg=cfg, provider=provider)
        slate = pipeline.evaluate_setups(setups)
        if args.slate or cfg.get('portfolio_guard', {}).get('enabled', False):
            print(format_trade_slate_cli(slate))
    except Exception as e:
        import logging
        logging.getLogger("scan_now").error(f"❌ Failed to run SlatePipeline: {e}", exc_info=True)

    # Log scan metrics telemetry (Phase 0 Baseline & Safety Net)
    market_label = 'crypto' if 'crypto' in filename.lower() else ('nse' if 'nifty' in filename.lower() or 'nse' in filename.lower() else 'us')
    try:
        from breakout_scanner.telemetry import log_scan_metrics
        log_scan_metrics(setups, market=market_label, timeframe=interval, config_name=os.path.basename(args.config))
    except Exception:
        pass

    # Build HTML string and send via Telegram if enabled
    if not args.no_tg:
        report_html = build_report_string(setups, slate=slate, market=market_label, timeframe=interval)
        send_telegram_message(report_html)
    else:
        print("\n🔕 Telegram notification skipped (--no-tg specified).")
            
    print("Scan complete.")

if __name__ == "__main__":
    scan_now()
