"""
Composite Quant Scorer (Phase 3 Step 4 & Audit Fix 3)
Aggregates all 5 quantitative pillars into an objective 0–100 score and tier grade:
1. Contraction Physics (Coil Decay & Base Width): 20 pts
2. Volume Footprint (VDU & RVOL): 20 pts
3. Multi-Timeframe Trend Template (1D 50 SMA + 1H Trend): 20 pts
4. Relative Strength Alpha & Momentum Slope: 20 pts
5. Risk-Reward Asymmetry & Execution Structure: 20 pts

Grade Tiers:
- A+ [90 - 100]: Institutional Grade (Ultra-tight coil, VDU, 1D aligned, accelerating RS)
- A  [80 - 89]:  High Conviction Setup
- B  [70 - 79]:  Playable Standard Setup
- C  [< 70]:     Filtered / Lower Conviction Setup

Audit Fix 3 (P1) Integrity Rule:
- No artificial fallback points for missing data. Missing evidence yields 0 points.
- Flags unconfirmed pillars on setup.evidence_status ('Unconfirmed HTF', 'Unconfirmed Sector', 'Unconfirmed Volume').
"""

import numpy as np
from typing import Tuple, List, Optional, Dict, Any
from .models import Setup, State

def is_finite_num(val) -> bool:
    if val is None:
        return False
    try:
        return bool(np.isfinite(val))
    except Exception:
        return False

