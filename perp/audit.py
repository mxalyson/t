"""Auditoria complementar pedida pela revisão independente.

NÃO altera nada do modelo congelado: usa as previsões OOS já gravadas e só estressa a avaliação.
Uso: python audit.py PQ_DIR PREDS.parquet START END OUT_DIR [N_SIMS]
"""
import json
import sys

import numpy as np
import pandas as pd

from lib import FUNDING_8H, atr, label_all, load, simulate_barrier, _utc
from portfolio import curve_stats, simulate

CFG = json.load(open("config.json"))
TP, SL, H, THR = CFG["tp"], CFG["sl"], CFG["H"], CFG["thr"]
BH = 2
PF = CFG["portfolio"]
SIDE_COST = 0.00055  # taker Bybit


def edges(pr):
    el, es = pr["p_long"] - pr["base_long"], pr["p_short"] - pr["base_short"]
    side = np.where(el >= es, 1, -1)
    return pd.DataFrame({"symbol": pr["symbol"].values, "ts": pr.index, "side": side,
                         "score": np.maximum(el, es).values})


class Book:
    """Pré-carrega arrays por símbolo para simular trades rapidamente."""

    def __init__(self, data):
        self.d = {}
        for s, df in data.items():
            self.d[s] = (df["open"].values, df["high"].values, df["low"].values, df["close"].values,
                         (atr(df) / df["close"]).values, df.index)

    def cands(self, sig, slip=0.0003, delay=0, end=None):
        """sig: DataFrame symbol, ts, side, score (já filtrado). Entrada em open[t+1+delay]."""
        rows = []
        for s, g in sig.groupby("symbol"):
            o, h, l, c, ap, idx = self.d[s]
            pos = idx.get_indexer(g["ts"])
            for t, sd, sc in zip(pos, g["side"].values, g["score"].values):
                if t < 0 or np.isnan(ap[t]):
                    continue
                r, b, ty = simulate_barrier(o, h, l, c, t + delay, int(sd), TP * ap[t], SL * ap[t], H)
                if np.isnan(r):
                    continue
                rows.append((s, idx[t], int(sd), sc, r, b, ty, SL * ap[t]))
        cd = pd.DataFrame(rows, columns=["symbol", "signal_time", "side", "score", "gross", "bars", "exit", "sl_pct"])
        cd["entry_time"] = cd["signal_time"] + pd.Timedelta(hours=BH * (1 + delay))
        cd["exit_time"] = cd["entry_time"] + pd.to_timedelta(cd["bars"] * BH, unit="h")
        cd["net"] = cd["gross"] - 2 * (SIDE_COST + slip) - FUNDING_8H * cd["bars"] * BH / 8
        if end is not None:
            cd = cd[cd["exit_time"] <= end]
        return cd.sort_values(["entry_time", "score"], ascending=[True, False]).reset_index(drop=True)


def run_pf(cd, start, end):
    tr, cv = simulate(cd, **PF)
    st, eq = curve_stats(cv, start, end, tr)
    return st, tr, eq


def stats_series(r, lags):
    r = np.asarray(r)
    m = r.mean()
    xc = r - m
    n = len(r)
    v = xc @ xc / n
    lr = v
    for k in range(1, lags + 1):
        lr += 2 * (1 - k / (lags + 1)) * (xc[k:] @ xc[:-k]) / n
    return {"n": n, "mean": m, "t_iid": m / np.sqrt(v / n), "t_nw": m / np.sqrt(lr / n), "n_eff": n * v / lr}


def block_boot(r, block, nb=5000, seed=0):
    r = np.asarray(r)
    n = len(r)
    rng = np.random.default_rng(seed)
    k = int(np.ceil(n / block))
    out = np.empty(nb)
    for i in range(nb):
        st = rng.integers(0, n - block + 1, k)
        out[i] = np.concatenate([r[s:s + block] for s in st])[:n].mean()
    return out


