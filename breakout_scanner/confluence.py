"""
Phase 3: Confluence & Context Layer (breakout_scanner/confluence.py)

Pure quantitative context functions evaluating:
1. Multi-Timeframe 4H Structure & Trend Alignment
2. Broad Market Volatility Regime (VIX / Benchmark 50 SMA & ATR Expansion)
3. Earnings Proximity Calendar Analysis
"""

import datetime
from typing import Tuple, Optional, Dict, Any
import numpy as np
import pandas as pd

from .models import Setup, State


def is_non_equity_symbol(symbol: str) -> bool:
    """
    Returns True if the symbol represents a macro or derivative instrument:
    - Index (^NDX, ^DJI, ^FTSE, ^GDAXI, ^HSI, ^NSEI, etc.)
    - Commodity / Index Future (=F, e.g. GC=F, SI=F, CL=F, NQ=F)
    - Forex currency pair (=X, e.g. GBPUSD=X, USDJPY=X)
    - Cryptocurrency (-USD, -USDT, etc.)

    These instruments do not have corporate earnings calendars or Wall Street analyst coverage.
    """
    if not symbol:
        return False
    s = str(symbol).strip().upper()
    return (
        s.startswith("^")
        or "=F" in s
        or "=X" in s
        or s.endswith("-USD")
        or s.endswith("-USDT")
        or s.endswith("-EUR")
        or s.endswith("-BTC")
    )


def is_finite(val) -> bool:
    if val is None:
        return False
    try:
        return bool(np.isfinite(val))
    except Exception:
        return False


def compute_4h_confluence(
    df: pd.DataFrame,
    direction: int = 1,
    sma_len: int = 20,
    market: Optional[str] = None,
    drop_incomplete: bool = True
) -> Tuple[int, bool]:
    """
    Synthesizes 4H candles from intraday (1H) bars with session alignment (P1-2)
    and evaluates 4H trend structure using completed bars to eliminate repainting.
    Returns (trend_4h, is_confirmed).
    - trend_4h: +1 (bullish), -1 (bearish), 0 (neutral)
    - is_confirmed: True if 4H trend directly aligns with trade direction.
    """
    if df.empty or len(df) < (sma_len * 4):
        return 0, False

    try:
        # Determine session offset for alignment
        mkt = str(market).upper() if market else ""
        offset = None
        if mkt in ("US", "HKEX", "HK"):
            offset = "30min"  # Anchors 4H bins to 09:30 session open
        elif mkt == "NSE":
            offset = "15min"  # Anchors 4H bins to 09:15 session open

        # If DatetimeIndex is present, use session-aware temporal resampling
        if isinstance(df.index, pd.DatetimeIndex):
            resample_kwargs: Dict[str, Any] = {"rule": "4h"}
            if offset is not None:
                resample_kwargs["offset"] = offset

            agg_dict = {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }
            df_4h = df.resample(**resample_kwargs).agg(agg_dict).dropna()

            # P1-2: Completed Candle Filter
            # If the newest 4H candle has fewer than 2 bars and we have enough history,
            # drop the incomplete open bar to avoid premature lookahead/repainting
            if drop_incomplete and len(df_4h) > (sma_len + 1):
                counts = df["Close"].resample(**resample_kwargs).count().dropna()
                if len(counts) > 0 and counts.iloc[-1] < 2:
                    df_4h = df_4h.iloc[:-1]
        else:
            # Fallback: non-overlapping 4-bar rolling chunks
            chunks = len(df) // 4
            if chunks < sma_len:
                return 0, False
            reshaped = df.iloc[len(df) % 4:]
            grouped = reshaped.groupby(np.arange(len(reshaped)) // 4)
            df_4h = grouped.agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }).dropna()

        if len(df_4h) < sma_len:
            return 0, False

        sma = df_4h["Close"].rolling(sma_len).mean()
        curr_close = df_4h["Close"].iloc[-1]
        curr_sma = sma.iloc[-1]
        prev_sma = sma.iloc[-3] if len(sma) >= 3 else sma.iloc[-1]

        if not (is_finite(curr_close) and is_finite(curr_sma) and is_finite(prev_sma)):
            return 0, False

        slope_up = curr_sma > prev_sma
        slope_down = curr_sma < prev_sma

        if curr_close > curr_sma and slope_up:
            trend_4h = 1
        elif curr_close < curr_sma and slope_down:
            trend_4h = -1
        else:
            trend_4h = 0

        target_dir = 1 if direction >= 0 else -1
        confirmed = (target_dir == 1 and trend_4h == 1) or (target_dir == -1 and trend_4h == -1)
        return trend_4h, confirmed

    except Exception:
        return 0, False


