"""Avalia previsões OOS com o simulador de carteira."""
import sys
import numpy as np, pandas as pd
from lib import load, _utc
from portfolio import candidates, simulate, curve_stats


def scores_from_preds(pr, thr, rule="edge", long_only=False):
    pl, ps = pr["p_long"].copy(), pr["p_short"].copy()
    if rule == "edge":
        pl, ps = pl - pr["base_long"], ps - pr["base_short"]
    if long_only:
        ps[:] = -1
    side = pd.Series(np.where(pl >= ps, 1, -1), index=pr.index)
    best = np.maximum(pl, ps)
    sc = pd.Series(np.where(best > thr, best, 0.0), index=pr.index)
    scores, sides = {}, {}
    for s in pr["symbol"].unique():
        m = (pr["symbol"] == s).values
        scores[s], sides[s] = sc[m], side[m]
    return scores, sides


def run(pq, itv, tp, sl, H, pr, start, end, thr, rule="edge", sims=None, long_only=False, label=""):
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    data = {s: d[d.index < _utc(end)] for s, d in load(pq, itv).items()}
    pr = pr[(pr.index >= _utc(start)) & (pr.index < _utc(end))]
    sc, sd = scores_from_preds(pr, thr, rule, long_only)
    cd = candidates(data, sc, sd, tp, sl, H, bh)
    res = []
    for kw in sims:
        tr, cv = simulate(cd, **kw)
        st, eq = curve_stats(cv, start, end, tr, label=f"{label} thr{thr} {kw}")
        res.append(st)
    return pd.DataFrame(res)


if __name__ == "__main__":
    pq, itv, tp, sl, H, pp, start, end = sys.argv[1:9]
    thrs = [float(x) for x in sys.argv[9].split(",")]
    pr = pd.read_parquet(pp)
    sims = [dict(risk=0.005, max_pos=10, max_net=10, max_lev=2), dict(risk=0.005, max_pos=4, max_net=3, max_lev=2),
            dict(risk=0.01, max_pos=4, max_net=3, max_lev=2), dict(risk=0.005, max_pos=6, max_net=2, max_lev=2)]
    pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 80)
    for t in thrs:
        print(run(pq, itv, float(tp), float(sl), int(H), pr, start, end, t, sims=sims, label=pp.split('/')[-1]).round(3).to_string())
