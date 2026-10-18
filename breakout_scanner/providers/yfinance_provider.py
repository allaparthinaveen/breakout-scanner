import logging
import pandas as pd
import yfinance as yf
from .base import MarketDataProvider
from .validation import validate_and_clean_candles

logger = logging.getLogger("yfinance_provider")


class YFinanceProvider(MarketDataProvider):
    def __init__(self, enforce_closed_candle: bool = True, min_bars: int = 50):
        self.enforce_closed_candle = enforce_closed_candle
        self.min_bars = min_bars
        self.daily_cache = {}
        self.daily_cache_ts = {}
        self.market_cache = {}
        self.market_cache_ts = {}

    def candles(
        self,
        symbol: str,
        period: str = '60d',
        interval: str = '1h',
        enforce_closed_candle: bool = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles from Yahoo Finance with robust data validation and cleaning.
        """
        enforce_closed = self.enforce_closed_candle if enforce_closed_candle is None else enforce_closed_candle
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False
            )
        except Exception as e:
            logger.error(f"Failed to download candles for {symbol}: {e}")
            return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

        if df is None or df.empty:
            return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

        # Validate, deduplicate, and clean candles
        clean_df = validate_and_clean_candles(
            df,
            symbol=symbol,
            min_bars=self.min_bars,
            enforce_closed_candle=enforce_closed,
            timeframe=interval
        )
        return clean_df
