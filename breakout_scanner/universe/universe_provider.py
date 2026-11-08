"""
Phase 6: Broad Eligible Universe Provider
=========================================
Loads candidate universe symbols across US Equities, NSE Equities, or custom watchlists,
and retrieves completed daily historical sessions for liquidity evaluation.
"""

import os
from typing import List, Dict, Optional
import pandas as pd

from ..providers.base import MarketDataProvider


class EligibleUniverseProvider:
    """
    Supplies broad stock universes for screening.
    """
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir

    def load_symbols_from_file(self, filepath: str) -> List[str]:
        """Parses a watchlist file, stripping comments and duplicates."""
        symbols = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    clean = line.split(",")[0].split("#")[0].strip()
                    if clean and not clean.startswith("#") and not clean.startswith("##"):
                        if clean not in symbols:
                            symbols.append(clean)
        return symbols

    def get_broad_universe(self, market: str = "US", custom_watchlist: Optional[str] = None) -> List[str]:
        """
        Retrieves the broad eligible universe for liquidity screening.
        """
        if custom_watchlist and os.path.exists(custom_watchlist):
            return self.load_symbols_from_file(custom_watchlist)

        m = market.upper()
        symbols = []

        if m == "US":
            # Combine S&P and Nasdaq broad constituent lists
            sp_path = os.path.join(self.config_dir, "sp_50_watchlist.txt")
            nasdaq_path = os.path.join(self.config_dir, "nasdaq_50_watchlist.txt")
            top_path = os.path.join(self.config_dir, "top30_watchlist.txt")

            symbols.extend(self.load_symbols_from_file(sp_path))
            symbols.extend(self.load_symbols_from_file(nasdaq_path))
            symbols.extend(self.load_symbols_from_file(top_path))

            # Core US blue chips fallback
            core_us = [
                "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "CRM",
                "CSCO", "NFLX", "AMD", "QCOM", "INTC", "JPM", "BAC", "WFC", "GS", "MS", "BLK",
                "V", "MA", "AXP", "DIS", "CAT", "UNH", "JNJ", "PFE", "ABBV", "LLY", "XOM", "CVX",
                "HD", "MCD", "NKE", "SBUX", "WMT", "TGT", "ORCL", "IBM", "TXN", "MU", "AMAT", "LRCX"
            ]
            for s in core_us:
                if s not in symbols:
                    symbols.append(s)

        elif m == "NSE":
            nifty_path = os.path.join(self.config_dir, "nifty_50_watchlist.txt")
            nse_top_path = os.path.join(self.config_dir, "nsetop10_watchlist.txt")

            symbols.extend(self.load_symbols_from_file(nifty_path))
            symbols.extend(self.load_symbols_from_file(nse_top_path))

            core_nse = [
                "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
                "SBIN.NS", "LICI.NS", "ITC.NS", "HINDUNILVR.NS", "LT.NS", "BAJFINANCE.NS", "HCLTECH.NS",
                "MARUTI.NS", "SUNPHARMA.NS", "AXISBANK.NS", "TITAN.NS", "ULTRACEMCO.NS", "KOTAKBANK.NS",
                "ASIANPAINT.NS", "WIPRO.NS", "M&M.NS", "TATAMOTORS.NS", "NTPC.NS", "POWERGRID.NS",
                "ADANIENT.NS", "COALINDIA.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "BAJAJ-AUTO.NS"
            ]
            for s in core_nse:
                if s not in symbols:
                    symbols.append(s)

        # De-duplicate while preserving insertion order
        seen = set()
        deduped = []
        for s in symbols:
            if s and s not in seen:
                seen.add(s)
                deduped.append(s)

        return deduped

    def fetch_daily_data(
        self,
        symbols: List[str],
        provider: MarketDataProvider,
        lookback_days: str = "60d"
    ) -> Dict[str, pd.DataFrame]:
        """
        Retrieves completed daily OHLCV candles for the broad candidate list.
        """
        data: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = provider.candles(sym, period=lookback_days, interval="1d")
                if df is not None and not df.empty:
                    data[sym] = df
            except Exception:
                pass
        return data

