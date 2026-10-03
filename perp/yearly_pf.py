import sys, pandas as pd
from lib import load, _utc
from pf_eval import scores_from_preds
from portfolio import candidates, simulate, curve_stats
def yearly_pf(pq, itv, tp, sl, H, pr, start, end, thr, sim):
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    data = {s: d[d.index < _utc(end)] for s, d in load(pq, itv).items()}
    pr = pr[(pr.index >= _utc(start)) & (pr.index < _utc(end))]
    sc, sd = scores_from_preds(pr, thr)
    tr, cv = simulate(candidates(data, sc, sd, tp, sl, H, bh), **sim)
    rows = []
    for y in range(_utc(start).year, _utc(end).year + 1):
        a, b = max(_utc(f"{y}-01-01"), _utc(start)), min(_utc(f"{y+1}-01-01"), _utc(end))
        c = cv[(cv.index >= a) & (cv.index < b)]
        if c.empty: continue
        base = cv[cv.index < a].iloc[-1] if (cv.index < a).any() else 1.0
        t = tr[(tr.exit_time >= a) & (tr.exit_time < b)]
        st, _ = curve_stats(c / base, a, b - pd.Timedelta(days=1), t, label=str(y)); rows.append(st)
    return pd.DataFrame(rows), tr, cv
if __name__ == "__main__":
    pq, itv, tp, sl, H, pp, start, end, thr = sys.argv[1:10]
    df, _, _ = yearly_pf(pq, itv, float(tp), float(sl), int(H), pd.read_parquet(pp), start, end, float(thr),
                         dict(risk=0.005, max_pos=4, max_net=3, max_lev=2))
    pd.set_option("display.width", 300); print(df.round(3).to_string())
