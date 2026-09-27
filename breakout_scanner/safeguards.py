import logging
import time
from datetime import datetime, timezone
from typing import Dict
from .models import Setup

logger = logging.getLogger(__name__)

class ProductionSafeguards:
    """
    Phase 9: Production safeguards for live execution.
    Contains pre-trade checks and global circuit breakers to prevent catastrophic losses.
    """
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.kill_switch_active = False
        self.daily_pnl = 0.0
        self.max_daily_loss = cfg['risk'].get('max_daily_loss_pct', 2.0) / 100.0
        
    def engage_kill_switch(self, reason: str):
        """Immediately halts all new trading activity."""
        self.kill_switch_active = True
        logger.critical(f"🛑 KILL SWITCH ENGAGED: {reason}")
        # In a production environment, this would also optionally trigger a "flatten all positions" API call.
        
    def reset_kill_switch(self):
        """Manually re-enables trading."""
        self.kill_switch_active = False
        logger.info("🟢 Kill switch disengaged. System active.")
        
    def check_stale_data(self, symbol: str, latest_timestamp: float, max_delay_seconds: int = 120) -> bool:
        """
        Validates that the incoming market data is not severely delayed.
        """
        now = time.time()
        if (now - latest_timestamp) > max_delay_seconds:
            logger.warning(f"⚠️ Stale Data Detected for {symbol}: Data is {int(now - latest_timestamp)}s old.")
            return False
        return True
        
    def check_daily_loss_limit(self, current_capital: float) -> bool:
        """
        Circuit breaker that trips if the daily loss exceeds the defined threshold.
        """
        max_loss_cash = current_capital * self.max_daily_loss
        if self.daily_pnl <= -max_loss_cash:
            if not self.kill_switch_active:
                self.engage_kill_switch(f"Daily loss limit reached (-${abs(self.daily_pnl):.2f})")
            return False
        return True
        
    def check_liquidity_spread(self, symbol: str, bid: float, ask: float, volume: float) -> bool:
        """
        Prevents entering trades during flash crashes, low liquidity, or widened spreads.
        """
        spread_pct = ((ask - bid) / ask) * 100
        max_spread_pct = self.cfg['risk'].get('max_spread_pct', 0.5)
        
        if spread_pct > max_spread_pct:
            logger.warning(f"⚠️ Spread too wide for {symbol}: {spread_pct:.2f}% (Max: {max_spread_pct}%)")
            return False
            
        min_volume = self.cfg['risk'].get('min_liquidity_volume', 10000)
        if volume < min_volume:
            logger.warning(f"⚠️ Volume too low for {symbol}: {volume} (Min: {min_volume})")
            return False
            
        return True
        
    def pre_trade_validation(self, setup: Setup, current_capital: float, current_timestamp: float) -> bool:
        """
        Master gatekeeper function. Must return True before any order is sent to the broker.
        """
        if self.kill_switch_active:
            logger.info(f"[{setup.symbol}] Trade rejected: Kill switch is active.")
            return False
            
        if not self.check_daily_loss_limit(current_capital):
            return False
            
        if not self.check_stale_data(setup.symbol, current_timestamp):
            return False
            
        # If all checks pass, order is allowed
        return True
