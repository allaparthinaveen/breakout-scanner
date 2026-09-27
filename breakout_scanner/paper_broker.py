import logging
from typing import Dict
from .models import Setup, State
from .risk import RiskEngine

logger = logging.getLogger(__name__)

class Position:
    def __init__(self, symbol: str, direction: int, entry_price: float, shares: float, stop_loss: float, take_profit: float):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.shares = shares
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.active = True

class PaperBroker:
    """
    Phase 8: Simulated execution environment for paper trading.
    Manages a virtual account balance and executes simulated market/limit orders.
    """
    def __init__(self, cfg: dict, initial_balance: float = 10000.0):
        self.cfg = cfg
        self.balance = initial_balance
        self.positions: Dict[str, Position] = {}
        self.risk_engine = RiskEngine(cfg)
        self.trade_history = []
        
    def execute_signal(self, setup: Setup, current_price: float):
        """
        Receives a validated setup from the RealtimeFeed and executes a paper order.
        """
        if setup.symbol in self.positions and self.positions[setup.symbol].active:
            logger.info(f"[{setup.symbol}] Ignored signal: Position already active.")
            return
            
        # 1. Calculate Risk & Position Size
        shares = self.risk_engine.calculate_position_size(
            capital=self.balance,
            entry=setup.entry,
            stop=setup.stop,
            current_positions=len([p for p in self.positions.values() if p.active])
        )
        
        if shares <= 0:
            logger.warning(f"[{setup.symbol}] Rejected: Insufficient capital or risk limit reached.")
            return
            
        # 2. Execute Order (Simulated Market Fill)
        # In a real broker (Phase 10), this would be an API call to Alpaca/Binance
        direction_str = "LONG" if setup.direction == 1 else "SHORT"
        
        position = Position(
            symbol=setup.symbol,
            direction=setup.direction,
            entry_price=current_price,
            shares=shares,
            stop_loss=setup.stop,
            take_profit=setup.target
        )
        
        self.positions[setup.symbol] = position
        logger.info(f"🟢 [PAPER EXECUTION] {direction_str} {setup.symbol} | Entry: {current_price:.2f} | Shares: {shares:.2f} | SL: {setup.stop:.2f} | TP: {setup.target:.2f}")

    def on_tick(self, symbol: str, current_price: float):
        """
        Processes real-time ticks to manage active stops and targets.
        """
        if symbol not in self.positions:
            return
            
        pos = self.positions[symbol]
        if not pos.active:
            return
            
        # Check Stop Loss & Take Profit
        exit_price = None
        reason = ""
        
        if pos.direction == 1: # LONG
            if current_price <= pos.stop_loss:
                exit_price = pos.stop_loss
                reason = "STOP LOSS"
            elif current_price >= pos.take_profit:
                exit_price = pos.take_profit
                reason = "TAKE PROFIT"
        else: # SHORT
            if current_price >= pos.stop_loss:
                exit_price = pos.stop_loss
                reason = "STOP LOSS"
            elif current_price <= pos.take_profit:
                exit_price = pos.take_profit
                reason = "TAKE PROFIT"
                
        if exit_price is not None:
            # Calculate PnL
            if pos.direction == 1:
                pnl = (exit_price - pos.entry_price) * pos.shares
            else:
                pnl = (pos.entry_price - exit_price) * pos.shares
                
            self.balance += pnl
            pos.active = False
            
            # Log Trade
            emoji = "✅" if pnl > 0 else "❌"
            logger.info(f"{emoji} [PAPER EXIT] {symbol} {reason} | Exit: {exit_price:.2f} | PnL: ${pnl:.2f} | New Balance: ${self.balance:.2f}")
            self.trade_history.append({'symbol': symbol, 'pnl': pnl, 'reason': reason})
