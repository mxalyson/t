"""Núcleo do sistema: features causais, rótulos triple-barrier, backtest com custos e walk-forward.

Convenções anti look-ahead:
  * Cada barra é indexada pelo horário de ABERTURA. O sinal da barra t usa apenas OHLCV
    das barras <= t (conhecidas no fechamento de t).
  * A entrada ocorre na ABERTURA da barra t+1. TP/SL são verificados com high/low a
    partir da barra t+1. Se TP e SL forem tocados na mesma barra, assume-se SL (pior caso).
  * Gaps: se a abertura de uma barra já ultrapassa o SL, a saída é na abertura (pior que o SL).
  * No treino walk-forward, só entram amostras cujo rótulo terminou ANTES do início do
    período de teste (purge de H+2 barras + embargo).
"""
import os

import numpy as np
import pandas as pd
from numba import njit

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]

# Custos por lado (perp Binance USDT-M, sem descontos): taker 0.05% + slippage 0.03%
FEE_SIDE = 0.00055  # Bybit taker (perp USDT) sem desconto VIP
SLIP_SIDE = 0.0003
COST_RT = 2 * (FEE_SIDE + SLIP_SIDE)          # 0.17% ida+volta
FUNDING_8H = 0.0001                            # 0.01%/8h cobrado SEMPRE (long e short) — conservador


def load(pq_dir, interval, symbols=SYMBOLS):
    out = {}
    for s in symbols:
        df = pd.read_parquet(f"{pq_dir}/{s}_{interval}.parquet")
        out[s] = df
    return out


# ----------------------------------------------------------------------------- features

def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def symbol_features(df, bars_per_day):
    """Features de uma série, todas causais (rolling/ewm sobre o passado)."""
    f = pd.DataFrame(index=df.index)
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    lr = np.log(c).diff()
    vol = lr.rolling(4 * bars_per_day, min_periods=bars_per_day).std()
    f["vol_d"] = lr.rolling(bars_per_day).std()
    f["vol_w"] = lr.rolling(7 * bars_per_day, min_periods=2 * bars_per_day).std()
    f["vol_ratio"] = f["vol_d"] / f["vol_w"]
    for k in [1, 2, 4, 8, 16, 32, 64, 128, 256]:
        f[f"ret_{k}"] = np.log(c / c.shift(k)) / (vol * np.sqrt(k))
    a = atr(df)
    f["atr_pct"] = a / c
    f["rsi"] = _rsi(c)
    for n in [24, 96, 288]:
        hh, ll = h.rolling(n).max(), l.rolling(n).min()
        f[f"stoch_{n}"] = (c - ll) / (hh - ll)
    for n in [20, 50, 200]:
        f[f"ema_dist_{n}"] = (c - c.ewm(span=n, adjust=False).mean()) / a
    f["ema_slope_50"] = c.ewm(span=50, adjust=False).mean().pct_change(10) / f["atr_pct"]
    lv = np.log1p(v)
    f["vol_z"] = (lv - lv.rolling(4 * bars_per_day).mean()) / lv.rolling(4 * bars_per_day).std()
    if "taker_buy_base" in df:
        tb = (df["taker_buy_base"] / v.replace(0, np.nan)).fillna(0.5)
        f["taker_1"] = tb - 0.5
        f["taker_8"] = tb.rolling(8).mean() - 0.5
        f["taker_dev"] = tb.rolling(8).mean() - tb.rolling(8 * bars_per_day).mean()
        lt = np.log1p(df["trades"])
        f["trades_z"] = (lt - lt.rolling(4 * bars_per_day).mean()) / lt.rolling(4 * bars_per_day).std()
    rng = (h - l).replace(0, np.nan)
    f["body"] = (c - o) / rng
    f["upper_wick"] = (h - np.maximum(c, o)) / rng
    f["lower_wick"] = (np.minimum(c, o) - l) / rng
    if os.environ.get("FEATS") == "v2":
        # tendência de prazo longo (em dias), todas causais
        for d in [14, 30, 60, 120]:
            k = d * bars_per_day
            f[f"lt_ret_{d}d"] = np.log(c / c.shift(k)) / (vol * np.sqrt(k))
        f["lt_ma_dist_100d"] = np.log(c / c.rolling(100 * bars_per_day, min_periods=50 * bars_per_day).mean())
        f["lt_dd_from_high_90d"] = c / h.rolling(90 * bars_per_day, min_periods=30 * bars_per_day).max() - 1
    f["hour"] = df.index.hour
    f["dow"] = df.index.dayofweek
    return f


