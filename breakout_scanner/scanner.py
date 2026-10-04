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
        print("\n=======================================================")
        print(" SECTION 1: ◉ VCP BASE (Actively Ready To Provide Signal)")
        print("=======================================================")
        
        base_setups = [s for s in setups if s.state == State.CONTRACTION]
        if not base_setups:
            print("  (None)")
        else:
            for s in base_setups:
                res = f"\033[91mResistance: {s.resistance:.5g}\033[0m"
                sup = f"\033[92mSupport: {s.support:.5g}\033[0m"
                print(f"  • {s.symbol:8} | {res} | {sup}")

        print("\n=======================================================")
        print(" SECTION 2: 🚀 ACTIVE SIGNALS (Trade In Progress)")
        print("=======================================================")
        
        active_setups = [s for s in setups if s.state in (State.BUY, State.SELL)]
        if not active_setups:
            print("  (None)")
        else:
            for s in active_setups:
                dir_str = "\033[92m▲ BUY\033[0m" if s.direction == 1 else "\033[91m▼ SELL\033[0m"
                entry = f"Entry: {s.entry:.5g}"
                stop = f"Stop: {s.stop:.5g}"
                target = f"Target: {s.target:.5g}"
                print(f"  • {s.symbol:8} | {dir_str} | {entry} | {stop} | {target}")
                
        print("\n")
