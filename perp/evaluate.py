"""Converte previsões OOS em sinais e faz o backtest com custos.

Uso: python evaluate.py PQ_DIR ITV TP SL H PREDS.parquet START END
"""
import sys

import numpy as np
import pandas as pd

from lib import backtest, load, portfolio_stats, _utc


def make_signals(pr, rule, thr):
    """rule='abs': p > thr ; rule='edge': p - base_rate_treino > thr."""
    pl, ps = pr["p_long"], pr["p_short"]
    if rule == "edge":
        pl, ps = pl - pr["base_long"], ps - pr["base_short"]
    side = np.where(pl >= ps, 1, -1)
    best = np.maximum(pl, ps)
    sig = pd.Series(np.where(best > thr, side, 0), index=pr.index)
    out = {}
    for s in pr["symbol"].unique():
        m = (pr["symbol"] == s).values
        out[s] = sig[m]
    return out


def evaluate(pq, itv, tp, sl, H, pr, start, end, rules, label=""):
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    data = {s: d[d.index < _utc(end)] for s, d in load(pq, itv).items()}
    pr = pr[(pr.index >= _utc(start)) & (pr.index < _utc(end))]
    res, trades = [], {}
    for rule, thr in rules:
        tr = backtest(data, make_signals(pr, rule, thr), tp, sl, H, bh)
        st = portfolio_stats(tr, len(data), start, end, label=f"{label} {rule}>{thr}")
        res.append(st)
        trades[(rule, thr)] = tr
    return pd.DataFrame(res), trades


def yearly(tr, n, label=""):
    rows = []
    for y, g in tr.groupby(tr["exit_time"].dt.year):
        st = portfolio_stats(g, n, f"{y}-01-01", f"{y}-12-31", label=str(y))
        rows.append(st)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pq, itv, tp, sl, H, pp, start, end = sys.argv[1:9]
    tp, sl, H = float(tp), float(sl), int(H)
    pr = pd.read_parquet(pp)
    rules = [("abs", t) for t in [0.40, 0.45, 0.50, 0.55, 0.60]] + [("edge", t) for t in [0.03, 0.06, 0.10, 0.15]]
    df, trades = evaluate(pq, itv, tp, sl, H, pr, start, end, rules, label=pp.split("/")[-1])
    pd.set_option("display.width", 250)
    print(df.round(3).to_string())
