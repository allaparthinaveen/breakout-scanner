import typer
from .config import load_config
from .providers.yfinance_provider import YFinanceProvider
from .scanner import MultiSymbolScanner
app=typer.Typer()
@app.command()
def scan(symbols:str='',watchlist:str='',period:str='60d',interval:str='1h',config:str='config/default.yaml'):
 cfg=load_config(config)
 syms=[x.split(',')[0].strip() for x in open(watchlist) if x.strip() and not x.strip().startswith('#')] if watchlist else [x.strip() for x in symbols.split(',') if x.strip()]
 scanner=MultiSymbolScanner(YFinanceProvider(),cfg)
 scanner.report(scanner.scan(syms,period,interval))
if __name__=='__main__':app()
