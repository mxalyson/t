"""Treina os modelos de produção com TODOS os dados disponíveis (mesma regra do walk-forward).

Rodar 1x por mês (ex.: dia 1, 00:30 UTC), como no backtest.
Uso: python train_final.py PQ_DIR config.json MODEL_DIR
"""
import json
import os
import sys

import pandas as pd

from lib import load
from wf_ml import DROP, build_panel, fit_models, seeds


def train_sleeve(pq, sl_cfg, model_dir):
    itv, tp, sl, H = sl_cfg["interval"], sl_cfg["tp"], sl_cfg["sl"], sl_cfg["H"]
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    end = max(d.index.max() for d in load(pq, itv).values()) + pd.Timedelta(hours=bh)
    P, _, _ = build_panel(pq, itv, tp, sl, H, end)
    cut = end - pd.Timedelta(hours=bh * H)
    feats = [c for c in P.columns if c not in DROP]
    meta = {"features": feats, "trained_until": str(end), "seeds": seeds()}
    for side in ["long", "short"]:
        trs = P[(P[f"end_{side}"] < cut) & P[f"y_{side}"].notna()]
        for sd, mdl in zip(seeds(), fit_models(trs, feats, side)):
            mdl.save_model(os.path.join(model_dir, f"{sl_cfg['name']}_{side}_s{sd}.txt"))
        meta[f"base_{side}"] = float(trs[f"y_{side}"].mean())
    json.dump(meta, open(os.path.join(model_dir, f"{sl_cfg['name']}_meta.json"), "w"), indent=1)
    print(sl_cfg["name"], "ok", meta["trained_until"], len(P))


if __name__ == "__main__":
    pq, cfg_path, model_dir = sys.argv[1:4]
    os.makedirs(model_dir, exist_ok=True)
    cfg = json.load(open(cfg_path))
    for s in cfg["sleeves"]:
        train_sleeve(pq, s, model_dir)