def panel_features(data, bars_per_day):
    """Features por símbolo + features de mercado/cross-section (mesmo timestamp => causal)."""
    feats = {s: symbol_features(df, bars_per_day) for s, df in data.items()}
    idx = sorted(set().union(*[f.index for f in feats.values()]))
    closes = pd.DataFrame({s: d["close"] for s, d in data.items()}).reindex(idx)
    lr = np.log(closes).diff()
    mkt = lr.mean(axis=1)
    btc = lr["BTCUSDT"] if "BTCUSDT" in lr else mkt
    mfe = pd.DataFrame(index=idx)
    for k in [1, 4, 8, 24, 72]:
        mfe[f"mkt_ret_{k}"] = mkt.rolling(k).sum() / (mkt.rolling(96).std() * np.sqrt(k))
        mfe[f"btc_ret_{k}"] = btc.rolling(k).sum() / (btc.rolling(96).std() * np.sqrt(k))
    mfe["breadth_24"] = (lr.rolling(24).sum() > 0).mean(axis=1)
    r24 = lr.rolling(24).sum()
    r72 = lr.rolling(72).sum()
    rank24 = r24.rank(axis=1, pct=True)
    rank72 = r72.rank(axis=1, pct=True)
    beta = lr.rolling(30 * bars_per_day, min_periods=10 * bars_per_day).cov(mkt).div(
        mkt.rolling(30 * bars_per_day, min_periods=10 * bars_per_day).var(), axis=0)
    out = []
    for i, (s, f) in enumerate(feats.items()):
        f = f.join(mfe, how="left")
        f["xs_rank_24"] = rank24[s]
        f["xs_rank_72"] = rank72[s]
        f["rel_ret_24"] = (r24[s] - r24.mean(axis=1))
        f["beta"] = beta[s]
        f["sym"] = i
        f["symbol"] = s
        out.append(f)
    return pd.concat(out)


# ----------------------------------------------------------------------------- barreiras

@njit(cache=True)
def simulate_barrier(o, h, l, c, t, side, tp_pct, sl_pct, H):
    """Simula um trade com sinal na barra t (entrada em o[t+1]).
    Retorna (retorno bruto, barras mantidas, tipo saída: 1 TP, -1 SL, 0 tempo)."""
    n = len(o)
    e = t + 1
    if e >= n:
        return np.nan, 0, 0
    entry = o[e]
    if side == 1:
        tp = entry * (1 + tp_pct)
        sl = entry * (1 - sl_pct)
    else:
        tp = entry * (1 - tp_pct)
        sl = entry * (1 + sl_pct)
    last = min(e + H - 1, n - 1)
    for j in range(e, last + 1):
        if j > e:  # gaps na abertura
            if side == 1 and o[j] <= sl:
                return o[j] / entry - 1, j - e + 1, -1
            if side == -1 and o[j] >= sl:
                return -(o[j] / entry - 1), j - e + 1, -1
            if side == 1 and o[j] >= tp:
                return o[j] / entry - 1, j - e + 1, 1
            if side == -1 and o[j] <= tp:
                return -(o[j] / entry - 1), j - e + 1, 1
        if side == 1:
            if l[j] <= sl:
                return sl / entry - 1, j - e + 1, -1
            if h[j] >= tp:
                return tp / entry - 1, j - e + 1, 1
        else:
            if h[j] >= sl:
                return -(sl / entry - 1), j - e + 1, -1
            if l[j] <= tp:
                return -(tp / entry - 1), j - e + 1, 1
    if last < e + H - 1:   # dados acabaram antes do horizonte
        return np.nan, 0, 0
    return side * (c[last] / entry - 1), last - e + 1, 0


@njit(cache=True)
def label_all(o, h, l, c, atrp, side, tp_mult, sl_mult, H):
    n = len(o)
    r = np.full(n, np.nan)
    bars = np.zeros(n, np.int64)
    typ = np.zeros(n, np.int64)
    for t in range(n):
        if np.isnan(atrp[t]):
            continue
        r[t], bars[t], typ[t] = simulate_barrier(o, h, l, c, t, side, tp_mult * atrp[t], sl_mult * atrp[t], H)
    return r, bars, typ


