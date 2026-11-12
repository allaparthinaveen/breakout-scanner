import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from .models import Setup

logger = logging.getLogger(__name__)

class ProductionSafeguards:
    """
    Phase 9: Production safeguards for live execution.
    Contains pre-trade checks and global circuit breakers to prevent catastrophic losses.
    """
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.kill_switch_active = False
        self.daily_pnl = 0.0
        self.max_daily_loss = self.cfg.get('risk', {}).get('max_daily_loss_pct', 2.0) / 100.0
        self.kill_switch_file = (
            os.getenv("KILL_SWITCH_FILE")
            or self.cfg.get('safeguards', {}).get('kill_switch_file', "data/KILL_SWITCH")
        )
        self.check_kill_switch_triggers()

    def check_kill_switch_triggers(self) -> bool:
        """
        Evaluates environment variables and lockfile triggers.
        Returns True if kill switch is active or engaged.
        """
        if self.kill_switch_active:
            return True

        if os.getenv("KILL_SWITCH", "").lower() in ("true", "1", "yes"):
            self.engage_kill_switch("KILL_SWITCH environment variable is set")
            return True

        if self.kill_switch_file and os.path.exists(self.kill_switch_file):
            self.engage_kill_switch(f"Kill switch file trigger detected: {self.kill_switch_file}")
            return True

        return False

    def engage_kill_switch(self, reason: str):
        """Immediately halts all new trading activity."""
        self.kill_switch_active = True
        logger.critical(f"🛑 KILL SWITCH ENGAGED: {reason}")
        
    def reset_kill_switch(self):
        """Manually re-enables trading."""
        self.kill_switch_active = False
        logger.info("🟢 Kill switch disengaged. System active.")
        
    def check_stale_data(
        self,
        symbol: str,
        latest_timestamp: float,
        max_delay_seconds: Optional[int] = None,
        timeframe_seconds: int = 0
    ) -> bool:
        """
        Validates that the incoming market data is not severely delayed.
        Accounts for candle open timestamp semantics via timeframe_seconds.
        """
        if latest_timestamp <= 0:
            return True

        # Check if stale data enforcement is disabled in config
        if not self.cfg.get('safeguards', {}).get('enforce_stale_data', True):
            return True

        if max_delay_seconds is None:
            max_delay_seconds = int(self.cfg.get('safeguards', {}).get(
                'max_stale_data_seconds',
                self.cfg.get('risk', {}).get('max_stale_data_seconds', 120)
            ))

        now = time.time()
        effective_close = (
            latest_timestamp + timeframe_seconds
            if (now - latest_timestamp) > timeframe_seconds and timeframe_seconds > 0
            else latest_timestamp
        )
        delay = now - effective_close
        if delay > max_delay_seconds:
            logger.warning(f"⚠️ Stale Data Detected for {symbol}: Data is {int(delay)}s old (Max allowed: {max_delay_seconds}s).")
            return False
        return True
        
    def check_daily_loss_limit(self, current_capital: float, portfolio_state: Optional[Any] = None) -> bool:
        """
        Circuit breaker that trips if the daily loss exceeds the defined threshold.
        Syncs with persistent portfolio state if provided.
        """
        if portfolio_state is not None:
            self.daily_pnl = portfolio_state.get_total_daily_pnl()
            if portfolio_state.circuit_breaker_tripped:
                if not self.kill_switch_active:
                    self.engage_kill_switch(portfolio_state.circuit_breaker_reason or "Persistent Circuit Breaker Tripped")
                return False

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
        spread_pct = ((ask - bid) / ask) * 100 if ask > 0 else 999.0
        max_spread_pct = self.cfg.get('risk', {}).get('max_spread_pct', 0.5)
        
        if spread_pct > max_spread_pct:
            logger.warning(f"⚠️ Spread too wide for {symbol}: {spread_pct:.2f}% (Max: {max_spread_pct}%)")
            return False
            
        min_volume = self.cfg.get('risk', {}).get('min_liquidity_volume', 10000)
        if volume < min_volume:
            logger.warning(f"⚠️ Volume too low for {symbol}: {volume} (Min: {min_volume})")
            return False
            
        return True
        
    def pre_trade_validation(
        self,
        setup: Setup,
        current_capital: float,
        current_timestamp: Optional[float] = None,
        timeframe_seconds: int = 0,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        volume: Optional[float] = None
    ) -> bool:
        """
        Master gatekeeper function. Must return True before any order/alert is sent to live path.
        Enforces:
        1. Kill-switch check (env / lockfile / manual)
        2. Daily loss limit circuit breaker
        3. Stale data detection (>120s)
        4. Bid-ask spread and liquidity floor
        5. Max structural stop risk cap
        """
        if self.kill_switch_active or self.check_kill_switch_triggers():
            logger.info(f"[{setup.symbol}] Trade rejected: Kill switch is active.")
            return False
            
        if not self.check_daily_loss_limit(current_capital):
            return False
            
        if current_timestamp is not None and current_timestamp > 0:
            if not self.check_stale_data(setup.symbol, current_timestamp, timeframe_seconds=timeframe_seconds):
                return False

        if bid is not None and ask is not None:
            vol = volume if volume is not None else 10000.0
            if not self.check_liquidity_spread(setup.symbol, bid, ask, vol):
                return False

        max_stop_pct = self.cfg.get('rejection_gate', {}).get(
            'max_stop_pct',
            self.cfg.get('risk', {}).get('max_stop_pct', 5.0)
        )
        if setup.risk_pct is not None and setup.risk_pct > max_stop_pct:
            logger.warning(f"[{setup.symbol}] Trade rejected by safeguards: Risk {setup.risk_pct:.2f}% > max {max_stop_pct:.2f}%")
            return False
            
        # If all checks pass, order/alert is allowed
        return True
