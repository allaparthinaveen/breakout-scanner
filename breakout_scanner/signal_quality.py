"""
Phase 2: Signal Quality Layer & Noise Reduction (breakout_scanner/signal_quality.py)

Pure quantitative functions evaluating:
1. Volume Dry-Up Curve (intra-base volume trajectory: last 1/3 vs first 1/3 of base)
2. Breakout Candle Close Location (wick rejection guard: upper 25-30% range bonus)
3. Distance-to-Trigger & Continuous Tightness Scoring
4. Base Piercing & Failed Breakout Memory (soft penalty for churned pivots)
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional, Dict, Any

from .models import Setup, State


def is_finite(val) -> bool:
    if val is None:
        return False
    try:
        return bool(np.isfinite(val))
    except Exception:
        return False


def compute_candle_close_location(
    df: pd.DataFrame,
    direction: int = 1,
    bar_index: int = -1
) -> Tuple[Optional[float], bool]:
    """
    Computes where the candle closed within its High-Low range:
    - Long:  (Close - Low) / (High - Low)
    - Short: (High - Close) / (High - Low)

    Returns (loc_ratio, is_upper_close_confirmed).
    An upper close (>= 0.70) confirms strong directional thrust with little upper wick rejection.
    """
    if df.empty or len(df) < abs(bar_index):
        return None, False

    row = df.iloc[bar_index]
    high = row.get("High")
    low = row.get("Low")
    close = row.get("Close")

    if not (is_finite(high) and is_finite(low) and is_finite(close)):
        return None, False

    bar_range = high - low
    if bar_range <= 1e-9:
        return 0.5, False

    if direction == -1:
        loc = (high - close) / bar_range
    else:
        loc = (close - low) / bar_range

    loc = float(np.clip(loc, 0.0, 1.0))
    # Threshold: Upper 30% of range (>= 0.70)
    confirmed = loc >= 0.70
    return round(loc, 2), confirmed


def compute_vdu_slope(
    df: pd.DataFrame,
    base_lookback: int = 20
) -> Tuple[Optional[float], bool]:
    """
    Measures volume contraction trajectory inside the base:
    Compares average volume in the final 1/3 of the base to the initial 1/3.
    Returns (vdu_slope_ratio, is_tightening_volume_confirmed).
    Confirmed if slope_ratio <= 0.70 (>= 30% volume dry-up into the apex).
    """
    if df.empty or len(df) < max(base_lookback, 6) or "Volume" not in df:
        return None, False

    base_vol = df["Volume"].iloc[-base_lookback:].dropna()
    if len(base_vol) < 6:
        return None, False

    third = max(2, len(base_vol) // 3)
    initial_vol = float(base_vol.iloc[:third].mean())
    final_vol = float(base_vol.iloc[-third:].mean())

    if not (is_finite(initial_vol) and is_finite(final_vol)) or initial_vol <= 0:
        return None, False

    slope_ratio = float(final_vol / initial_vol)
    if not is_finite(slope_ratio):
        return None, False

    confirmed = slope_ratio <= 0.70
    return round(slope_ratio, 2), confirmed


def compute_distance_to_trigger(
    setup: Setup,
    current_price: Optional[float] = None
) -> Tuple[Optional[float], bool]:
    """
    Computes distance from current close to breakout trigger level.
    If setup is in CONTRACTION and coiled within 0.75% of resistance with base width <= 4%,
    tightness is confirmed.
    """
    price = current_price if is_finite(current_price) else setup.current_price
    if not is_finite(price) or price <= 0:
        return None, False

    dist_pct = None
    target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.trend >= 0 else -1)

    if target_dir == 1 and is_finite(setup.resistance) and setup.resistance > 0:
        dist_pct = max(0.0, float((setup.resistance - price) / setup.resistance * 100.0))
    elif target_dir == -1 and is_finite(setup.support) and setup.support > 0:
        dist_pct = max(0.0, float((price - setup.support) / setup.support * 100.0))

    if dist_pct is None or not is_finite(dist_pct):
        return None, False

    # Confirmed if base is tight (<= 4%) and coiled right at the trigger level (<= 0.75%)
    is_tight_base = is_finite(setup.base_width_pct) and setup.base_width_pct <= 4.0
    confirmed = (dist_pct <= 0.75) and is_tight_base
    return round(dist_pct, 2), confirmed


def compute_failed_breakouts_in_base(
    df: pd.DataFrame,
    resistance: Optional[float],
    support: Optional[float],
    base_lookback: int = 20,
    direction: int = 1
) -> int:
    """
    Counts how many times candles in the base pierced beyond resistance/support
    and subsequently closed back inside the base (failed false-breakout attempts).
    """
    if df.empty or len(df) < 5:
        return 0

    base_slice = df.iloc[-base_lookback:]
    count = 0

    if direction == 1 and is_finite(resistance) and resistance > 0:
        # Pierced high but closed back below resistance
        mask = (base_slice["High"] > resistance * 1.001) & (base_slice["Close"] <= resistance)
        count = int(mask.sum())
    elif direction == -1 and is_finite(support) and support > 0:
        # Pierced low but closed back above support
        mask = (base_slice["Low"] < support * 0.999) & (base_slice["Close"] >= support)
        count = int(mask.sum())

    return count


def enrich_signal_quality(
    setup: Setup,
    df: pd.DataFrame,
    cfg: Optional[Dict[str, Any]] = None
) -> Setup:
    """
    Enriches a Setup instance with Phase 2 signal quality metrics.
    Pure calculation: does not alter core state or existing attributes.
    """
    if df.empty or setup.state == State.IDLE:
        return setup

    base_lookback = 20
    if cfg and "base" in cfg and "base_lookback" in cfg["base"]:
        base_lookback = cfg["base"]["base_lookback"]

    target_dir = setup.direction if setup.direction in (1, -1) else (1 if setup.trend >= 0 else -1)

    # 1. Breakout Candle Close Location
    bar_idx = -1
    if setup.state in (State.BUY, State.SELL) and setup.breakout_index is not None:
        # Target the breakout candle itself
        idx_diff = len(df) - 1 - setup.breakout_index
        if 0 <= idx_diff < len(df):
            bar_idx = -1 - idx_diff

    loc, loc_confirmed = compute_candle_close_location(df, direction=target_dir, bar_index=bar_idx)
    setup.candle_close_loc = loc
    setup.candle_close_confirmed = loc_confirmed

    # 2. Volume Dry-Up Slope (Last 1/3 vs First 1/3)
    if setup.state == State.CONTRACTION:
        vdu_slope, vdu_confirmed = compute_vdu_slope(df, base_lookback=base_lookback)
        setup.vdu_slope = vdu_slope
        setup.vdu_slope_confirmed = vdu_confirmed
    else:
        setup.vdu_slope = None
        setup.vdu_slope_confirmed = False

    # 3. Distance-to-Trigger & Continuous Tightness
    dist_pct, tightness_confirmed = compute_distance_to_trigger(setup, setup.current_price)
    setup.distance_to_trigger_pct = dist_pct
    setup.trigger_tightness_confirmed = tightness_confirmed

    # 4. Failed Breakouts inside base
    failed_count = compute_failed_breakouts_in_base(
        df,
        resistance=setup.resistance,
        support=setup.support,
        base_lookback=base_lookback,
        direction=target_dir
    )
    setup.failed_breakouts_in_base = failed_count

    return setup
