import numpy as np,pandas as pd
from breakout_scanner.config import load_config
from breakout_scanner.engine import BreakoutEngine
def test_smoke():
 n=120;c=np.linspace(100,105,n);df=pd.DataFrame({'Open':c-.1,'High':c+.2,'Low':c-.2,'Close':c,'Volume':np.ones(n)*100})
 s=BreakoutEngine(load_config(),'TEST').run(df);assert s.symbol=='TEST'
