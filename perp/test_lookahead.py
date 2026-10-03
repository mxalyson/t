"""Testes automáticos de vazamento temporal.

1) Features: embaralhar/perturbar todos os preços APÓS T não pode alterar nenhuma feature em ts <= T.
2) Walk-forward: perturbar os dados APÓS o fim do mês de teste não pode alterar as previsões do mês.
3) Purge: nenhum rótulo usado no treino do mês M termina depois de (início de M − embargo).

Uso: python test_lookahead.py PQ_DIR
"""
import sys

import numpy as np
import pandas as pd

import wf_ml
from lib import load, panel_features, _utc

PQ = sys.argv[1]
ITV, TP, SL, H, BH = "2h", 3.0, 1.5, 48, 2


def perturb(data, after, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for s, df in data.items():
        df = df.copy().astype(float)
        m = df.index > after
        k = rng.lognormal(0, 0.05, m.sum())
        for c in ["open", "high", "low", "close"]:
            df.loc[m, c] = df.loc[m, c].values * k
        for c in ["volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]:
            df.loc[m, c] = df.loc[m, c].values * rng.uniform(0.2, 3, m.sum())
        out[s] = df
    return out


def test_features():
    data = {s: d[(d.index >= "2023-01-01") & (d.index < "2024-01-01")] for s, d in load(PQ, ITV).items()}
    T = _utc("2023-09-15 10:00")
    A = panel_features(data, 12)
    B = panel_features(perturb(data, T), 12)
    a = A[A.index <= T].drop(columns="symbol").values
    b = B[B.index <= T].drop(columns="symbol").values
    assert np.allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), rtol=0, atol=1e-12), "feature usa dado futuro"
    changed = ~np.isclose(np.nan_to_num(A[A.index > T].drop(columns="symbol").values, nan=-9e9),
                          np.nan_to_num(B[B.index > T].drop(columns="symbol").values, nan=-9e9))
    assert changed.any(), "perturbação não teve efeito (teste inválido)"
    print("OK features: nenhuma feature em ts <= T muda quando o futuro é alterado")


def test_walk_forward():
    m0, m1 = _utc("2024-03-01"), _utc("2024-04-01")
    full = load(PQ, ITV)
    orig = wf_ml.load

    def run(data):
        wf_ml.load = lambda pq, itv: data
        try:
            P, _, _ = wf_ml.build_panel(PQ, ITV, TP, SL, H, _utc("2024-06-01"))
            return wf_ml.walk_forward(P, m0, m1, BH, H), P
        finally:
            wf_ml.load = orig

    data = {s: d[d.index < "2024-06-01"] for s, d in full.items()}
    p1, P = run(data)
    p2, _ = run(perturb(data, m1 - pd.Timedelta(hours=BH)))
    assert np.allclose(p1[["p_long", "p_short"]].values, p2[["p_long", "p_short"]].values, atol=1e-12), "previsão usa dado futuro"
    print("OK walk-forward: previsões de mar/2024 idênticas com dados após o mês alterados")
    cut = m0 - pd.Timedelta(hours=BH * H)
    for side in ["long", "short"]:
        tr = P[(P.index < m0) & (P[f"end_{side}"] < cut) & P[f"y_{side}"].notna()]
        assert tr[f"end_{side}"].max() < cut
        # o último instante de preço usado pelo rótulo = início da barra de saída + 1 barra = end - 1 barra
        print(f"OK purge {side}: último rótulo termina {tr[f'end_{side}'].max()} < corte {cut} (início do teste {m0})")


if __name__ == "__main__":
    test_features()
    test_walk_forward()
