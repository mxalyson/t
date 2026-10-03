"""Relatório de avaliação do paper trading (protocolo do VEREDITO.md).

Uso:
    python paper/report.py --out paper_v1                  # baixa as velas 2h do perp Bybit do período
    python paper/report.py --out DIR --pq PQ_DIR           # usa parquets locais (replay/teste)
Gera DIR/report.md e DIR/report.json. Este relatório NÃO aprova capital real automaticamente.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.chdir(ROOT)

from audit import THR, Book, block_boot, run_pf, stats_series  # noqa: E402
from lib import SYMBOLS, load, simulate_barrier, FUNDING_8H  # noqa: E402

ASSUMED_SLIP_BPS = 3.0


def pct(x):
    return f"{x * 100:+.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pq", help="parquets 2h locais (senão baixa o perp da Bybit)")
    ap.add_argument("--sims", type=int, default=1000)
    a = ap.parse_args()
    D = a.out
    tr = pd.read_csv(os.path.join(D, "trades.csv"), parse_dates=["entry_time", "exit_time", "signal_time"])
    sg = pd.read_csv(os.path.join(D, "signals.csv"), parse_dates=["bar_time"])
    eq = pd.read_csv(os.path.join(D, "equity.csv"), parse_dates=["time"]).set_index("time")
    st = json.load(open(os.path.join(D, "state.json")))
    start, end = sg["bar_time"].min(), sg["bar_time"].max() + pd.Timedelta(hours=2)
    R = {"periodo": [str(start), str(end)], "dias": (end - start).days, "trades": len(tr),
         "versao": st["version"], "frozen_hash": st["frozen_hash"]}

    # ------------------------------------------------ expectativa e estatística robusta
    tr["sl_pct"] = 1.5 * tr["atr_pct"]
    tr["R"] = tr["net_pnl"] / (tr["notional"] * tr["sl_pct"])
    r = tr["net_ret_on_notional"].values
    R["expectativa"] = {"bps_por_trade": float(r.mean() * 1e4), "R_por_trade": float(tr["R"].mean()),
                        "win_rate": float((r > 0).mean()),
                        "profit_factor": float(tr.loc[tr.net_pnl > 0, "net_pnl"].sum() / -tr.loc[tr.net_pnl < 0, "net_pnl"].sum())
                        if (tr.net_pnl < 0).any() else None}
    e = eq["equity_mtm"].resample("D").last().ffill()
    dr = e.pct_change().dropna()
    wk = e.resample("W").last().pct_change().dropna()
    R["retorno_total"] = float(e.iloc[-1] / st["equity0"] - 1)
    R["max_dd_mtm"] = float((e / e.cummax() - 1).min())
    R["sharpe_diario"] = float(dr.mean() / dr.std() * np.sqrt(365)) if dr.std() > 0 else None
    if len(dr) > 30:
        R["diario_nw"] = stats_series(dr.values, 10)
        bs = block_boot(dr.values, 10)
        R["block_boot_10d"] = {"P(media<=0)": float((bs <= 0).mean()),
                               "IC95_anual": [float(np.quantile(bs, .025) * 365), float(np.quantile(bs, .975) * 365)]}
    if len(wk) > 8:
        R["semanal_nw"] = stats_series(wk.values, 4)

    # ------------------------------------------------ custos observados
    R["custos"] = {"slip_entrada_vs_mid_bps": float(tr["entry_slip_vs_mid_bps"].mean()),
                   "spread_medio_bps": float(tr["entry_spread_bps"].mean()),
                   "entrada_vs_close_spot_bps": float(tr["entry_vs_spot_close_bps"].mean()),
                   "slip_assumido_backtest_bps": ASSUMED_SLIP_BPS,
                   "taxas_bps_notional": float((tr["fees"] / tr["notional"]).mean() * 1e4),
                   "funding_bps_notional": float((tr["funding_pnl"] / tr["notional"]).mean() * 1e4),
                   "funding_backtest_bps": float((FUNDING_8H * tr["bars_held"] * 2 / 8).mean() * 1e4)}

    # ------------------------------------------------ concentração
    by = tr.groupby("symbol")["net_pnl"].sum()
    tot = tr["net_pnl"].sum()
    pos = by[by > 0].sum()
    R["concentracao"] = {"pnl_por_ativo": by.round(2).to_dict(),
                         "maior_fatia_do_lucro_bruto_positivo": float(by.max() / pos) if pos > 0 else None,
                         "ativo_maior_fatia": by.idxmax() if len(by) else None}
    for q in [0.01, 0.02, 0.05]:
        k = max(1, int(np.ceil(len(tr) * q)))
        R["concentracao"][f"pnl_sem_top_{int(q*100)}pct"] = float(tot - tr["net_pnl"].nlargest(k).sum())
    R["por_lado"] = tr.groupby("side")["net_ret_on_notional"].agg(["size", "mean"]).assign(mean=lambda x: x["mean"] * 1e4).round(2).to_dict(orient="index")
    R["por_saida"] = tr.groupby("exit_reason")["net_ret_on_notional"].agg(["size", "mean"]).assign(mean=lambda x: x["mean"] * 1e4).round(2).to_dict(orient="index")
    R["por_mes"] = tr.groupby(tr["exit_time"].dt.strftime("%Y-%m"))["net_pnl"].agg(["size", "sum"]).round(2).to_dict(orient="index")

    # ------------------------------------------------ controles pareados e previsto × realizado (perp 2h)
    if a.pq:
        data = {s: d[(d.index >= start - pd.Timedelta(days=30)) & (d.index < end + pd.Timedelta(days=10))]
                for s, d in load(a.pq, "2h").items()}
    else:
        from feeds import LiveFeed
        f = LiveFeed()
        data = {s: f.exec_klines_2h(s, start - pd.Timedelta(days=30), end + pd.Timedelta(days=10)) for s in SYMBOLS}
    book = Book(data)
    E = sg.rename(columns={"bar_time": "ts"})[["symbol", "ts", "side", "score"]]
    sig = E[E["score"] > THR]
    stA, trA, _ = run_pf(book.cands(sig, end=end), start, end)
    rng = np.random.default_rng(2026)
    ctrl = {}
    for name in ["B_lado_aleatorio", "D_score_embaralhado"]:
        rets = []
        for _ in range(a.sims):
            if name.startswith("B"):
                s2 = sig.assign(side=rng.choice([-1, 1], len(sig)))
            else:
                perm = rng.permutation(len(E))
                s2 = E.assign(side=E["side"].values[perm], score=E["score"].values[perm])
                s2 = s2[s2["score"] > THR]
            rets.append(run_pf(book.cands(s2, end=end), start, end)[0]["total_ret"])
        rets = np.array(rets)
        ctrl[name] = {"sims": a.sims, "ret_medio": float(rets.mean()), "ret_p95": float(np.quantile(rets, .95)),
                      "percentil_modelo_simulado": float((rets < stA["total_ret"]).mean()),
                      "percentil_paper_realizado": float((rets < R["retorno_total"]).mean())}
    R["controles_pareados"] = ctrl
    R["modelo_simulado_no_perp"] = {"total_ret": stA["total_ret"], "trades": stA.get("trades"), "bps": stA.get("avg_net_bps")}

    rows = []
    for s, g in E.groupby("symbol"):
        o, h, l, c, ap_, idx = book.d[s]
        pos_ = idx.get_indexer(g["ts"])
        for t, sd, sc in zip(pos_, g["side"].values, g["score"].values):
            if t < 0 or np.isnan(ap_[t]):
                continue
            rr, b, _ = simulate_barrier(o, h, l, c, t, int(sd), 3.0 * ap_[t], 1.5 * ap_[t], 48)
            if not np.isnan(rr):
                rows.append((sc, rr - 2 * (0.00055 + 0.0003) - FUNDING_8H * b * 2 / 8))
    M = pd.DataFrame(rows, columns=["score", "net"])
    if len(M) >= 100:
        M["dec"] = pd.qcut(M["score"], 10, labels=False, duplicates="drop")
        dec = M.groupby("dec")["net"].mean() * 1e4
        R["decis_score_bps"] = dec.round(1).to_dict()
        R["spearman_decil"] = float(dec.rank().corr(pd.Series(range(len(dec)), index=dec.index).rank()))

    # ------------------------------------------------ critérios do protocolo (não aprovam sozinhos)
    def chk(ok, pend=False):
        return "PENDENTE" if pend else ("OK" if ok else "FALHOU")
    nw = R.get("semanal_nw", {})
    crit = {
        "duracao >= 6 meses": chk(R["dias"] >= 182),
        "trades >= 200": chk(len(tr) >= 200),
        "expectativa liquida > 0": chk(R["expectativa"]["bps_por_trade"] > 0),
        "t Newey-West semanal > 2 (evidencia forte)": chk(nw.get("t_nw", 0) > 2, pend=not nw),
        "acima do controle B (p95)": chk(ctrl["B_lado_aleatorio"]["percentil_paper_realizado"] >= 0.95),
        "acima do controle D (p95)": chk(ctrl["D_score_embaralhado"]["percentil_paper_realizado"] >= 0.95),
        "slippage de entrada <= 3 bps": chk(R["custos"]["slip_entrada_vs_mid_bps"] <= ASSUMED_SLIP_BPS),
        "maior ativo <= 50% do lucro (avaliar com DD)": chk((R["concentracao"]["maior_fatia_do_lucro_bruto_positivo"] or 1) <= 0.5),
        "lucro positivo sem top 2% trades": chk(R["concentracao"]["pnl_sem_top_2pct"] > 0),
    }
    R["criterios"] = crit
    json.dump(R, open(os.path.join(D, "report.json"), "w"), indent=1, default=str)

    L = [f"# Relatório de paper trading — {R['versao']}", "",
         f"Período: {R['periodo'][0]} → {R['periodo'][1]} ({R['dias']} dias). Hash congelado: `{R['frozen_hash'][:12]}`.",
         "", "> Este relatório não aprova capital real automaticamente. Os critérios abaixo são necessários, não suficientes.", "",
         "## Resultado", "",
         "| Métrica | Valor |", "|---|---|",
         f"| Trades fechados | {len(tr)} |",
         f"| Retorno total (MTM) | {pct(R['retorno_total'])} |",
         f"| Max DD (MTM) | {pct(R['max_dd_mtm'])} |",
         f"| Sharpe diário anualizado | {R['sharpe_diario'] if R['sharpe_diario'] is None else round(R['sharpe_diario'], 2)} |",
         f"| Expectativa | {R['expectativa']['bps_por_trade']:+.1f} bps / trade, {R['expectativa']['R_por_trade']:+.3f} R |",
         f"| Win rate / PF | {R['expectativa']['win_rate']:.0%} / {R['expectativa']['profit_factor'] or float('nan'):.2f} |",
         f"| t Newey-West semanal | {nw.get('t_nw', 'n/d')} |",
         f"| Bootstrap blocos 10d P(média≤0) | {R.get('block_boot_10d', {}).get('P(media<=0)', 'n/d')} |", "",
         "## Custos observados", "", "| Item | Observado | Backtest |", "|---|---|---|",
         f"| Slippage de entrada vs mid | {R['custos']['slip_entrada_vs_mid_bps']:.2f} bps | 3 bps |",
         f"| Spread médio | {R['custos']['spread_medio_bps']:.2f} bps | — |",
         f"| Entrada vs close spot Binance | {R['custos']['entrada_vs_close_spot_bps']:.2f} bps | 0 |",
         f"| Funding | {R['custos']['funding_bps_notional']:+.2f} bps | −{R['custos']['funding_backtest_bps']:.2f} bps |",
         f"| Taxas | {R['custos']['taxas_bps_notional']:.2f} bps | 11 bps |", "",
         "## Controles pareados (mesmos sinais registrados, velas do perp)", "",
         "| Controle | Retorno médio | Percentil do paper |", "|---|---|---|"]
    for k, v in ctrl.items():
        L.append(f"| {k} | {pct(v['ret_medio'])} | {v['percentil_paper_realizado']:.1%} |")
    L += ["", f"Modelo simulado com os mesmos sinais no perp: {pct(stA['total_ret'])} (diferença para o paper = custo de execução real).", "",
          "## Critérios do protocolo", "", "| Critério | Status |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in crit.items()]
    L += ["", "## Concentração", "", f"Maior fatia do lucro: {R['concentracao']['ativo_maior_fatia']} "
          f"({(R['concentracao']['maior_fatia_do_lucro_bruto_positivo'] or 0):.0%}). "
          f"PnL sem top 2% trades: {R['concentracao']['pnl_sem_top_2pct']:+.2f}.", "",
          "Detalhes completos em `report.json`."]
    open(os.path.join(D, "report.md"), "w").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
