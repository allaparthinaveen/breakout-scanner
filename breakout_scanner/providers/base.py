from abc import ABC,abstractmethod
class MarketDataProvider(ABC):
 @abstractmethod
 def candles(self,symbol,period='60d',interval='1h'): ...
