"""
Composite Quant Scorer (Phase 3 Step 4)
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
"""

from typing import Tuple
from .models import Setup, State

def calculate_composite_score(setup: Setup) -> Tuple[int, str]:
    """
    Computes a composite score (0-100) and grade ('A+', 'A', 'B', 'C')
    for a given Setup.
    """
    # ----------------------------------------------------
    # Pillar 1: Contraction Physics (Max 20 pts)
    # ----------------------------------------------------
    p1 = 0
    # Base Width tightness (Max 10 pts)
    if setup.base_width_pct is not None:
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
    else:
        p1 += 5

    # Coil decay & tightness confirmation (Max 10 pts)
    if setup.tightening_confirmed and setup.decay_ratio is not None and setup.decay_ratio <= 0.40:
        p1 += 10
    elif setup.tightening_confirmed or (setup.decay_ratio is not None and setup.decay_ratio <= 0.70):
        p1 += 8
    elif setup.decay_ratio is not None and setup.decay_ratio <= 0.85:
        p1 += 5
    elif setup.decay_ratio is None:
        p1 += 4
    else:
        p1 += 2

    # ----------------------------------------------------
    # Pillar 2: Volume Footprint (Max 20 pts)
    # ----------------------------------------------------
    p2 = 0
    if setup.state == State.CONTRACTION:
        if setup.vdu_confirmed and setup.vdu_ratio is not None and setup.vdu_ratio <= 0.60:
            p2 = 20
        elif setup.vdu_confirmed or (setup.vdu_ratio is not None and setup.vdu_ratio <= 0.75):
            p2 = 16
        elif setup.vdu_ratio is not None and setup.vdu_ratio <= 0.90:
            p2 = 11
        elif setup.vdu_ratio is None:
            p2 = 10  # Neutral fallback for volume-free assets
        else:
            p2 = 5
    else:  # Active Breakout / Retest
        if setup.rvol is not None:
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
            p2 = 10

    # ----------------------------------------------------
    # Pillar 3: Multi-Timeframe Trend Template (Max 20 pts)
    # ----------------------------------------------------
    p3 = 0
    # Daily (1D) Macro alignment (Max 12 pts)
    if setup.htf_confirmed:
        p3 += 12
    elif setup.htf_trend == 1 or (setup.direction == -1 and setup.htf_trend == -1):
        p3 += 8
    else:
        p3 += 2

    # Hourly (1H) Trend alignment (Max 8 pts)
    if setup.trend == 1 or (setup.direction != 0 and setup.state in (State.BUY, State.SELL)):
        p3 += 8
    else:
        p3 += 4

    # ----------------------------------------------------
    # Pillar 4: Relative Strength Alpha & Momentum Slope (Max 20 pts)
    # ----------------------------------------------------
    p4 = 0
    # Sector RS Delta (Max 10 pts)
    if setup.sector_rs_pct is not None:
        if setup.sector_rs_pct >= 3.0:
            p4 += 10
        elif setup.sector_rs_pct > 0.0:
            p4 += 8
        elif setup.sector_rs_pct >= -2.0:
            p4 += 5
        else:
            p4 += 2
    else:
        p4 += 6  # Neutral fallback

    # RS Linear Regression Slope (Max 10 pts)
    if setup.rs_trend == "↗":
        p4 += 10
    elif setup.rs_trend == "→":
        p4 += 6
    elif setup.rs_trend == "":
        p4 += 5
    else:  # "↘"
        p4 += 1

    # ----------------------------------------------------
    # Pillar 5: Risk-Reward Asymmetry & Execution Structure (Max 20 pts)
    # ----------------------------------------------------
    p5 = 0
    # Risk Tightness (Max 12 pts)
    if setup.risk_pct is not None:
        if setup.risk_pct <= 1.2:
            p5 += 12
        elif setup.risk_pct <= 2.0:
            p5 += 9
        elif setup.risk_pct <= 3.0:
            p5 += 6
        else:
            p5 += 2
    elif setup.base_width_pct is not None:
        if setup.base_width_pct <= 4.5:
            p5 += 12
        elif setup.base_width_pct <= 7.0:
            p5 += 9
        elif setup.base_width_pct <= 10.0:
            p5 += 6
        else:
            p5 += 3
    else:
        p5 += 6

    # Pattern / Invalidation Quality (Max 8 pts)
    if setup.state in (State.BUY, State.SELL):
        if setup.retest_confirmed:
            p5 += 8
        else:
            p5 += 6
    else:
        if setup.resistance is not None and setup.support is not None:
            p5 += 8
        else:
            p5 += 4

    total_score = min(100, max(0, p1 + p2 + p3 + p4 + p5))

    if total_score >= 90:
        grade = "A+"
    elif total_score >= 80:
        grade = "A"
    elif total_score >= 70:
        grade = "B"
    else:
        grade = "C"

    return total_score, grade
