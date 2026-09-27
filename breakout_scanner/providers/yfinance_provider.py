import yfinance as yf
from .base import MarketDataProvider
class YFinanceProvider(MarketDataProvider):
 def candles(self,symbol,period='60d',interval='1h'):
  df=yf.download(symbol,period=period,interval=interval,auto_adjust=False,progress=False)
  if df.empty:return df
  if hasattr(df.columns,'levels'):df.columns=[c[0] if isinstance(c,tuple) else c for c in df.columns]
  return df[['Open','High','Low','Close','Volume']].dropna()
