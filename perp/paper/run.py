"""Executa o paper trading da v1-congelada.

Ao vivo (cron a cada 2h, 1 minuto após o fechamento da vela, em UTC):
    1 */2 * * *  cd /caminho/perp && python paper/run.py live --out paper_v1

Retreino mensal (parte da especificação congelada, igual ao walk-forward):
    30 0 1 * *   cd /caminho/perp && python fetch_history.py pq 2h && SEEDS=7,11 python train_final.py pq config.json models

Replay offline (teste de mecânica com os dados locais):
    python paper/run.py replay --pq PQ_DIR --start 2026-07-01 --end 2026-10-01 --out /tmp/x [--preds PREDS.parquet]
"""
import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine import Engine, ModelSignals, PredsSignals  # noqa: E402
from feeds import LiveFeed, ReplayFeed  # noqa: E402

from lib import load  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["live", "replay"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=os.path.join(ROOT, "models"))
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--pq")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--preds")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config.json")))

    if a.mode == "live":
        eng = Engine(a.out, cfg, ModelSignals(a.models, cfg["sleeves"][0]), model_dir=a.models, equity0=a.equity)
        print(eng.step(LiveFeed()))
        return

    data = load(a.pq, "2h")
    end = pd.Timestamp(a.end, tz="UTC")
    data = {s: d[d.index < end + pd.Timedelta(days=10)] for s, d in data.items()}
    if a.preds:
        sig = PredsSignals(pd.read_parquet(a.preds), data)
        eng = Engine(a.out, cfg, sig, equity0=a.equity, check_frozen=False)
    else:
        eng = Engine(a.out, cfg, ModelSignals(a.models, cfg["sleeves"][0]), model_dir=a.models,
                     equity0=a.equity, check_frozen=False)
    for t in pd.date_range(pd.Timestamp(a.start, tz="UTC"), end, freq="2h"):
        feed = ReplayFeed(data, t + pd.Timedelta(minutes=1))
        eng.step(feed)
    print(json.dumps({k: v for k, v in eng.state.items() if k != "positions"}, indent=1, default=str))


if __name__ == "__main__":
    main()
