from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class MarketDataProvider(ABC):
    @abstractmethod
    def candles(self, symbol: str, period: str = '60d', interval: str = '1h'):
        ...

    def get_ticker_quote(
        self,
        symbol: str,
        fallback_price: Optional[float] = None,
        atr: Optional[float] = None
    ) -> Dict[str, Any]:
        return {}

