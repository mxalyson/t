"""Simulação de carteira multi-símbolo com sizing por risco e limites de exposição.

  * Cada trade arrisca `risk` da equity corrente se bater o SL: notional = risk / sl_pct
    (limitado a `max_lev` × equity por posição).
  * No máximo `max_pos` posições abertas e no máximo `max_net` posições líquidas na mesma direção.
  * Quando há mais candidatos que vagas no mesmo instante, entram os de maior score.
  * Custos e funding já descontados no retorno de cada trade (lib.net_return).
  * Equity marcada a mercado apenas na saída (os trades duram poucos dias).
"""
import numpy as np
import pandas as pd

from lib import COST_RT, FUNDING_8H, atr, simulate_barrier, _utc


def candidates(data, scores, sides, tp_mult, sl_mult, H, bar_hours):
    """scores/sides: dict símbolo -> Series (score>0 => candidato). Pré-calcula o resultado de cada trade."""
    rows = []
    for s, df in data.items():
        if s not in scores:
            continue
        sc = scores[s].reindex(df.index).fillna(0).values
        sd = sides[s].reindex(df.index).fillna(0).astype(np.int64).values
        ap = (atr(df) / df["close"]).values
        o, h, l, c = (df[k].values for k in ["open", "high", "low", "close"])
        for t in np.nonzero((sc > 0) & (sd != 0))[0]:
            if np.isnan(ap[t]):
                continue
            r, b, ty = simulate_barrier(o, h, l, c, t, int(sd[t]), tp_mult * ap[t], sl_mult * ap[t], H)
            if np.isnan(r):
                continue
            rows.append((s, df.index[t], int(sd[t]), sc[t], r, b, ty, sl_mult * ap[t]))
    cd = pd.DataFrame(rows, columns=["symbol", "signal_time", "side", "score", "gross", "bars", "exit", "sl_pct"])
    cd["entry_time"] = cd["signal_time"] + pd.Timedelta(hours=bar_hours)
    cd["exit_time"] = cd["entry_time"] + pd.to_timedelta(cd["bars"] * bar_hours, unit="h")
    cd["net"] = cd["gross"] - COST_RT - FUNDING_8H * cd["bars"] * bar_hours / 8
    return cd.sort_values(["entry_time", "score"], ascending=[True, False]).reset_index(drop=True)


def simulate(cd, risk=0.005, max_pos=10, max_net=10, max_lev=1.0, equity0=1.0):
    eq = equity0
    open_pos = []          # (exit_time, symbol, side, notional, net)
    busy = {}
    taken = []
    curve = []
    for et, grp in cd.groupby("entry_time", sort=True):
        # fecha posições cuja saída ocorreu até este instante
        still = []
        for p in sorted(open_pos, key=lambda x: x[0]):
            if p[0] <= et:
                eq += p[3] * p[4]
                curve.append((p[0], eq))
                busy.pop(p[1], None)
            else:
                still.append(p)
        open_pos = still
        for r in grp.itertuples(index=False):
            if r.symbol in busy or len(open_pos) >= max_pos:
                continue
            net_dir = sum(p[2] for p in open_pos)
            if r.side * net_dir >= max_net:
                continue
            notional = min(risk / r.sl_pct, max_lev) * eq
            open_pos.append((r.exit_time, r.symbol, r.side, notional, r.net))
            busy[r.symbol] = True
            taken.append(r)
    for p in sorted(open_pos, key=lambda x: x[0]):
        eq += p[3] * p[4]
        curve.append((p[0], eq))
    tr = pd.DataFrame(taken)
    cv = pd.Series([c[1] for c in curve], index=pd.DatetimeIndex([c[0] for c in curve])).groupby(level=0).last()
    return tr, cv


def curve_stats(cv, start, end, tr=None, label=""):
    days = pd.date_range(_utc(start).floor("D"), _utc(end).floor("D"), freq="D")
    eq = cv.groupby(cv.index.floor("D")).last().reindex(days).ffill().fillna(1.0)
    dr = eq.pct_change().fillna(eq.iloc[0] - 1)
    yrs = len(days) / 365.25
    out = {"label": label, "total_ret": eq.iloc[-1] - 1,
           "cagr": eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1,
           "sharpe": dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0,
           "max_dd": (eq / eq.cummax() - 1).min()}
    out["calmar"] = out["cagr"] / -out["max_dd"] if out["max_dd"] < 0 else np.nan
    if tr is not None and len(tr):
        w = tr["net"] > 0
        out.update({"trades": len(tr), "trades_mes": len(tr) / (yrs * 12), "win_rate": w.mean(),
                    "avg_net_bps": tr["net"].mean() * 1e4,
                    "pf": tr.loc[w, "net"].sum() / -tr.loc[~w, "net"].sum()})
    return out, eq
