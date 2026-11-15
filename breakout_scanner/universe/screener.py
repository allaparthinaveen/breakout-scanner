"""
Phase 6: Liquidity & Microstructure Screener
============================================
Performs rigorous data quality audits, 20-day ADV computation, liquidity trend analysis,
spread/impact modeling, and overnight gap-risk detection.
"""

from typing import Dict, Any, Optional, List, Tuple
import pandas as pd
import numpy as np

from .models import LiquidityMetrics
from ..sectors import get_sector_benchmark


class LiquidityScreener:
    """
    Screens and calculates liquidity and execution metrics for a single stock
    over trailing completed daily sessions.
    """
    def __init__(
        self,
        lookback_sessions: int = 20,
        min_active_sessions: int = 18,
        min_price_us: float = 5.0,
        min_price_nse: float = 50.0,
        max_spread_pct: float = 6.0,
        max_avg_gap_pct: float = 2.5,
    ):
        self.lookback = lookback_sessions
        self.min_active_sessions = min_active_sessions
        self.min_price_us = min_price_us
        self.min_price_nse = min_price_nse
        self.max_spread_pct = max_spread_pct
        self.max_avg_gap_pct = max_avg_gap_pct

    def detect_market(self, symbol: str) -> str:
        s = symbol.upper()
        if s.endswith(".NS") or s.endswith(".BO"):
            return "NSE"
        elif s.endswith("-USD") or s.endswith("-USDT"):
            return "CRYPTO"
        return "US"

    def get_min_price(self, symbol: str) -> float:
        market = self.detect_market(symbol)
        if market == "NSE":
            return self.min_price_nse
        return self.min_price_us

    def screen_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        historical_ranks: Optional[List[int]] = None
    ) -> LiquidityMetrics:
        """
        Evaluates daily OHLCV dataframe and returns rich LiquidityMetrics.
        """
        sec_name = "Broad Market"
        try:
            sec_name, _ = get_sector_benchmark(symbol)
        except Exception:
            pass

        metrics = LiquidityMetrics(
            symbol=symbol,
            sector=sec_name or "Broad Market",
            rank_history=historical_ranks or []
        )

        # -------------------------------------------------------------
        # 1. Data Quality Checks (Missing DataFrame, Insufficient Bars)
        # -------------------------------------------------------------
        if df is None or df.empty or len(df) < self.min_active_sessions:
            metrics.valid_data = False
            metrics.status = "REJECTED"
            metrics.rejection_reason = f"INSUFFICIENT_BARS: Got {0 if df is None else len(df)} bars, require >= {self.min_active_sessions}"
            return metrics

        # Required columns
        cols = [c.capitalize() for c in df.columns]
        df_clean = df.copy()
        df_clean.columns = cols

        for req in ("Open", "High", "Low", "Close", "Volume"):
            if req not in df_clean.columns:
                metrics.valid_data = False
                metrics.status = "REJECTED"
                metrics.rejection_reason = f"MISSING_COLUMN: Column {req} missing"
                return metrics

        # Slice trailing lookback sessions
        tail = df_clean.iloc[-self.lookback:]
        actual_sessions = len(tail)
        metrics.active_sessions = actual_sessions

        # Check for NaNs or negative prices
        for col in ("Open", "High", "Low", "Close", "Volume"):
            s_col = pd.to_numeric(tail[col], errors="coerce")
            if s_col.isna().any():
                metrics.valid_data = False
                metrics.status = "REJECTED"
                metrics.rejection_reason = f"NAN_DATA: Column {col} contains NaN values"
                return metrics
            if (s_col < 0).any():
                metrics.valid_data = False
                metrics.status = "REJECTED"
                metrics.rejection_reason = f"NEGATIVE_DATA: Column {col} contains negative values"
                return metrics

        closes = tail["Close"].values
        volumes = tail["Volume"].values
        opens = tail["Open"].values
        highs = tail["High"].values
        lows = tail["Low"].values

        # Zero-volume halts check (e.g. suspended / halted trading)
        zero_vols = np.sum(volumes == 0)
        if zero_vols > 2:
            metrics.valid_data = False
            metrics.status = "REJECTED"
            metrics.rejection_reason = f"TRADING_HALT: {zero_vols} zero-volume sessions in trailing {actual_sessions} bars"
            return metrics

        last_close = float(closes[-1])
        metrics.last_close = round(last_close, 4)

        # -------------------------------------------------------------
        # 2. Price Floor Check (Penny / Low-Priced Exclusion)
        # -------------------------------------------------------------
        min_p = self.get_min_price(symbol)
        if last_close < min_p:
            metrics.valid_data = False
            metrics.status = "REJECTED"
            metrics.rejection_reason = f"PENNY_STOCK: Last close ${last_close:.2f} is below minimum floor ${min_p:.2f}"
            return metrics

        # -------------------------------------------------------------
        # 3. ADV Calculation (Average Daily Traded Value = Close * Volume)
        # -------------------------------------------------------------
        traded_values = closes * volumes
        adv_20 = float(np.mean(traded_values))
        vol_20 = float(np.mean(volumes))

        metrics.adv_20d = round(adv_20, 2)
        metrics.volume_20d_avg = round(vol_20, 1)

        # 5-day short-term ADV for liquidity trend calculation
        tail_5_closes = closes[-5:]
        tail_5_vols = volumes[-5:]
        adv_5 = float(np.mean(tail_5_closes * tail_5_vols)) if len(tail_5_closes) >= 5 else adv_20
        metrics.adv_5d = round(adv_5, 2)

        if adv_20 > 0:
            trend_ratio = adv_5 / adv_20
            metrics.liquidity_trend = round(trend_ratio, 3)
            if trend_ratio >= 1.15:
                metrics.liquidity_trend_label = "EXPANDING"
            elif trend_ratio <= 0.85:
                metrics.liquidity_trend_label = "CONTRACTING"
            else:
                metrics.liquidity_trend_label = "STABLE"

        # -------------------------------------------------------------
        # 4. Microstructure Spread & Market Impact Estimation
        # -------------------------------------------------------------
        bar_ranges = (highs - lows) / np.maximum(1e-4, closes) * 100.0
        avg_spread = float(np.mean(bar_ranges))
        metrics.spread_pct = round(avg_spread, 3)

        # Market impact estimate: basis points of friction expected on typical institutional order
        # Impact bps = 10000 * (0.05 * (ATR / Price))
        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
        tr[0] = highs[0] - lows[0]
        atr14 = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))
        impact_bps = 10000.0 * (0.05 * (atr14 / max(1e-4, last_close)))
        metrics.market_impact_est_bps = round(float(impact_bps), 1)

        # Spread penalty
        if avg_spread > self.max_spread_pct:
            metrics.valid_data = False
            metrics.status = "REJECTED"
            metrics.rejection_reason = f"EXCESSIVE_SPREAD: Average spread {avg_spread:.2f}% exceeds maximum {self.max_spread_pct:.2f}%"
            return metrics

        # -------------------------------------------------------------
        # 5. Overnight Gap-Risk Detection
        # -------------------------------------------------------------
        if len(opens) >= 2:
            prev_closes = closes[:-1]
            curr_opens = opens[1:]
            overnight_gaps = np.abs(curr_opens - prev_closes) / np.maximum(1e-4, prev_closes) * 100.0
            avg_gap = float(np.mean(overnight_gaps))
            max_gap = float(np.max(overnight_gaps))
            metrics.avg_overnight_gap_pct = round(avg_gap, 2)

            # Flag if average gap exceeds threshold or single extreme jump occurred
            if avg_gap > self.max_avg_gap_pct or max_gap > 8.0:
                metrics.gap_risk_flag = True

        metrics.valid_data = True
        metrics.status = "QUALIFIED"
        return metrics

