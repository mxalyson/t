"""Walk-forward do modelo ML (LightGBM) com retreino mensal, purge e embargo.

Gera previsões OUT-OF-SAMPLE para cada mês de teste: o modelo do mês M só vê amostras cujo
rótulo (triple-barrier) terminou antes do início de M menos um embargo.

Uso: python wf_ml.py PQ_DIR ITV TP SL H TEST_START TEST_END OUT.parquet [train_years]
"""
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

from lib import (SYMBOLS, atr, label_all, load, month_starts, net_return, panel_features, _utc)

PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=400,
              feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=int(__import__("os").environ.get("NT", 4)), seed=7)
N_ROUNDS = 300
DROP = ["symbol", "y_long", "y_short", "r_long", "r_short", "end_long", "end_short", "t"]


def build_panel(pq, itv, tp, sl, H, end):
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    bpd = 24 // bh
    data = {s: d[d.index < end] for s, d in load(pq, itv).items()}
    X = panel_features(data, bpd)
    labs = []
    for s, df in data.items():
        ap = (atr(df) / df["close"]).values
        o, h, l, c = (df[k].values for k in ["open", "high", "low", "close"])
        out = pd.DataFrame(index=df.index)
        for side, nm in [(1, "long"), (-1, "short")]:
            r, b, _ = label_all(o, h, l, c, ap, side, tp, sl, H)
            out[f"r_{nm}"] = net_return(r, b, bh)
            # instante em que o rótulo fica conhecido (fim do trade) -> usado no purge
            out[f"end_{nm}"] = df.index + pd.to_timedelta((b + 1) * bh, unit="h")
        out["symbol"] = s
        labs.append(out.reset_index())
    L = pd.concat(labs)
    X = X.reset_index().rename(columns={"index": "ts"})
    P = X.merge(L, on=["ts", "symbol"], how="inner").set_index("ts")
    P["y_long"] = (P["r_long"] > 0).astype(float).where(P["r_long"].notna())
    P["y_short"] = (P["r_short"] > 0).astype(float).where(P["r_short"].notna())
    return P, data, bh


def walk_forward(P, test_start, test_end, bh, H, train_years=None, embargo_bars=None):
    embargo = pd.Timedelta(hours=bh * (embargo_bars if embargo_bars is not None else H))
    feats = [c for c in P.columns if c not in DROP]
    preds = []
    for m0 in month_starts(test_start, test_end):
        m1 = m0 + pd.offsets.MonthBegin(1)
        if m0 >= _utc(test_end):
            break
        cut = m0 - embargo
        tr = P[(P.index < m0)]
        if train_years:
            tr = tr[tr.index >= m0 - pd.DateOffset(years=train_years)]
        te = P[(P.index >= m0) & (P.index < m1)]
        if te.empty:
            continue
        out = te[["symbol"]].copy()
        for side in ["long", "short"]:
            # purge: apenas rótulos já conhecidos antes do corte
            trs = tr[(tr[f"end_{side}"] < cut) & tr[f"y_{side}"].notna()]
            ds = lgb.Dataset(trs[feats], trs[f"y_{side}"], categorical_feature=["sym"], free_raw_data=True)
            mdl = lgb.train(PARAMS, ds, N_ROUNDS)
            out[f"p_{side}"] = mdl.predict(te[feats])
            out[f"base_{side}"] = trs[f"y_{side}"].mean()
        preds.append(out)
        print(m0.date(), len(trs), f"{time.strftime('%H:%M:%S')}", flush=True)
    return pd.concat(preds)


if __name__ == "__main__":
    pq, itv, tp, sl, H, ts, te, outp = sys.argv[1:9]
    ty = float(sys.argv[9]) if len(sys.argv) > 9 else None
    tp, sl, H = float(tp), float(sl), int(H)
    P, data, bh = build_panel(pq, itv, tp, sl, H, _utc(te))
    print("panel", P.shape, flush=True)
    pr = walk_forward(P, ts, te, bh, H, train_years=ty)
    pr.to_parquet(outp)
