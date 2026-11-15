import os
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
        self.consecutive_fetch_failures = 0
        self.circuit_breaker_threshold = 3
        self.daily_cache = {}
        self.daily_cache_ts = {}
        self.market_cache = {}
        self.market_cache_ts = {}

        # 1. Expand OS file descriptors limit (prevents EMFILE 'unable to open database file' crashes)
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(65536, hard)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except Exception:
            pass

        # 2. Direct yfinance cache to dedicated project directory
        try:
            cache_dir = os.path.abspath("data/cache/py-yfinance")
            os.makedirs(cache_dir, exist_ok=True)
            yf.set_tz_cache_location(cache_dir)
        except Exception:
            pass

    def reset_circuit_breaker(self):
        self.consecutive_fetch_failures = 0

    def candles(
        self,
        symbol: str,
        period: str = '60d',
        interval: str = '1h',
        enforce_closed_candle: bool = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles from Yahoo Finance with retry backoff, robust data validation and cleaning.
        Includes automatic SQLite database bypass to eliminate 'unable to open database file' errors.
        Each symbol operates with independent backoff retries so bad/delisted tickers never drop the scan universe.
        """
        enforce_closed = self.enforce_closed_candle if enforce_closed_candle is None else enforce_closed_candle
        backoff_delays = [0.5, 1.5, 3.0]
        df = None

        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                df = yf.download(
                    symbol,
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    progress=False
                )
                lat_ms = (time.time() - t0) * 1000.0
                if df is not None and not df.empty:
                    self.consecutive_fetch_failures = 0
                    try:
                        from breakout_scanner.run_logger import log_yf_call
                        log_yf_call(
                            symbol=symbol,
                            interval=interval,
                            period=period,
                            attempt=attempt + 1,
                            latency_ms=lat_ms,
                            outcome="OK",
                            rows_returned=len(df),
                            scan_id=getattr(self, "current_scan_id", None),
                        )
                    except Exception:
                        pass
                    break
                else:
                    logger.warning(f"Empty data received for {symbol} (attempt {attempt + 1}/{self.max_retries})")
                    try:
                        from breakout_scanner.run_logger import log_yf_call
                        log_yf_call(
                            symbol=symbol,
                            interval=interval,
                            period=period,
                            attempt=attempt + 1,
                            latency_ms=lat_ms,
                            outcome="EMPTY",
                            rows_returned=0,
                            scan_id=getattr(self, "current_scan_id", None),
                        )
                    except Exception:
                        pass
                    # If empty data received, bypass SQLite cache for next attempt to avoid lock/corruption
                    try:
                        import yfinance.cache as yc
                        tz_cache = yc.get_tz_cache()
                        if tz_cache is not None:
                            tz_cache.dummy = True
                    except Exception:
                        pass
            except Exception as e:
                lat_ms = (time.time() - t0) * 1000.0
                logger.warning(f"Attempt {attempt + 1}/{self.max_retries} failed to download candles for {symbol}: {e}")
                try:
                    from breakout_scanner.run_logger import log_yf_call
                    log_yf_call(
                        symbol=symbol,
                        interval=interval,
                        period=period,
                        attempt=attempt + 1,
                        latency_ms=lat_ms,
                        outcome="ERROR",
                        error_class=type(e).__name__,
                        error_message=str(e),
                        scan_id=getattr(self, "current_scan_id", None),
                    )
                except Exception:
                    pass
                try:
                    import yfinance.cache as yc
                    tz_cache = yc.get_tz_cache()
                    if tz_cache is not None:
                        tz_cache.dummy = True
                except Exception:
                    pass

            if attempt < self.max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                try:
                    from breakout_scanner.run_logger import log_yf_retry_sleep
                    log_yf_retry_sleep(
                        symbol=symbol,
                        sleep_seconds=delay,
                        attempt=attempt + 1,
                        scan_id=getattr(self, "current_scan_id", None),
                    )
                except Exception:
                    pass
                time.sleep(delay)

        if df is None or df.empty:
            logger.error(f"All {self.max_retries} attempts failed or returned empty data for {symbol}")
            return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

        self.consecutive_fetch_failures = 0

        # Validate, deduplicate, and clean candles
        clean_df = validate_and_clean_candles(
            df,
            symbol=symbol,
            min_bars=self.min_bars,
            enforce_closed_candle=enforce_closed,
            timeframe=interval
        )
        return clean_df

    def get_ticker_quote(
        self,
        symbol: str,
        fallback_price: float = None,
        atr: float = None
    ) -> dict:
        """
        Retrieves real-time quote metrics (bid, ask, last_price, volume) for execution validation (P1-1).
        When live books are missing or after hours, synthesizes effective spread from volatility microstructure.
        """
        bid = None
        ask = None
        last_price = fallback_price
        volume = None

        t0 = time.time()
        try:
            ticker = yf.Ticker(symbol)
            fi = getattr(ticker, "fast_info", None)
            if fi:
                last_price = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None) or last_price
                bid = getattr(fi, "bid", None)
                ask = getattr(fi, "ask", None)
                volume = getattr(fi, "last_volume", None) or getattr(fi, "regular_market_volume", None)
            try:
                from breakout_scanner.run_logger import log_yf_call
                log_yf_call(
                    symbol=symbol,
                    interval="quote",
                    period="",
                    attempt=1,
                    latency_ms=(time.time() - t0) * 1000.0,
                    outcome="OK",
                    rows_returned=1 if last_price else 0,
                    scan_id=getattr(self, "current_scan_id", None),
                )
            except Exception:
                pass
        except Exception as e:
            try:
                from breakout_scanner.run_logger import log_yf_call
                log_yf_call(
                    symbol=symbol,
                    interval="quote",
                    period="",
                    attempt=1,
                    latency_ms=(time.time() - t0) * 1000.0,
                    outcome="ERROR",
                    error_class=type(e).__name__,
                    error_message=str(e),
                    scan_id=getattr(self, "current_scan_id", None),
                )
            except Exception:
                pass

        if last_price is None and fallback_price is not None:
            last_price = fallback_price

        # Synthetic spread fallback if bid or ask is missing, inverted, or zero
        if (bid is None or ask is None or bid <= 0 or ask <= 0 or bid >= ask) and last_price and last_price > 0:
            if atr is not None and atr > 0:
                spread_pct = max(0.05, 0.05 * (atr / last_price) * 100.0)
            else:
                spread_pct = 0.08  # Baseline liquid spread

            half_spread = (spread_pct / 200.0) * last_price
            bid = round(last_price - half_spread, 4)
            ask = round(last_price + half_spread, 4)

        spread_pct = 0.0
        if bid and ask and ask > 0:
            spread_pct = round(((ask - bid) / ask) * 100.0, 3)

        return {
            "symbol": symbol,
            "last_price": last_price,
            "bid": bid,
            "ask": ask,
            "volume": volume if volume is not None else 100000.0,
            "spread_pct": spread_pct,
        }
