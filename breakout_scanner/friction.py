"""
Phase 4: Institutional Microstructure & Friction Engine
======================================================
Models market-specific transaction frictions, statutory fees, and dynamic volatility slippage:
- NSE Equity: STT (delivery/intraday), Exchange turnover, GST (18%), SEBI charges, Stamp Duty.
- US Equity: SEC fees, FINRA TAF, clearing fees, execution commissions.
- Dynamic Volatility Slippage: Slippage scaled by ATR / Price ratio.
- Overnight Gap Risk Haircut: Statistical distribution for inter-session gap risk.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np


@dataclass
class FrictionBreakdown:
    symbol: str
    market: str
    shares: float
    entry_price: float
    stop_loss: float
    dollar_risk: float
    entry_turnover: float
    exit_turnover: float
    total_turnover: float
    brokerage: float
    stt_or_sec: float
    exchange_and_regulatory: float
    gst: float
    stamp_duty: float
    slippage_cost: float
    total_dollar_friction: float
    friction_r: float
    dynamic_slippage_pct: float
    gap_haircut_pct: float = 0.0


class MarketFrictionEngine:
    """
    Simulates institutional real-world execution costs and regulatory deductions.
    """
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        cfg = cfg or {}
        risk_cfg = cfg.get("risk", {})
        friction_cfg = cfg.get("friction", {})

        self.default_base_slippage_pct = float(friction_cfg.get("base_slippage_pct", risk_cfg.get("slippage_pct", 0.05)))
        self.default_commission_pct = float(friction_cfg.get("commission_pct", risk_cfg.get("commission_pct", 0.05)))
        self.max_friction_r = float(friction_cfg.get("max_friction_r", risk_cfg.get("max_friction_r", 0.15)))

    def detect_market(self, symbol: str) -> str:
        """Determines whether the symbol trades under NSE or US equity rules."""
        sym_upper = symbol.upper()
        if sym_upper.endswith(".NS") or sym_upper.endswith(".BO"):
            return "NSE"
        elif sym_upper.endswith("-USD") or sym_upper.endswith("-USDT"):
            return "CRYPTO"
        else:
            return "US"

    def calculate_dynamic_slippage_pct(self, price: float, atr: Optional[float] = None) -> float:
        """
        Dynamic Volatility Slippage:
        Slippage = max(Base Slippage %, 0.05 * (ATR14 / Price) * 100)
        """
        if price <= 0:
            return self.default_base_slippage_pct

        if atr is not None and atr > 0:
            vol_slip = 0.05 * (atr / price) * 100.0
            return float(max(self.default_base_slippage_pct, vol_slip))
        return float(self.default_base_slippage_pct)

    def calculate_round_trip_friction(
        self,
        symbol: str,
        entry: float,
        stop: float,
        shares: float,
        atr: Optional[float] = None,
        is_intraday: bool = False,
        held_overnight: bool = False
    ) -> FrictionBreakdown:
        """
        Computes granular, statutory round-trip friction and friction in R.
        """
        market = self.detect_market(symbol)
        shares = max(0.0, float(shares))
        entry = max(0.0, float(entry))
        stop = max(0.0, float(stop))
        per_share_risk = abs(entry - stop)
        dollar_risk = max(1e-4, shares * per_share_risk)

        entry_turnover = entry * shares
        exit_turnover = entry * shares  # Baseline round-trip assuming breakeven or target turnover
        total_turnover = entry_turnover + exit_turnover

        slippage_pct = self.calculate_dynamic_slippage_pct(entry, atr)
        slippage_cost = total_turnover * (slippage_pct / 100.0)

        # Gap risk haircut if held across market session close
        gap_haircut_cost = 0.0
        gap_haircut_pct = 0.0
        if held_overnight and atr is not None and atr > 0:
            gap_haircut_pct = float(0.02 * (atr / entry) * 100.0)
            gap_haircut_cost = entry_turnover * (gap_haircut_pct / 100.0)

        brokerage = 0.0
        stt_or_sec = 0.0
        exchange_regulatory = 0.0
        gst = 0.0
        stamp_duty = 0.0

        if market == "NSE":
            # 1. Brokerage: min(0.03%, Rs 20) per leg -> approx 0.03%
            brokerage = total_turnover * 0.0003
            # 2. STT / CTT: 0.1% on delivery (both legs), 0.025% on intraday (sell leg only)
            if is_intraday:
                stt_or_sec = exit_turnover * 0.00025
            else:
                stt_or_sec = total_turnover * 0.0010
            # 3. Exchange turnover charges: 0.00345% of total turnover
            exchange_turnover_fee = total_turnover * 0.0000345
            # 4. SEBI turnover charges: 0.0001% of total turnover
            sebi_charges = total_turnover * 0.000001
            exchange_regulatory = exchange_turnover_fee + sebi_charges
            # 5. GST: 18% on (Brokerage + Exchange turnover charges + SEBI charges)
            gst = (brokerage + exchange_turnover_fee + sebi_charges) * 0.18
            # 6. Stamp duty: 0.015% on buy turnover
            stamp_duty = entry_turnover * 0.00015

        elif market == "US":
            # 1. Base commission
            brokerage = total_turnover * (self.default_commission_pct / 100.0)
            # 2. SEC fee: 0.00278% on sell leg
            stt_or_sec = exit_turnover * 0.0000278
            # 3. FINRA TAF: $0.000166 per share (sell leg, capped at $8.30) + clearing $0.00020/share
            taf = min(8.30, shares * 0.000166)
            clearing = shares * 0.00020 * 2.0  # both legs
            exchange_regulatory = taf + clearing
            gst = 0.0
            stamp_duty = 0.0

        else:  # CRYPTO or generic
            brokerage = total_turnover * (self.default_commission_pct / 100.0)
            stt_or_sec = 0.0
            exchange_regulatory = 0.0
            gst = 0.0
            stamp_duty = 0.0

        total_dollar_friction = (
            brokerage + stt_or_sec + exchange_regulatory + gst + stamp_duty + slippage_cost + gap_haircut_cost
        )
        friction_r = total_dollar_friction / dollar_risk

        return FrictionBreakdown(
            symbol=symbol,
            market=market,
            shares=round(shares, 2),
            entry_price=round(entry, 4),
            stop_loss=round(stop, 4),
            dollar_risk=round(dollar_risk, 2),
            entry_turnover=round(entry_turnover, 2),
            exit_turnover=round(exit_turnover, 2),
            total_turnover=round(total_turnover, 2),
            brokerage=round(brokerage, 2),
            stt_or_sec=round(stt_or_sec, 2),
            exchange_and_regulatory=round(exchange_regulatory, 2),
            gst=round(gst, 2),
            stamp_duty=round(stamp_duty, 2),
            slippage_cost=round(slippage_cost, 2),
            total_dollar_friction=round(total_dollar_friction, 2),
            friction_r=round(float(friction_r), 3),
            dynamic_slippage_pct=round(slippage_pct, 4),
            gap_haircut_pct=round(gap_haircut_pct, 4),
        )

