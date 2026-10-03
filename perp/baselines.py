"""Baselines no período de DESENVOLVIMENTO (holdout final não é tocado aqui)."""
import sys
import numpy as np, pandas as pd
from lib import *

PQ = sys.argv[1]
HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
itv = sys.argv[2] if len(sys.argv) > 2 else "4h"
bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
data = {s: d[d.index < HOLDOUT] for s, d in load(PQ, itv).items()}
start = "2019-01-01" if itv != "1h" else "2022-03-01"
res = []
rng = np.random.default_rng(0)
for name in ["random", "donch20", "donch55", "tsmom", "meanrev_rsi"]:
    for tp, sl, H in [(2, 1, 24), (3, 1.5, 48), (4, 2, 96), (1.5, 1.5, 24)]:
        sigs = {}
        for s, df in data.items():
            c = df["close"]
            if name == "random":
                sg = pd.Series(rng.choice([-1, 0, 1], len(c), p=[.02, .96, .02]), index=c.index)
            elif name.startswith("donch"):
                n = int(name[5:])
                sg = (c > df["high"].shift(1).rolling(n).max()).astype(int) - (c < df["low"].shift(1).rolling(n).min()).astype(int)
            elif name == "tsmom":
                m = np.sign(c / c.shift(6 * 24 // bh * 0 + 42) - 1)  # ~1 semana em 4h
                sg = m.where(m != m.shift(1), 0).fillna(0).astype(int)
            elif name == "meanrev_rsi":
                r = ta = None
                from lib import _rsi
                r = _rsi(c)
                sg = (r < 25).astype(int) - (r > 75).astype(int)
            sigs[s] = sg[sg.index >= pd.Timestamp(start, tz="UTC")]
        tr = backtest(data, sigs, tp, sl, H, bh)
        st = portfolio_stats(tr, len(data), start, HOLDOUT, label=f"{name} tp{tp} sl{sl} H{H}")
        res.append(st)
pd.set_option("display.width", 250)
print(pd.DataFrame(res).round(3).to_string())
