import typer
from .config import load_config
from .providers.yfinance_provider import YFinanceProvider
from .scanner import MultiSymbolScanner
app=typer.Typer()
@app.command()
def scan(symbols:str='',watchlist:str='',period:str='60d',interval:str='1h',config:str='config/default.yaml'):
 cfg=load_config(config)
 syms=[x.strip() for x in open(watchlist) if x.strip()] if watchlist else [x.strip() for x in symbols.split(',') if x.strip()]
 MultiSymbolScanner(YFinanceProvider(),cfg).report(MultiSymbolScanner(YFinanceProvider(),cfg).scan(syms,period,interval))
if __name__=='__main__':app()
