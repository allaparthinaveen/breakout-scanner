import logging
import time
import pandas as pd
import yfinance as yf
from .base import MarketDataProvider
from .validation import validate_and_clean_candles

logger = logging.getLogger("yfinance_provider")


class YFinanceProvider(MarketDataProvider):
    def __init__(self, enforce_closed_candle: bool = True, min_bars: int = 50, max_retries: int = 3):
        self.enforce_closed_candle = enforce_closed_candle
        self.min_bars = min_bars
        self.max_retries = max_retries
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
        Fetch OHLCV candles from Yahoo Finance with retry backoff, robust data validation and cleaning.
        """
        enforce_closed = self.enforce_closed_candle if enforce_closed_candle is None else enforce_closed_candle
        backoff_delays = [0.5, 1.5, 3.0]
        df = None

        for attempt in range(self.max_retries):
            try:
                df = yf.download(
                    symbol,
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    progress=False
                )
                if df is not None and not df.empty:
                    break
                else:
                    logger.warning(f"Empty data received for {symbol} (attempt {attempt + 1}/{self.max_retries})")
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}/{self.max_retries} failed to download candles for {symbol}: {e}")

            if attempt < self.max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                time.sleep(delay)

        if df is None or df.empty:
            logger.error(f"All {self.max_retries} attempts failed or returned empty data for {symbol}")
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