def calculate_composite_score(
    setup: Setup,
    quality_cfg: Optional[Dict[str, Any]] = None,
    context_cfg: Optional[Dict[str, Any]] = None
) -> Tuple[int, str]:
    """
    Computes a composite score (0-100) and grade ('A+', 'A', 'B', 'C')
    for a given Setup without phantom fallback points or NaN poisoning.
    Supports optional Phase 2 (Signal Quality) and Phase 3 (Confluence/Context)
    additive modifiers behind feature-flagged config weights (default weight = 0.0).
    """
    unconfirmed: List[str] = []

    # ----------------------------------------------------
    # Pillar 1: Contraction Physics (Max 20 pts)
    # ----------------------------------------------------
    p1 = 0
    # Base Width tightness (Max 10 pts)
    if is_finite_num(setup.base_width_pct):
        if setup.base_width_pct <= 4.0:
            p1 += 10
        elif setup.base_width_pct <= 6.5:
            p1 += 8
        elif setup.base_width_pct <= 9.0:
            p1 += 5
        elif setup.base_width_pct <= 12.0:
            p1 += 3
        else:
            p1 += 1
    # Missing/NaN base_width_pct yields 0 points

    # Coil decay & tightness confirmation (Max 10 pts)
    if is_finite_num(setup.decay_ratio):
        if setup.tightening_confirmed and setup.decay_ratio <= 0.40:
            p1 += 10
        elif setup.tightening_confirmed or setup.decay_ratio <= 0.70:
            p1 += 8
        elif setup.decay_ratio <= 0.85:
            p1 += 5
        else:
            p1 += 2
    # Missing/NaN decay_ratio yields 0 points

    # ----------------------------------------------------
    # Pillar 2: Volume Footprint (Max 20 pts)
    # ----------------------------------------------------
    p2 = 0
    if setup.state == State.CONTRACTION:
        if is_finite_num(setup.vdu_ratio):
            if setup.vdu_confirmed and setup.vdu_ratio <= 0.60:
                p2 = 20
            elif setup.vdu_confirmed or setup.vdu_ratio <= 0.75:
                p2 = 16
            elif setup.vdu_ratio <= 0.90:
                p2 = 11
            else:
                p2 = 5
        else:
            p2 = 0
            unconfirmed.append("Unconfirmed Volume")
    else:  # Active Breakout / Retest
        if is_finite_num(setup.rvol):
            if setup.rvol >= 2.0:
                p2 = 20
            elif setup.rvol >= 1.5:
                p2 = 17
            elif setup.rvol >= 1.2:
                p2 = 14
            elif setup.rvol >= 1.0:
                p2 = 10
            else:
                p2 = 5
        else:
            p2 = 0
            unconfirmed.append("Unconfirmed Volume")

    # ----------------------------------------------------
    # Pillar 3: Multi-Timeframe Trend Template (Max 20 pts)
    # ----------------------------------------------------
    p3 = 0
    target_dir = setup.direction if setup.direction in (1, -1) else (1 if (is_finite_num(setup.trend) and setup.trend >= 0) else -1)
    
    # Daily (1D) Macro alignment (Max 12 pts)
    if setup.htf_confirmed:
        p3 += 12
    elif is_finite_num(setup.htf_trend) and setup.htf_trend != 0:
        if (target_dir == 1 and setup.htf_trend == 1) or (target_dir == -1 and setup.htf_trend == -1):
            p3 += 8
        else:
            p3 += 0  # Opposing trend yields 0 pts
    else:
        p3 += 0  # Missing HTF evidence yields 0 pts
        unconfirmed.append("Unconfirmed HTF")

    # Hourly (1H) Trend alignment (Max 8 pts)
    if is_finite_num(setup.trend) and setup.trend != 0:
        if (target_dir == 1 and setup.trend == 1) or (target_dir == -1 and setup.trend == -1):
            p3 += 8
        else:
            p3 += 0  # Opposing 1H trend yields 0 pts
    else:
        p3 += 0

    # ----------------------------------------------------
    # Pillar 4: Relative Strength Alpha & Momentum Slope (Max 20 pts)
    # ----------------------------------------------------
    p4 = 0
    # Sector RS Delta (Max 10 pts)
    if is_finite_num(setup.sector_rs_pct):
        if target_dir == 1:  # Long
            if setup.sector_rs_pct >= 3.0:
                p4 += 10
            elif setup.sector_rs_pct > 0.0:
                p4 += 8
            elif setup.sector_rs_pct >= -2.0:
                p4 += 5
            else:
                p4 += 2
        else:  # Short: Underperformance indicates selling pressure
            if setup.sector_rs_pct <= -3.0:
                p4 += 10
            elif setup.sector_rs_pct < 0.0:
                p4 += 8
            elif setup.sector_rs_pct <= 2.0:
                p4 += 5
            else:
                p4 += 2
    else:
        p4 += 0  # Missing sector evidence yields 0 pts
        unconfirmed.append("Unconfirmed Sector")

    # RS Linear Regression Slope (Max 10 pts)
    if target_dir == 1:
        if setup.rs_trend == "↗":
            p4 += 10
        elif setup.rs_trend == "→":
            p4 += 6
        elif setup.rs_trend == "↘":
            p4 += 1
        else:
            p4 += 0  # Missing / empty slope yields 0 pts
    else:  # Short setup
        if setup.rs_trend == "↘":
            p4 += 10
        elif setup.rs_trend == "→":
            p4 += 6
        elif setup.rs_trend == "↗":
            p4 += 1
        else:
            p4 += 0  # Missing / empty slope yields 0 pts

    # ----------------------------------------------------
    # Pillar 5: Risk-Reward Asymmetry & Execution Structure (Max 20 pts)
    # ----------------------------------------------------
    p5 = 0
    # Risk Tightness (Max 12 pts)
    if is_finite_num(setup.risk_pct):
        if setup.risk_pct <= 1.2:
            p5 += 12
        elif setup.risk_pct <= 2.0:
            p5 += 9
        elif setup.risk_pct <= 3.0:
            p5 += 6
        elif setup.risk_pct <= 5.0:
            p5 += 2
        else:
            p5 += 0
    elif is_finite_num(setup.base_width_pct):
        if setup.base_width_pct <= 4.5:
            p5 += 12
        elif setup.base_width_pct <= 7.0:
            p5 += 9
        elif setup.base_width_pct <= 10.0:
            p5 += 6
        elif setup.base_width_pct <= 12.0:
            p5 += 3
        else:
            p5 += 0
    else:
        p5 += 0  # Missing risk and base width yields 0 pts

    # Pattern / Invalidation Quality (Max 8 pts)
    if setup.state in (State.BUY, State.SELL):
        if setup.retest_confirmed:
            p5 += 8
        elif is_finite_num(setup.breakout_level) or is_finite_num(setup.entry):
            p5 += 6
        else:
            p5 += 0
    else:
        if is_finite_num(setup.resistance) and is_finite_num(setup.support):
            p5 += 8
        elif is_finite_num(setup.resistance) or is_finite_num(setup.support):
            p5 += 4
        else:
            p5 += 0

    raw_sum = p1 + p2 + p3 + p4 + p5

    # ----------------------------------------------------
    # Phase 2: Additive Signal Quality Modifiers (Behind Config Weights)
    # ----------------------------------------------------
    quality_adj = 0.0
    if quality_cfg and quality_cfg.get("enabled", False):
        w_close = float(quality_cfg.get("close_location_weight", 0.0))
        w_vdu = float(quality_cfg.get("vdu_slope_weight", 0.0))
        w_tight = float(quality_cfg.get("trigger_tightness_weight", 0.0))
        w_failed = float(quality_cfg.get("failed_breakout_penalty_weight", 0.0))

        # 1. Breakout Candle Close Location (Wick Guard)
        if setup.state in (State.BUY, State.SELL):
            if getattr(setup, "candle_close_confirmed", False):
                quality_adj += w_close * 5.0
            elif getattr(setup, "candle_close_loc", None) is not None and setup.candle_close_loc < 0.40:
                quality_adj -= w_close * 5.0

        # 2. Intra-Base Volume Dry-Up Slope
        if setup.state == State.CONTRACTION and getattr(setup, "vdu_slope_confirmed", False):
            quality_adj += w_vdu * 5.0

        # 3. Continuous Distance-to-Trigger & Tightness
        if setup.state == State.CONTRACTION and getattr(setup, "trigger_tightness_confirmed", False):
            quality_adj += w_tight * 5.0

        # 4. Base Piercing / Failed Breakout Memory Penalty
        failed_count = getattr(setup, "failed_breakouts_in_base", 0)
        if failed_count > 0:
            quality_adj -= w_failed * min(2, failed_count) * 5.0

    # ----------------------------------------------------
    # Phase 3: Confluence & Context Modifiers (Behind Config Weights)
    # ----------------------------------------------------
    context_adj = 0.0
    if context_cfg and context_cfg.get("enabled", False):
        w_4h = float(context_cfg.get("htf_4h_weight", 0.0))
        w_regime = float(context_cfg.get("market_regime_weight", 0.0))
        w_earnings = float(context_cfg.get("earnings_risk_weight", 0.0))

        # 1. 4H Structure Alignment Bonus
        if getattr(setup, "htf_4h_confirmed", False):
            context_adj += w_4h * 5.0

        # 2. Market Regime Volatility Dampener
        regime = getattr(setup, "market_regime", "NORMAL")
        if regime == "HIGH_VOL_SELLOFF":
            context_adj -= w_regime * 8.0
        elif regime == "BEAR_TREND":
            context_adj -= w_regime * 4.0

        # 3. Earnings Risk Warning Penalty
        if getattr(setup, "earnings_warning", False):
            context_adj -= w_earnings * 5.0

    final_sum = raw_sum + quality_adj + context_adj
    total_score = int(min(100, max(0, round(final_sum)))) if is_finite_num(final_sum) else 0

    if total_score >= 90:
        grade = "A+"
    elif total_score >= 80:
        grade = "A"
    elif total_score >= 70:
        grade = "B"
    else:
        grade = "C"

    # Persist score, grade, and unconfirmed evidence flags onto Setup model
    setup.composite_score = total_score
    setup.composite_grade = grade
    setup.evidence_status = list(dict.fromkeys(unconfirmed))

    return total_score, grade
