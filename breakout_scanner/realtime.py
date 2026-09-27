import asyncio
import json
import logging
from typing import Callable, Dict, List
import pandas as pd
from .scanner import MultiSymbolScanner
from .models import Setup

logger = logging.getLogger(__name__)

class RealtimeFeed:
    """
    Phase 5: Real-time websocket feed processor.
    Decisions happen on confirmed candle closes. 
    Uses (symbol, timeframe, candle_close_time) as an idempotency key.
    """
    def __init__(self, scanner: MultiSymbolScanner, timeframe: str = '1h'):
        self.scanner = scanner
        self.timeframe = timeframe
        self.processed_candles = set()
        
    def _get_idempotency_key(self, symbol: str, timestamp: str) -> str:
        """Create a unique key to prevent processing the same candle twice."""
        return f"{symbol}|{self.timeframe}|{timestamp}"

    async def on_candle_closed(self, symbol: str, timestamp: str, open_p: float, high: float, low: float, close: float, volume: float, emit_alert: Callable):
        """
        Handler for when a websocket confirms a candle has closed.
        """
        key = self._get_idempotency_key(symbol, timestamp)
        if key in self.processed_candles:
            logger.debug(f"Duplicate candle dropped: {key}")
            return
            
        self.processed_candles.add(key)
        
        # In a real system, we'd append this row to a local dataframe or database
        # and then run the engine.
        try:
            # We use the provider to fetch the latest dataframe including this close
            # Alternatively, maintain the dataframe in memory and just append.
            df = self.scanner.provider.candles(symbol, period='60d', interval=self.timeframe)
            if not df.empty:
                engine = self.scanner.get_engine(symbol)
                old_state = engine.setup.state
                
                setup = engine.run(df)
                
                # Emit alert if state changed
                if setup.state != old_state:
                    emit_alert(setup)
                    
        except Exception as e:
            logger.error(f"Error processing real-time candle for {symbol}: {e}")

    async def connect_websocket_mock(self, symbols: List[str], emit_alert: Callable):
        """
        Mock implementation of a websocket connection.
        In production, replace this with ccxt WS, Binance WS, etc.
        """
        logger.info(f"Connecting websocket for {len(symbols)} symbols...")
        # Simulate websocket listening loop
        while True:
            await asyncio.sleep(60)
            # Example of how a message would be parsed
            # msg = await ws.recv()
            # data = json.loads(msg)
            # if data['is_final']:
            #     await self.on_candle_closed(data['symbol'], data['timestamp'], ...)
            pass
