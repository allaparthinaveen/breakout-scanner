import asyncio
import json
import logging
import os
import tempfile
from typing import Callable, Dict, List, Set
import pandas as pd
from .scanner import MultiSymbolScanner
from .models import Setup

logger = logging.getLogger(__name__)

class RealtimeFeed:
    """
    PROTOTYPE REALTIME FEED PROCESSOR (Mock / Demonstration).
    ⚠️ NOTE: connect_websocket_mock is an asynchronous demonstration stub and does
    not connect to a live exchange venue. Production feeds will use dedicated broker adapters.

    Decisions happen on confirmed candle closes. 
    Uses (symbol, timeframe, candle_close_time) as an idempotency key.
    """
    def __init__(
        self,
        scanner: MultiSymbolScanner,
        timeframe: str = '1h',
        persistence_file: str = "data/realtime_processed_candles.json"
    ):
        self.scanner = scanner
        self.timeframe = timeframe
        self.persistence_file = persistence_file
        self.processed_candles: Set[str] = set()
        self._load_persisted_keys()

    def _load_persisted_keys(self):
        if self.persistence_file and os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, "r") as f:
                    data = json.load(f)
                    self.processed_candles = set(data)
            except Exception as e:
                logger.warning(f"Failed to load realtime processed candles cache: {e}")

    def _persist_key(self, key: str):
        self.processed_candles.add(key)
        if not self.persistence_file:
            return
        dir_name = os.path.dirname(self.persistence_file) or "."
        os.makedirs(dir_name, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                json.dump(list(self.processed_candles), tf)
                tf.flush()
                os.fsync(tf.fileno())
                temp_path = tf.name
            os.replace(temp_path, self.persistence_file)
        except Exception as e:
            logger.error(f"Failed to persist realtime candle key: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _get_idempotency_key(self, symbol: str, timestamp: str) -> str:
        """Create a unique key to prevent processing the same candle twice."""
        return f"{symbol}|{self.timeframe}|{timestamp}"

    async def on_candle_closed(self, symbol: str, timestamp: str, open_p: float, high: float, low: float, close: float, volume: float, emit_alert: Callable):
        """
        Handler for when a websocket confirms a candle has closed.
        Idempotency key is recorded strictly AFTER successful processing to allow retry on fetch failure.
        """
        key = self._get_idempotency_key(symbol, timestamp)
        if key in self.processed_candles:
            logger.debug(f"Duplicate candle dropped: {key}")
            return

        try:
            # We use the provider to fetch the latest dataframe including this close
            df = self.scanner.provider.candles(symbol, period='60d', interval=self.timeframe)
            if not df.empty:
                engine = self.scanner.get_engine(symbol)
                old_state = engine.setup.state

                setup = engine.run(df)

                # Record idempotency key strictly after successful fetch and evaluation (Fix P1-4)
                self._persist_key(key)

                # Emit alert if state changed
                if setup.state != old_state:
                    emit_alert(setup)

        except Exception as e:
            logger.error(f"Error processing real-time candle for {symbol}: {e}")
            # Key is NOT added on error, allowing transient retries

    async def connect_websocket_mock(self, symbols: List[str], emit_alert: Callable):
        """
        ⚠️ PROTOTYPE MOCK IMPLEMENTATION:
        Simulates websocket listening loop. Does not connect to live exchange.
        In production, replace this with ccxt WS, Binance WS, etc.
        """
        logger.info(f"Connecting mock websocket for {len(symbols)} symbols...")
        while True:
            await asyncio.sleep(60)
            pass

# Phase 0 Architectural Boundary: Explicitly alias RealtimeFeed as PrototypeMockFeed
PrototypeMockFeed = RealtimeFeed
