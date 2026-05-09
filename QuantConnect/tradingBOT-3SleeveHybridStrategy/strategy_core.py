"""Shared calculations for the Three-Sleeve Hybrid Alpaca port."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


LABEL_HORIZON = 21
SAFETY_BUFFER = 10
TRAIN_VAL_GAP = 126
MIN_TRAIN_ROWS = 100
MIN_VIX_BARS = 50
MIN_SPY_BARS = 260
MIN_AUX_BARS = 60
DIP_DEEP_THRESHOLD = -0.08
DIP_SHALLOW_SPY_W = 0.60
DIP_SHALLOW_SPY_W_ML = 0.75
DIP_DEEP_SPY_W = 0.85
DIP_DEEP_SPY_W_ML = 1.00


@dataclass
class SignalState:
    bull: bool
    regime_name: str
    s1_spy_weight: float
    s1_gld_weight: float
    s2_budget: float
    sleeves_active: bool
    ml_on: bool
    spy_price: float
    sma50: float
    sma200: float
    ret20: float
    vix: float
    vix80: float


def normalize_bars(df: pd.DataFrame, tz: str = "US/Eastern") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(col).lower().replace(" ", "_") for col in out.columns]
    rename = {"adj_close": "adjclose", "adj close": "adjclose"}
    out = out.rename(columns=rename)
    required = ["open", "high", "low", "close", "volume"]
    if any(col not in out.columns for col in required):
        return pd.DataFrame()
    if out.index.tz is None:
        out.index = out.index.tz_localize(tz)
    else:
        out.index = out.index.tz_convert(tz)
    return out[required].dropna(subset=["close"]).sort_index()


def closes(df: pd.DataFrame, end=None, n: int | None = None) -> np.ndarray:
    if df is None or df.empty:
        return np.array([])
    view = df
    if end is not None:
        ts = pd.to_datetime(end)
        if ts.tzinfo is None and view.index.tz is not None:
            ts = ts.tz_localize(view.index.tz)
        elif ts.tzinfo is not None and view.index.tz is not None:
            ts = ts.tz_convert(view.index.tz)
        view = view[view.index <= ts]
    if n is not None:
        view = view.tail(n)
    return view["close"].astype(float).to_numpy()


def pct_return(values: Iterable[float], lookback: int) -> float:
    arr = np.asarray(list(values), dtype=float)
    if len(arr) < lookback + 1 or arr[-lookback - 1] <= 0:
        return 0.0
    return float(arr[-1] / arr[-lookback - 1] - 1.0)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if df.empty or len(df) < period + 2:
        return pd.Series(index=df.index, dtype=float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def band_index(price: float, bands: list[float]) -> int:
    for idx in range(len(bands) - 1):
        if bands[idx] <= price < bands[idx + 1]:
            return idx
    return len(bands) - 2


def get_features(
    vix_c: np.ndarray,
    spy_c: np.ndarray,
    vix3m_c: np.ndarray | None = None,
    hyg_c: np.ndarray | None = None,
    lqd_c: np.ndarray | None = None,
    rsp_c: np.ndarray | None = None,
    ief_c: np.ndarray | None = None,
    shy_c: np.ndarray | None = None,
) -> list[float] | None:
    if len(vix_c) < MIN_VIX_BARS or len(spy_c) < MIN_SPY_BARS:
        return None
    try:
        cv = float(vix_c[-1])
        sc = float(spy_c[-1])
        vs20 = float(np.mean(vix_c[-20:]))
        vs50 = float(np.mean(vix_c[-50:]))
        vstd = float(np.std(vix_c[-20:]))
        vz = (cv - vs20) / vstd if vstd > 0 else 0.0
        vpr = float(np.sum(vix_c < cv)) / len(vix_c)
        ss50 = float(np.mean(spy_c[-50:]))
        ss200 = float(np.mean(spy_c[-200:]))
        s5 = spy_c[-1] / spy_c[-5] - 1
        s10 = spy_c[-1] / spy_c[-10] - 1
        s20 = spy_c[-1] / spy_c[-20] - 1
        svol = float(np.std(np.diff(spy_c[-21:]) / spy_c[-21:-1]))
        s60 = spy_c[-1] / spy_c[-60] - 1
        s120 = spy_c[-1] / spy_c[-120] - 1
        s252 = spy_c[-1] / spy_c[-252] - 1
        vtr = vt5 = 0.0
        if vix3m_c is not None and len(vix3m_c) >= 5 and vix3m_c[-1] > 0:
            vtr = cv / vix3m_c[-1]
            vt5 = (cv / vix_c[-5]) - (vix3m_c[-1] / vix3m_c[-5])
        cr = c5 = c20 = 0.0
        if hyg_c is not None and lqd_c is not None and len(hyg_c) >= MIN_AUX_BARS and len(lqd_c) >= MIN_AUX_BARS and lqd_c[-1] > 0:
            cr = hyg_c[-1] / lqd_c[-1]
            c5 = (hyg_c[-1] / hyg_c[-5]) - (lqd_c[-1] / lqd_c[-5])
            c20 = (hyg_c[-1] / hyg_c[-20]) - (lqd_c[-1] / lqd_c[-20])
        br = b5 = b20 = 0.0
        if rsp_c is not None and len(rsp_c) >= MIN_AUX_BARS and sc > 0:
            br = rsp_c[-1] / sc
            b5 = (rsp_c[-1] / rsp_c[-5]) - (spy_c[-1] / spy_c[-5])
            b20 = (rsp_c[-1] / rsp_c[-20]) - (spy_c[-1] / spy_c[-20])
        cu20 = cu60 = 0.0
        if ief_c is not None and shy_c is not None and len(ief_c) >= MIN_AUX_BARS and len(shy_c) >= MIN_AUX_BARS:
            cu20 = (ief_c[-1] / ief_c[-20]) - (shy_c[-1] / shy_c[-20])
            cu60 = (ief_c[-1] / ief_c[-60]) - (shy_c[-1] / shy_c[-60])
        return [
            cv, vz, vpr, cv / vs20, cv / vs50, s5, s10, s20, sc / ss50,
            sc / ss200, svol * np.sqrt(252), s60, s120, s252, vtr, vt5,
            cr, c5, c20, br, b5, b20, cu20, cu60,
        ]
    except Exception:
        return None


def evaluate_signal(
    spy_c: np.ndarray,
    vix_c: np.ndarray,
    ml_on: bool = False,
) -> SignalState | None:
    if len(spy_c) < MIN_SPY_BARS or len(vix_c) < MIN_VIX_BARS:
        return None

    cv = float(vix_c[-1])
    vsma = float(np.mean(vix_c[-20:]))
    v80 = float(np.percentile(vix_c, 80))
    sc = float(spy_c[-1])
    s50 = float(np.mean(spy_c[-50:]))
    s200 = float(np.mean(spy_c[-200:]))
    r5 = float(spy_c[-1] / spy_c[-5] - 1)
    r10 = float(spy_c[-1] / spy_c[-10] - 1)
    r20 = float(spy_c[-1] / spy_c[-20] - 1)

    sleeves_active = True
    if cv > v80 and r5 < -0.03:
        rs = (DIP_DEEP_SPY_W_ML if ml_on else DIP_DEEP_SPY_W) if r10 <= DIP_DEEP_THRESHOLD else (DIP_SHALLOW_SPY_W_ML if ml_on else DIP_SHALLOW_SPY_W)
        rg = max(0.0, 1.0 - rs)
        sleeves_active = False
        regime = "R1-dip"
    elif cv < 13 and sc > s50 * 1.05:
        rs, rg, regime = 0.40, 0.20, "R2-lowvol"
    elif 20 < cv < vsma:
        rs, rg, regime = (0.85 if ml_on else 0.70), 0.10, "R3-recovery"
    elif cv > vsma * 1.2:
        rs, rg, sleeves_active, regime = 0.30, 0.20, False, "R4-stress"
    elif sc > s200:
        rs, rg, regime = (0.70 if ml_on else 0.60), 0.15, "R5-trend"
    else:
        rs, rg, sleeves_active, regime = 0.30, 0.20, False, "R6-below200"

    bull = sc > s200 and sc > s50 and r20 > 0.0 and cv < v80 and cv < 25
    if bull:
        return SignalState(
            bull=True,
            regime_name="S3+S1-BULL",
            s1_spy_weight=0.15,
            s1_gld_weight=0.05,
            s2_budget=0.0,
            sleeves_active=True,
            ml_on=ml_on,
            spy_price=sc,
            sma50=s50,
            sma200=s200,
            ret20=r20,
            vix=cv,
            vix80=v80,
        )

    return SignalState(
        bull=False,
        regime_name=regime,
        s1_spy_weight=rs,
        s1_gld_weight=rg,
        s2_budget=max(0.0, 1.0 - rs - rg),
        sleeves_active=sleeves_active,
        ml_on=ml_on,
        spy_price=sc,
        sma50=s50,
        sma200=s200,
        ret20=r20,
        vix=cv,
        vix80=v80,
    )