def main():
    pq, pp, start, end, out = sys.argv[1:6]
    nsims = int(sys.argv[6]) if len(sys.argv) > 6 else 1000
    start, end = _utc(start), _utc(end)
    data = {s: d[d.index < end] for s, d in load(pq, "2h").items()}
    pr = pd.read_parquet(pp)
    pr = pr[(pr.index >= start) & (pr.index < end)]
    book = Book(data)
    E = edges(pr)
    sig = E[E["score"] > THR]
    R = {}

    # ---------------------------------------------------------------- A: original
    cd0 = book.cands(sig, end=end)
    st0, tr0, eq0 = run_pf(cd0, start, end)
    R["original"] = st0

    # ---------------------------------------------------------------- dependência temporal
    dr = eq0.pct_change().dropna()
    wk = eq0.resample("W").last().pct_change().dropna()
    R["diario"] = stats_series(dr.values, 10)
    R["semanal"] = stats_series(wk.values, 4)
    for b in [5, 10, 20]:
        bs = block_boot(dr.values, b)
        R[f"block_boot_diario_{b}d"] = {"P(media<=0)": float((bs <= 0).mean()),
                                        "IC95_retorno_anual": [float(np.quantile(bs, .025) * 365), float(np.quantile(bs, .975) * 365)]}
    # trades agrupados por semana de entrada (cluster)
    tr0["wk"] = tr0["entry_time"].dt.to_period("W").astype(str)
    g = tr0.groupby("wk")["net"].sum().values
    R["trades_cluster_semana"] = stats_series(g, 2)

    # ---------------------------------------------------------------- remoção de ativos (sem retreino)
    abl = {}
    for drop in [["AVAXUSDT"], ["LINKUSDT"], ["SOLUSDT"], ["AVAXUSDT", "LINKUSDT", "SOLUSDT"]]:
        st, _, _ = run_pf(cd0[~cd0["symbol"].isin(drop)].reset_index(drop=True), start, end)
        abl["sem " + "+".join(x[:-4] for x in drop)] = st
    R["ablacao_ativos"] = abl
    R["por_ativo"] = tr0.groupby("symbol")["net"].agg(
        trades="size", bps=lambda x: x.mean() * 1e4, win=lambda x: (x > 0).mean(),
        pf=lambda x: x[x > 0].sum() / -x[x < 0].sum()).round(3).to_dict(orient="index")
    R["por_lado"] = tr0.groupby("side")["net"].agg(trades="size", bps=lambda x: x.mean() * 1e4).round(2).to_dict(orient="index")

    # ---------------------------------------------------------------- remoção dos melhores trades
    top = {}
    for q in [0.01, 0.02, 0.05]:
        k = int(np.ceil(len(tr0) * q))
        best = tr0.nlargest(k, "net")[["symbol", "signal_time"]]
        key = set(zip(best["symbol"], best["signal_time"]))
        cd = cd0.copy()
        mask = [(a, b) in key for a, b in zip(cd["symbol"], cd["signal_time"])]
        cd.loc[mask, "net"] = 0.0
        st, _, _ = run_pf(cd, start, end)
        top[f"sem top {int(q*100)}% ({k} trades)"] = st
    R["sem_melhores_trades"] = top
    R["mes_a_mes"] = tr0.assign(m=tr0["exit_time"].dt.strftime("%Y-%m")).groupby("m")["net"].agg(
        trades="size", bps=lambda x: x.mean() * 1e4, soma_pct=lambda x: x.sum() * 100).round(1).to_dict(orient="index")
    nov = tr0[tr0["exit_time"].dt.strftime("%Y-%m") == "2025-11"]
    R["nov25_detalhe"] = {"trades": len(nov), "top5_bps": (nov["net"].nlargest(5) * 1e4).round(0).tolist(),
                          "bps_sem_top5": float(nov["net"].nsmallest(len(nov) - 5).mean() * 1e4),
                          "por_ativo_bps": (nov.groupby("symbol")["net"].sum() * 1e4).round(0).to_dict()}

    # ---------------------------------------------------------------- estresse de execução
    ex = {}
    for slip in [0.0006, 0.0010]:
        st, _, _ = run_pf(book.cands(sig, slip=slip, end=end), start, end)
        ex[f"slippage {round(slip*1e4)} bps/lado"] = st
    st, _, _ = run_pf(book.cands(sig, delay=1, end=end), start, end)
    ex["entrada atrasada 1 vela (2h)"] = st
    st, _, _ = run_pf(book.cands(sig, delay=1, slip=0.0006, end=end), start, end)
    ex["atraso 1 vela + slippage 6 bps"] = st
    R["estresse_execucao"] = ex

    # ---------------------------------------------------------------- controles pareados
    rng = np.random.default_rng(12345)
    syms = sorted(data)
    avail = {s: set(data[s].index) for s in syms}
    ctrl = {}
    for name in (["B_lado_aleatorio", "C_ativo_aleatorio", "D_score_embaralhado"] if nsims > 0 else []):
        rets, bps = [], []
        for _ in range(nsims):
            if name == "B_lado_aleatorio":
                sg = sig.assign(side=rng.choice([-1, 1], len(sig)))
            elif name == "C_ativo_aleatorio":
                new = [rng.choice([s for s in syms if t in avail[s]]) for t in sig["ts"]]
                sg = sig.assign(symbol=new).drop_duplicates(["symbol", "ts"])
            else:
                perm = rng.permutation(len(E))
                sg = E.assign(side=E["side"].values[perm], score=E["score"].values[perm])
                sg = sg[sg["score"] > THR]
            st, tr, _ = run_pf(book.cands(sg, end=end), start, end)
            rets.append(st["total_ret"])
            bps.append(st.get("avg_net_bps", np.nan))
        rets = np.array(rets)
        ctrl[name] = {"sims": nsims, "ret_medio": float(rets.mean()), "ret_p95": float(np.quantile(rets, .95)),
                      "ret_max": float(rets.max()), "bps_medio": float(np.nanmean(bps)),
                      "percentil_do_modelo": float((rets < st0["total_ret"]).mean())}
        print(name, ctrl[name], flush=True)
    R["controles_pareados"] = ctrl

    # ---------------------------------------------------------------- baselines simples, mesma carteira
    base = {}
    rate = len(sig) / len(E)
    for name in ["momentum_1sem", "reversao_1d"]:
        rows = []
        for s, df in data.items():
            c = df["close"]
            lr = np.log(c).diff()
            vol = lr.rolling(48).std()
            k = 84 if name == "momentum_1sem" else 12
            z = np.log(c / c.shift(k)) / (vol * np.sqrt(k))
            side = np.sign(z) if name == "momentum_1sem" else -np.sign(z)
            rows.append(pd.DataFrame({"symbol": s, "ts": df.index, "side": side.values, "score": z.abs().values}))
        A = pd.concat(rows).dropna()
        cut = A.loc[A["ts"] < start, "score"].quantile(1 - rate)  # limiar só com dados anteriores ao teste
        sg = A[(A["ts"] >= start) & (A["ts"] < end) & (A["score"] > cut) & (A["side"] != 0)]
        st, _, _ = run_pf(book.cands(sg.astype({"side": int}), end=end), start, end)
        base[name] = st
    R["baselines_simples"] = base

    # ---------------------------------------------------------------- calibração e monotonicidade
    labs = []
    for s, df in data.items():
        o, h, l, c, ap, idx = book.d[s]
        for side, nm in [(1, "long"), (-1, "short")]:
            r, b, _ = label_all(o, h, l, c, ap, side, TP, SL, H)
            net = r - 2 * (SIDE_COST + 0.0003) - FUNDING_8H * b * BH / 8
            labs.append(pd.DataFrame({"symbol": s, "ts": idx, "side_lab": nm, "net": net}))
    L = pd.concat(labs).pivot_table(index=["symbol", "ts"], columns="side_lab", values="net").reset_index()
    M = pr.reset_index().rename(columns={"index": "ts"}).merge(L, on=["symbol", "ts"]).dropna(subset=["long", "short"])
    cal = {}
    for sd in ["long", "short"]:
        y = (M[sd] > 0).astype(float)
        p = M[f"p_{sd}"].clip(1e-6, 1 - 1e-6)
        b0 = M[f"base_{sd}"]
        cal[sd] = {"brier_modelo": float(((p - y) ** 2).mean()), "brier_taxa_base": float(((b0 - y) ** 2).mean()),
                   "logloss_modelo": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
                   "logloss_taxa_base": float(-(y * np.log(b0) + (1 - y) * np.log(1 - b0)).mean())}
        bins = pd.cut(p, [0, .2, .3, .4, .5, .6, .7, 1])
        cal[sd]["curva"] = M.assign(y=y, b=bins).groupby("b", observed=True).agg(
            n=("y", "size"), p_prevista=(f"p_{sd}", "mean"), freq_real=("y", "mean")).round(3).reset_index().astype(str).to_dict(orient="records")
    R["calibracao"] = cal
    M["edge"] = np.maximum(M["p_long"] - M["base_long"], M["p_short"] - M["base_short"])
    M["realizado"] = np.where(M["p_long"] - M["base_long"] >= M["p_short"] - M["base_short"], M["long"], M["short"])
    M["dec"] = pd.qcut(M["edge"], 10, labels=False)
    dec = M.groupby("dec").agg(n=("edge", "size"), edge_medio=("edge", "mean"), bps_liq=("realizado", lambda x: x.mean() * 1e4),
                               acerto=("realizado", lambda x: (x > 0).mean()))
    R["decis_edge"] = dec.round(3).to_dict(orient="index")
    R["spearman_decil"] = float(dec["bps_liq"].rank().corr(pd.Series(range(10), index=dec.index).rank()))
    bands = pd.cut(M["edge"], [-1, 0, .04, .08, .12, .16, .2, 1])
    R["faixas_edge"] = M.groupby(bands, observed=True).agg(n=("edge", "size"), bps_liq=("realizado", lambda x: x.mean() * 1e4),
                                                           acerto=("realizado", lambda x: (x > 0).mean())).round(2).reset_index().astype(str).to_dict(orient="records")

    json.dump(R, open(f"{out}/audit.json", "w"), indent=1, default=str)
    print(json.dumps(R, indent=1, default=str))


if __name__ == "__main__":
    main()