def compute_market_regime(
    proxy_df: Optional[pd.DataFrame],
    sma_len: int = 50,
    atr_len: int = 14
) -> Tuple[str, bool]:
    """
    Evaluates market regime on the benchmark index / proxy ETF:
    - High-volatility selloff: Benchmark below 50 SMA and ATR expanding.
    - Bear trend: Benchmark below 50 SMA.
    - Bull expansion: Benchmark above upward-sloping 50 SMA.
    - Neutral: Benchmark sideways.

    Returns (regime_name, is_favorable).
    """
    if proxy_df is None or proxy_df.empty or len(proxy_df) < sma_len:
        return "NORMAL", True

    try:
        closes = proxy_df["Close"]
        sma = closes.rolling(sma_len).mean()
        curr_close = float(closes.iloc[-1])
        curr_sma = float(sma.iloc[-1])
        prev_sma = float(sma.iloc[-5]) if len(sma) >= 5 else curr_sma

        if not (is_finite(curr_close) and is_finite(curr_sma)):
            return "NORMAL", True

        # Calculate True Range and ATR
        high = proxy_df["High"]
        low = proxy_df["Low"]
        close_prev = proxy_df["Close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)

        atr = tr.rolling(atr_len).mean()
        curr_atr = float(atr.iloc[-1]) if not atr.empty else 0.0
        atr_baseline = float(atr.rolling(30, min_periods=5).mean().iloc[-1]) if len(atr) >= 10 else curr_atr

        vol_spike = (curr_atr > 1.4 * atr_baseline) if atr_baseline > 0 else False

        if curr_close < curr_sma and vol_spike:
            return "HIGH_VOL_SELLOFF", False
        elif curr_close < curr_sma:
            return "BEAR_TREND", False
        elif curr_close > curr_sma and curr_sma > prev_sma:
            return "BULL_EXPANSION", True
        else:
            return "NEUTRAL", True

    except Exception:
        return "NORMAL", True


_EARNINGS_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_earnings_cache():
    """Resets the in-memory earnings cache."""
    _EARNINGS_CACHE.clear()


def check_earnings_proximity(
    symbol: str,
    calendar_data: Optional[Any] = None,
    return_status: bool = False,
) -> Any:
    """
    Parses earnings calendar data for a symbol.
    Returns (days_until_earnings, is_earnings_imminent) or (days_until_earnings, is_earnings_imminent, data_status).
    is_earnings_imminent is True if earnings occur within 0 to 3 trading days.
    data_status: "OK", "FAILED", "UNKNOWN".
    
    Includes TTL (12h for OK, 15m for FAILED/UNKNOWN) and dynamic day recalculation
    so cached earnings dates adjust naturally as calendar days advance.
    """
    if not symbol or is_non_equity_symbol(symbol):
        res = (None, False, "OK")
        return res if return_status else (res[0], res[1])

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = now_utc.date()

    if calendar_data is None and symbol in _EARNINGS_CACHE:
        entry = _EARNINGS_CACHE[symbol]
        cached_status = entry.get("status", "UNKNOWN")
        cached_at = entry.get("cached_at")
        
        # TTL: 12 hours for OK, 15 minutes for FAILED/UNKNOWN
        ttl_seconds = 12 * 3600 if cached_status == "OK" else 15 * 60
        is_fresh = False
        if cached_at is not None:
            age_sec = (now_utc - cached_at).total_seconds()
            if age_sec < ttl_seconds:
                is_fresh = True
                
        if is_fresh:
            earning_date = entry.get("earning_date")
            if earning_date is not None and cached_status == "OK":
                days_until = (earning_date - today).days
                is_imminent = (0 <= days_until <= 3)
                res = (days_until, is_imminent, cached_status)
            else:
                res = (None, False, cached_status)
            return res if return_status else (res[0], res[1])

    try:
        cal = calendar_data
        if cal is None:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            cal = getattr(ticker, "calendar", None)

        if cal is None:
            _EARNINGS_CACHE[symbol] = {
                "earning_date": None,
                "status": "UNKNOWN",
                "cached_at": now_utc,
            }
            res = (None, False, "UNKNOWN")
            return res if return_status else (res[0], res[1])

        dates = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or cal.get("Earnings date")
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                dates = cal["Earnings Date"].tolist()
            elif "Earnings Date" in cal.index:
                dates = cal.loc["Earnings Date"].tolist()
        elif hasattr(cal, "get"):
            dates = cal.get("Earnings Date")

        if dates is not None:
            if isinstance(dates, (list, tuple, pd.Series, np.ndarray)) and len(dates) > 0:
                earning_date = dates[0]
            else:
                earning_date = dates

            if hasattr(earning_date, "date"):
                earning_date = earning_date.date()
            elif isinstance(earning_date, str):
                earning_date = datetime.datetime.strptime(earning_date[:10], "%Y-%m-%d").date()

            if isinstance(earning_date, datetime.date):
                days_until = (earning_date - today).days
                is_imminent = (0 <= days_until <= 3)
                _EARNINGS_CACHE[symbol] = {
                    "earning_date": earning_date,
                    "status": "OK",
                    "cached_at": now_utc,
                }
                res = (days_until, is_imminent, "OK")
                return res if return_status else (res[0], res[1])

        _EARNINGS_CACHE[symbol] = {
            "earning_date": None,
            "status": "UNKNOWN",
            "cached_at": now_utc,
        }
        res = (None, False, "UNKNOWN")
        return res if return_status else (res[0], res[1])

    except Exception:
        _EARNINGS_CACHE[symbol] = {
            "earning_date": None,
            "status": "FAILED",
            "cached_at": now_utc,
        }
        res = (None, False, "FAILED")
        return res if return_status else (res[0], res[1])


def enrich_confluence_and_context(
    setup: Setup,
    df: pd.DataFrame,
    proxy_df: Optional[pd.DataFrame] = None,
    cfg: Optional[Dict[str, Any]] = None
) -> Setup:
    """
    Enriches a Setup instance with Phase 3 Confluence & Context metrics:
    - 4H Structure & Trend Alignment
    - Broad Market Volatility Regime
    - Earnings Proximity Calendar Check
    """
    if df.empty or setup.state == State.IDLE:
        return setup

    target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.trend >= 0 else -1)

    # 1. 4H Structure Alignment (Session-aligned, P1-2)
    market = cfg.get("market") if cfg else None
    trend_4h, confirmed_4h = compute_4h_confluence(df, direction=target_dir, market=market)
    setup.htf_4h_trend = trend_4h
    setup.htf_4h_confirmed = confirmed_4h

    # 2. Market Regime Volatility Filter
    regime_name, favorable = compute_market_regime(proxy_df)
    setup.market_regime = regime_name
    setup.market_regime_favorable = favorable

    # 3. Earnings Proximity (Equities only - skip non-equity macro instruments)
    if setup.symbol and not is_non_equity_symbol(setup.symbol):
        days_until, imminent, earn_stat = check_earnings_proximity(setup.symbol, return_status=True)
        setup.days_to_earnings = days_until
        setup.earnings_data_status = earn_stat
        if imminent:
            setup.earnings_warning = True

    return setup
