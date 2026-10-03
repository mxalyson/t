"""Teste offline do robô completo (sem rede, sem chaves): mercado sintético minuto a minuto.

Verifica nos perfis oficial (taker) e maker: entradas, TP/SL, saída por tempo, limites de carteira,
registros (trades/signals/equity) e que o PnL registrado bate com o saldo da corretora simulada.
Uso: python tests/test_bot_offline.py
"""
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.brokers import PaperBroker  # noqa: E402
from bot.settings import Settings, load_settings  # noqa: E402
from bot.trader import Trader  # noqa: E402
from lib import SYMBOLS  # noqa: E402

T0 = pd.Timestamp("2026-01-05 00:00", tz="UTC")


class FakeMarket:
    def __init__(self, seed=1, days=8):
        rng = np.random.default_rng(seed)
        idx = pd.date_range(T0 - pd.Timedelta(days=1), T0 + pd.Timedelta(days=days), freq="1min")
        self.px = {}
        for i, s in enumerate(SYMBOLS):
            r = rng.normal(0, 0.0012, len(idx))
            c = (100 + 10 * i) * np.exp(np.cumsum(r))
            o = np.r_[c[0], c[:-1]]
            w = np.abs(rng.normal(0, 0.0006, len(idx)))
            self.px[s] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + w), "low": np.minimum(o, c) * (1 - w),
                                       "close": c}, index=idx)
        self.t = T0

    def now(self):
        return self.t

    def _cur(self, s):
        d = self.px[s]
        return d["open"].iloc[d.index.get_indexer([self.t.floor("min")])[0]]

    def book(self, s):
        p = self._cur(s)
        return p * (1 - 0.0001), p * (1 + 0.0001)

    def last_price(self, s):
        return self._cur(s)

    def instrument(self, s):
        return {"tick": 0.001, "step": 0.001, "min_qty": 0.001, "min_notional": 5.0}

    def path(self, s, start, end):
        d = self.px[s]
        return d[(d.index >= start) & (d.index + pd.Timedelta(minutes=1) <= end)]

    def funding(self, s, start, end):
        ts = pd.date_range(pd.Timestamp(start).ceil("8h"), end, freq="8h")
        return [(t, 0.0001) for t in ts if pd.Timestamp(start) < t <= pd.Timestamp(end)]


class FakeSignals:
    """BTC long forte em toda vela; ETH short forte; resto abaixo do limiar."""

    def __init__(self, atr=0.004):
        self.atr = atr

    def __call__(self, feed, bar_time):
        rows = []
        for s in SYMBOLS:
            el, es = (0.2, -0.1) if s == "BTCUSDT" else (-0.1, 0.18) if s == "ETHUSDT" else (0.05, 0.0)
            rows.append({"symbol": s, "edge_long": el, "edge_short": es, "atr_pct": self.atr,
                         "close_spot": feed.last_price(s)})
        return pd.DataFrame(rows)


class Quiet:
    enabled = False

    def __init__(self):
        self.msgs = []

    def send(self, t, key=None, every=0):
        self.msgs.append(t)
        return True


def run(profile, hours=150, atr=0.004):
    tmp = tempfile.mkdtemp()
    s = load_settings(os.path.join(tmp, "nao_existe.env"))
    s.data_dir = tmp
    if profile.startswith("maker"):
        s.entry_mode, s.tp_order, s.exit_mode = "maker", "maker", "maker"
    s.signal_delay, s.reprice, s.maker_timeout = 60, 60, 600
    m = FakeMarket()
    tg = Quiet()
    tr = Trader(s, lambda st: PaperBroker(m, s, st), m, tg, signals=FakeSignals(atr), clock=m.now)
    tr.startup()
    end = T0 + pd.Timedelta(hours=hours)
    while m.t < end:
        tr.step()
        m.t += pd.Timedelta(seconds=60)
    trades = pd.read_csv(os.path.join(tmp, "trades.csv"))
    sig = pd.read_csv(os.path.join(tmp, "signals.csv"))
    cash = tr.broker.wallet()
    open_pos = tr.state["positions"]
    print(f"\n=== perfil {profile} ({tr.s.label}) ===")
    print(trades[["symbol", "side", "entry_time", "exit_time", "exit_reason", "entry_maker_frac", "net_pnl", "R"]]
          .to_string(max_rows=12))
    print("decisões:", sig["decision"].value_counts().to_dict())
    pnl = trades["net_pnl"].sum()
    # posições ainda abertas pagaram taxa de entrada e funding que ainda não estão no trades.csv
    open_fees = sum(p["entry_fee"] for p in open_pos.values())
    fund_open = sum(f["pnl"] for f in tr.broker.st["funding"]
                    if f["symbol"] in open_pos and pd.Timestamp(f["time"]) >= pd.Timestamp(open_pos[f["symbol"]]["entry_time"]))
    diff = (cash - s.capital) - (pnl - open_fees + fund_open)
    print(f"saldo {cash:.4f} | soma PnL trades {pnl:.4f} | taxas/funding de abertas {-open_fees + fund_open:.4f} | diferença {diff:.6f}")
    assert abs(diff) < 1e-6, "PnL registrado não bate com o saldo"
    assert len(trades) >= 3
    assert set(trades["symbol"]) <= {"BTCUSDT", "ETHUSDT"}
    assert (trades.query("symbol=='BTCUSDT'")["side"] == 1).all() and (trades.query("symbol=='ETHUSDT'")["side"] == -1).all()
    held = trades["bars_held"]
    assert held.max() <= 48.5, "posição passou do limite de 48 velas"
    if atr > 0.02:
        assert (trades["exit_reason"] == "tempo").any(), "saída por tempo não ocorreu"
        tt = trades[trades["exit_reason"] == "tempo"]
        dur = (pd.to_datetime(tt["exit_time"]) - pd.to_datetime(tt["signal_time"])) / pd.Timedelta(hours=2)
        print("velas entre a abertura da vela do sinal e a saída por tempo:", sorted(dur.round(2).unique()))
        assert ((dur >= 49) & (dur <= 49 + 0.1)).all(), "saída por tempo fora de 48 velas após o fechamento do sinal"
    if profile.startswith("maker"):
        assert trades["entry_maker_frac"].mean() > 0
    else:
        assert (trades["entry_maker_frac"] == 0).all()
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    if "tempo" not in sys.argv:
        run("oficial")
        run("maker")
    run("oficial_tempo", atr=0.05)
    run("maker_tempo", atr=0.05)
