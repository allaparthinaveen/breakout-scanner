import time
from typing import Dict, List
from .engine import BreakoutEngine
from .prediction_chat import explain
from .models import Setup, State

class MultiSymbolScanner:
    def __init__(self, provider, cfg):
        self.provider = provider
        self.cfg = cfg
        self.engines: Dict[str, BreakoutEngine] = {}

    def get_engine(self, symbol: str) -> BreakoutEngine:
        if symbol not in self.engines:
            self.engines[symbol] = BreakoutEngine(self.cfg, symbol)
        return self.engines[symbol]

    def scan(self, symbols: List[str], period='60d', interval='1h') -> List[Setup]:
        """
        Poll-based scan that maintains state. In a real system, this could be 
        triggered periodically (e.g., every minute) as a fallback to websockets.
        """
        out = []
        for symbol in symbols:
            try:
                df = self.provider.candles(symbol, period, interval)
                if not df.empty:
                    engine = self.get_engine(symbol)
                    setup = engine.run(df)
                    out.append(setup)
            except Exception as e:
                print(f"Error scanning {symbol}: {e}")
        return out

    def report(self, setups: List[Setup]):
        for s in setups:
            print(f'{s.symbol:8} {s.state.value:24} trend={s.trend:+d} score={s.breakout_score}/5')
            if s.state != State.IDLE:
                print('  ' + explain(s).replace('\n', '\n  '))
