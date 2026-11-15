import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("market_data_validation")

TIMEFRAME_DELTAS = {
    '1m': timedelta(minutes=1),
    '2m': timedelta(minutes=2),
    '5m': timedelta(minutes=5),
    '15m': timedelta(minutes=15),
    '30m': timedelta(minutes=30),
    '60m': timedelta(hours=1),
    '1h': timedelta(hours=1),
    '1d': timedelta(days=1),
    '1wk': timedelta(weeks=1),
}


def parse_timeframe_delta(timeframe: str) -> Optional[timedelta]:
    tf = timeframe.lower().strip()
    return TIMEFRAME_DELTAS.get(tf)


def validate_and_clean_candles(
    df: pd.DataFrame,
    symbol: str = "UNKNOWN",
    min_bars: int = 50,
    enforce_closed_candle: bool = True,
    timeframe: str = "1h"
) -> pd.DataFrame:
    """
    Validates, deduplicates, and sanitizes OHLCV candle datasets.
    
    Checks performed:
    1. Required columns presence (Open, High, Low, Close, Volume)
    2. Chronological ordering & timestamp deduplication
    3. Non-negative, non-zero price validation
    4. Geometric OHLC consistency (High >= Low, High >= Open/Close, Low <= Open/Close)
    5. Confirmed-candle policy (optional drop of incomplete, currently forming candle)
    6. Minimum bar requirement check for indicator warmup
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

    df = df.copy()

    # Handle multi-index columns from yfinance (e.g. ('Close', 'AAPL'))
    if hasattr(df.columns, 'levels'):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"[{symbol}] Missing required column '{col}'. Returning empty DataFrame.")
            return pd.DataFrame(columns=required_cols)

    df = df[required_cols]

    # Convert numeric columns safely
    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Drop NaNs and Infs
    df = df.dropna()
    df = df[~df.isin([np.nan, np.inf, -np.inf]).any(axis=1)]

    if df.empty:
        return df

    # 1. Chronological Sorting
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()

    # 2. Duplicate timestamp removal (keep the latest observation)
    if df.index.has_duplicates:
        duplicate_count = df.index.duplicated(keep='last').sum()
        logger.debug(f"[{symbol}] Dropping {duplicate_count} duplicate timestamp rows.")
        df = df[~df.index.duplicated(keep='last')]

    # 3. Geometric OHLC & Volume Sanity
    valid_mask = (
        (df['Open'] > 0) &
        (df['High'] > 0) &
        (df['Low'] > 0) &
        (df['Close'] > 0) &
        (df['Volume'] >= 0) &
        (df['High'] >= df['Low']) &
        (df['High'] >= df['Open'] * 0.9999) &
        (df['High'] >= df['Close'] * 0.9999) &
        (df['Low'] <= df['Open'] * 1.0001) &
        (df['Low'] <= df['Close'] * 1.0001)
    )

    invalid_count = (~valid_mask).sum()
    if invalid_count > 0:
        logger.warning(f"[{symbol}] Filtered out {invalid_count} geometrically invalid OHLC bars.")
        df = df[valid_mask]

    if df.empty:
        return df

    # 4. Confirmed-Candle Policy (Discard incomplete live bar if requested)
    if enforce_closed_candle and isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
        delta = parse_timeframe_delta(timeframe)
        if delta is not None:
            last_bar_time = df.index[-1]
            # Ensure timezone comparability
            if last_bar_time.tzinfo is not None:
                now = datetime.now(last_bar_time.tzinfo)
            else:
                now = datetime.now(timezone.utc).replace(tzinfo=None)

            candle_close_time = last_bar_time + delta
            # If current time is earlier than the close time of the last candle, the candle is still open
            if now < candle_close_time:
                logger.debug(
                    f"[{symbol}] Latest bar {last_bar_time} is still open (closes at {candle_close_time}). "
                    f"Truncating incomplete candle to ensure closed-bar execution."
                )
                df = df.iloc[:-1]

    # 5. Minimum History Warmup
    if len(df) < min_bars:
        logger.warning(
            f"[{symbol}] Insufficient data points: {len(df)} bars found, but minimum {min_bars} required."
        )

    return df