def net_return(gross, bars, bar_hours):
    return gross - COST_RT - FUNDING_8H * bars * bar_hours / 8.0


# ----------------------------------------------------------------------------- execução dos sinais

@njit(cache=True)
def run_signals(o, h, l, c, sig, tp_pct, sl_pct, H):
    """Executa sinais sem sobreposição (1 posição por símbolo). sig[t] in {-1,0,1}."""
    n = len(o)
    ent = []
    ret = []
    bars = []
    typ = []
    sides = []
    t = 0
    while t < n:
        if sig[t] != 0 and not np.isnan(tp_pct[t]):
            r, b, ty = simulate_barrier(o, h, l, c, t, sig[t], tp_pct[t], sl_pct[t], H)
            if np.isnan(r):
                break
            ent.append(t)
            ret.append(r)
            bars.append(b)
            typ.append(ty)
            sides.append(sig[t])
            t = t + b  # próximo sinal possível no fechamento da barra de saída
            continue
        t += 1
    return np.array(ent), np.array(ret), np.array(bars), np.array(typ), np.array(sides)


def backtest(data, signals, tp_mult, sl_mult, H, bar_hours, atr_n=14):
    """signals: dict símbolo -> pd.Series (-1/0/1) indexada como data[s]. Retorna DataFrame de trades."""
    rows = []
    for s, df in data.items():
        if s not in signals:
            continue
        sig = signals[s].reindex(df.index).fillna(0).astype(np.int64).values
        ap = (atr(df, atr_n) / df["close"]).values
        e, r, b, ty, sd = run_signals(df["open"].values, df["high"].values, df["low"].values,
                                      df["close"].values, sig, tp_mult * ap, sl_mult * ap, H)
        if len(e) == 0:
            continue
        t = pd.DataFrame({"symbol": s, "signal_time": df.index[e], "side": sd, "gross": r,
                          "bars": b, "exit": ty})
        t["entry_time"] = t["signal_time"] + pd.Timedelta(hours=bar_hours)
        t["exit_time"] = t["entry_time"] + pd.to_timedelta(t["bars"] * bar_hours, unit="h")
        t["net"] = net_return(t["gross"], t["bars"], bar_hours)
        rows.append(t)
    if not rows:
        return pd.DataFrame(columns=["symbol", "signal_time", "side", "gross", "bars", "exit", "net"])
    return pd.concat(rows).sort_values("entry_time").reset_index(drop=True)


def portfolio_stats(trades, n_symbols, start, end, alloc=None, label=""):
    """Carteira: cada símbolo recebe 1/n_symbols do capital (alavancagem 1x por posição).
    PnL realizado na data de saída. Retorna dict de métricas."""
    alloc = alloc or 1.0 / n_symbols
    if trades.empty:
        return {"label": label, "trades": 0}
    tr = trades.copy()
    tr["day"] = tr["exit_time"].dt.floor("D")
    days = pd.date_range(_utc(start).floor("D"), _utc(end).floor("D"), freq="D")
    daily = (tr.groupby("day")["net"].sum() * alloc).reindex(days, fill_value=0.0)
    eq = (1 + daily).cumprod()
    yrs = len(days) / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    wins = tr["net"] > 0
    pf = tr.loc[wins, "net"].sum() / -tr.loc[~wins, "net"].sum() if (~wins).any() else np.inf
    return {"label": label, "trades": len(tr), "trades_por_mes": len(tr) / (yrs * 12),
            "win_rate": wins.mean(), "avg_net_bps": tr["net"].mean() * 1e4,
            "avg_gross_bps": tr["gross"].mean() * 1e4, "profit_factor": pf,
            "total_ret": eq.iloc[-1] - 1, "cagr": cagr, "sharpe": sharpe, "max_dd": dd,
            "long_frac": (tr["side"] == 1).mean()}


def _utc(x):
    x = pd.Timestamp(x)
    return x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC")


def month_starts(start, end):
    return list(pd.date_range(_utc(start), _utc(end), freq="MS"))
