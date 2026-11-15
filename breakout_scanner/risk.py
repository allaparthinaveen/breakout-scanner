from typing import Dict
from .models import Setup, State

class RiskEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        
    def calculate_stop_loss(self, setup: Setup, df, current_index: int, method: str = 'baseline') -> float:
        """
        Calculate stop loss based on chosen method.
        Methods:
        - baseline: Stop beyond the breakout level with configurable buffer.
        - box_invalidation: Stop beyond the consolidation box.
        - retest_swing: Stop beyond the retest extreme.
        - atr: ATR based stop.
        """
        buf = self.cfg['breakout']['buffer_pct'] / 100.0
        d = setup.direction
        level = setup.breakout_level or (setup.resistance if d == 1 else setup.support) or setup.entry
        if level is None:
            return 0.0
        
        if method == 'baseline':
            return level * (1 - buf) if d == 1 else level * (1 + buf)
            
        elif method == 'box_invalidation':
            ref = setup.support if d == 1 else setup.resistance
            if ref is None:
                ref = level
            return ref * (1 - buf) if d == 1 else ref * (1 + buf)
            
        elif method == 'retest_swing':
            # This requires access to the lows/highs during the retest phase.
            # Simplified for now: just use the baseline or an estimated recent extreme
            return level * (1 - buf) if d == 1 else level * (1 + buf)
            
        elif method == 'atr':
            atr = float(df['_ATR'].iloc[current_index])
            multiplier = 2.0
            return setup.entry - (atr * multiplier) if d == 1 else setup.entry + (atr * multiplier)
            
        return level * (1 - buf) if d == 1 else level * (1 + buf)
        
    def calculate_take_profit(self, entry: float, stop: float, direction: int, rr: float = 2.0) -> float:
        risk = abs(entry - stop)
        if direction == 1:
            return entry + (risk * rr)
        else:
            return entry - (risk * rr)
            
    def validate_structural_risk(self, entry: float, stop: float) -> tuple:
        """
        Validates whether the structural risk is within acceptable parameters.
        Returns: (is_valid: bool, risk_pct: float, reason: str)
        """
        if entry is None or stop is None or entry <= 0:
            return False, 0.0, "INVALID_PRICES"
            
        risk_pct = (abs(entry - stop) / entry) * 100.0
        max_stop = self.cfg['risk'].get('max_stop_pct', 5.0)
        min_stop = self.cfg['risk'].get('min_stop_pct', 0.75)
        
        if risk_pct > max_stop:
            return False, risk_pct, f"EXCEEDS_MAX_RISK ({risk_pct:.2f}% > {max_stop:.2f}%)"
            
        return True, risk_pct, "VALID"

    def calculate_position_size(self, capital: float, entry: float, stop: float, current_positions: int = 0) -> float:
        """
        Calculates position size factoring in account size, risk %, and limits.
        Ensures exact dollar risk equals capital * risk_per_trade_pct (e.g. 1.0%).
        """
        if entry is None or stop is None or entry <= 0:
            return 0.0

        max_positions = self.cfg['risk'].get('max_positions', 5)
        if current_positions >= max_positions:
            return 0.0 # Portfolio limit reached
            
        risk_pct = self.cfg['risk'].get('risk_per_trade_pct', 1.0) / 100.0
        risk_cash = capital * risk_pct
        
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return 0.0
            
        shares = risk_cash / risk_per_share
        
        # Max exposure check (e.g. max 25% portfolio in single asset)
        max_exposure_pct = self.cfg['risk'].get('max_exposure_pct', 25.0) / 100.0
        max_notional = capital * max_exposure_pct
        
        if shares * entry > max_notional:
            shares = max_notional / entry
            
        return shares

