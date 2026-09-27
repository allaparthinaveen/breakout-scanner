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

def trend_from_structure(df,n=3,min_swings=2):
    ph,pl=pivots(df,n)
    hh,lh,hl,ll=0,0,0,0
    trend=0
    last_h,last_l=np.nan,np.nan
    for i in range(len(df)):
        if not np.isnan(ph[i]):
            prev_h=last_h; last_h=ph[i]
            if not np.isnan(prev_h):
                if last_h>prev_h:
                    hh+=1; lh=0
                elif last_h<prev_h:
                    lh+=1; hh=0
        if not np.isnan(pl[i]):
            prev_l=last_l; last_l=pl[i]
            if not np.isnan(prev_l):
                if last_l>prev_l:
                    hl+=1
                elif last_l<prev_l:
                    ll+=1
        if hh>=min_swings and hl>=min_swings: trend=1
        elif lh>=min_swings and ll>=min_swings: trend=-1
    return trend
