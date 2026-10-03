"""Teste FINAL no holdout (out/2025 → hoje), rodado UMA vez com a configuração congelada.

O walk-forward continua igual: retreino mensal usando apenas dados anteriores a cada mês.
Uso: python final_test.py PQ_DIR config.json OUT_DIR
"""
import json
import os
import sys

import pandas as pd

from lib import load, _utc
from pf_eval import scores_from_preds
from portfolio import candidates, curve_stats, simulate

pq, cfg_path, out = sys.argv[1:4]
cfg = json.load(open(cfg_path))
for k, v in cfg.get("env", {}).items():
    os.environ[k] = str(v)
from wf_ml import build_panel, walk_forward  # noqa: E402  (env precisa estar definido antes)

itv, tp, sl, H = cfg["interval"], cfg["tp"], cfg["sl"], cfg["H"]
start = cfg["holdout_start"]
end = max(d.index.max() for d in load(pq, itv).values()) + pd.Timedelta(hours=1)
P, data, bh = build_panel(pq, itv, tp, sl, H, end)
pr = walk_forward(P, start, end, bh, H, train_years=cfg.get("train_years"))
pr.to_parquet(f"{out}/holdout_preds.parquet")

sc, sd = scores_from_preds(pr, cfg["thr"], cfg["rule"])
cd = candidates(data, sc, sd, tp, sl, H, bh)
cd = cd[cd["exit_time"] <= end]
tr, cv = simulate(cd, **cfg["portfolio"])
st, eq = curve_stats(cv, start, end, tr, label="HOLDOUT")
tr.to_csv(f"{out}/holdout_trades.csv", index=False)
eq.to_csv(f"{out}/holdout_equity.csv")
print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in st.items()}, indent=1))
tr["m"] = tr["exit_time"].dt.strftime("%Y-%m")
print(tr.groupby("m").agg(trades=("net", "size"), avg_net_bps=("net", lambda x: x.mean() * 1e4)).round(1).to_string())
print(tr.groupby("side")["net"].agg(["count", "mean"]))
