import numpy as np
import pandas as pd

def atr(df, period=14):
    tr=pd.concat([df.High-df.Low,(df.High-df.Close.shift()).abs(),(df.Low-df.Close.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(period).mean()

def pivots(df, n=3):
    h=df.High.to_numpy(); l=df.Low.to_numpy(); ph=np.full(len(df),np.nan); pl=np.full(len(df),np.nan)
    for i in range(n,len(df)-n):
        if h[i]==np.max(h[i-n:i+n+1]): ph[i]=h[i]
        if l[i]==np.min(l[i-n:i+n+1]): pl[i]=l[i]
    return ph,pl

def add_momentum_indicators(df, sma_len=50, vol_len=20):
    df = df.copy()
    # Simple Moving Average for macro trend
    df['SMA'] = df['Close'].rolling(sma_len).mean()
    # Average Volume
    df['AvgVol'] = df['Volume'].rolling(vol_len).mean()
    # Relative Volume
    df['RelVol'] = df['Volume'] / df['AvgVol']
    return df
