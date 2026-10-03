"""Motor de paper trading da v1-congelada.

Replica as regras do backtest (portfolio.simulate + lib.simulate_barrier) com execução observada:
  * Sinal no fechamento da vela 2h (candles spot da Binance, modelo congelado em models/).
  * Entrada a mercado no perp da Bybit: ask (long) ou bid (short) do topo do livro no momento.
  * TP/SL relativos ao preço de entrada; checagem com velas de 1 min do perp. TP e SL no mesmo
    minuto contam como SL. Gap na abertura além do nível sai pela abertura.
  * Saídas por TP/SL recebem o slippage assumido de 3 bps (gatilho a mercado, não observável em
    paper). Saída por tempo após 48 velas usa o livro real.
  * Taxa taker de 0,055% por lado e funding REAL da Bybit (long paga taxa positiva, short recebe).
  * Sizing pela equity realizada, como no backtest.

Todos os registros são CSVs com apenas acréscimos (append-only) em OUT_DIR:
  signals.csv  — todas as previsões de todos os ativos a cada vela, com a decisão e o motivo.
  trades.csv   — cada trade fechado, com preço esperado/efetivo, slippage, taxas, funding e PnL.
  equity.csv   — equity realizada e marcada a mercado a cada execução.
  runs.csv     — cada execução: horário, vela, hashes, avisos.
"""
import hashlib
import json
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from lib import SYMBOLS, atr, panel_features  # noqa: E402

VERSION = "v1-congelada"
FROZEN_FILES = ["config.json", "lib.py", "wf_ml.py", "train_final.py"]
# sha256 registrado na auditoria; alterar qualquer um desses arquivos exige uma NOVA versão
TAKER = 0.00055
ASSUMED_TRIGGER_SLIP = 0.0003
BAR = pd.Timedelta(hours=2)


def file_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        with open(os.path.join(ROOT, p), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def models_hash(model_dir):
    return file_hash([os.path.relpath(os.path.join(model_dir, f), ROOT) for f in sorted(os.listdir(model_dir))])


class ModelSignals:
    """Previsões do modelo congelado para a vela que fechou."""

    def __init__(self, model_dir, cfg):
        self.meta = json.load(open(os.path.join(model_dir, f"{cfg['name']}_meta.json")))
        self.models = {side: [lgb.Booster(model_file=os.path.join(model_dir, f"{cfg['name']}_{side}_s{sd}.txt"))
                              for sd in self.meta["seeds"]] for side in ["long", "short"]}

    def __call__(self, feed, bar_time):
        data = {s: feed.signal_klines(s, bar_time + BAR) for s in SYMBOLS}
        X = panel_features(data, 12)
        X = X[X.index == bar_time]
        out = X[["symbol"]].copy()
        for side in ["long", "short"]:
            out[f"edge_{side}"] = (np.mean([m.predict(X[self.meta["features"]]) for m in self.models[side]], axis=0)
                                   - self.meta[f"base_{side}"])
        out["atr_pct"] = [float((atr(data[s]) / data[s]["close"]).loc[bar_time]) for s in out["symbol"]]
        out["close_spot"] = [float(data[s]["close"].loc[bar_time]) for s in out["symbol"]]
        return out.reset_index(drop=True)


class PredsSignals:
    """Lê previsões OOS gravadas (só para o teste de consistência em replay)."""

    def __init__(self, preds, data):
        self.p, self.data = preds, data

    def __call__(self, feed, bar_time):
        p = self.p[self.p.index == bar_time]
        out = pd.DataFrame({"symbol": p["symbol"].values,
                            "edge_long": (p["p_long"] - p["base_long"]).values,
                            "edge_short": (p["p_short"] - p["base_short"]).values})
        out["atr_pct"] = [float((atr(self.data[s]) / self.data[s]["close"]).loc[bar_time]) for s in out["symbol"]]
        out["close_spot"] = [float(self.data[s]["close"].loc[bar_time]) for s in out["symbol"]]
        return out


def _append(path, rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(path, mode="a", header=not os.path.exists(path), index=False)


class Engine:
    def __init__(self, out_dir, cfg, signals, model_dir=None, equity0=10_000.0, check_frozen=True):
        self.out, self.cfg, self.signals = out_dir, cfg, signals
        self.sl_cfg = cfg["sleeves"][0]
        self.pf = cfg["portfolio"]
        os.makedirs(out_dir, exist_ok=True)
        self.state_path = os.path.join(out_dir, "state.json")
        frozen = file_hash(FROZEN_FILES)
        if os.path.exists(self.state_path):
            self.state = json.load(open(self.state_path))
        else:
            self.state = {"version": VERSION, "frozen_hash": frozen, "equity_realized": equity0,
                          "equity0": equity0, "positions": {}, "last_bar": None, "created": None}
        if check_frozen and self.state["frozen_hash"] != frozen:
            raise SystemExit(f"Arquivos congelados ({FROZEN_FILES}) mudaram desde o início do paper. "
                             "Isso é uma NOVA versão: use outro diretório de saída.")
        self.model_hash = models_hash(model_dir) if model_dir else "replay"

    # ------------------------------------------------------------------ saídas
    def _check_exit(self, p, bars):
        side, tp, sl = p["side"], p["tp"], p["sl"]
        entry_t = pd.Timestamp(p["entry_time"])
        for ts, b in bars.iterrows():
            if ts > entry_t:  # gaps (não existe gap na própria barra de entrada)
                if side == 1 and b.open <= sl or side == -1 and b.open >= sl:
                    return ts, b.open, "sl_gap"
                if side == 1 and b.open >= tp or side == -1 and b.open <= tp:
                    return ts, b.open, "tp_gap"
            if side == 1:
                if b.low <= sl:
                    return ts, sl, "sl"
                if b.high >= tp:
                    return ts, tp, "tp"
            else:
                if b.high >= sl:
                    return ts, sl, "sl"
                if b.low <= tp:
                    return ts, tp, "tp"
        return None

    def _close(self, feed, sym, p, exit_ts, price, reason, observed):
        side, qty, entry = p["side"], p["qty"], p["entry_price"]
        fill = price if observed else price * (1 - side * ASSUMED_TRIGGER_SLIP)
        gross = side * qty * (fill - entry)
        fee_exit = TAKER * qty * fill
        fund = sum(-side * r * qty * entry for _, r in feed.funding(sym, p["entry_time"], exit_ts))
        net = gross - p["fee_entry"] - fee_exit + fund
        self.state["equity_realized"] += net
        notional = qty * entry
        return {**{k: p[k] for k in ["symbol", "side", "signal_time", "entry_time", "score", "edge_long", "edge_short",
                                     "atr_pct", "expected_price", "entry_price", "entry_mid", "entry_spread_bps",
                                     "entry_slip_vs_mid_bps", "entry_vs_spot_close_bps", "tp", "sl", "qty"]},
                "notional": notional, "exit_time": str(exit_ts), "exit_reason": reason, "exit_price": fill,
                "exit_slip_observed": observed, "gross_pnl": gross, "fees": p["fee_entry"] + fee_exit,
                "funding_pnl": fund, "net_pnl": net, "net_ret_on_notional": net / notional,
                "bars_held": (pd.Timestamp(exit_ts) - pd.Timestamp(p["entry_time"])) / BAR,
                "equity_after": self.state["equity_realized"], "model_hash": p.get("model_hash")}

    # ------------------------------------------------------------------ passo principal
    def step(self, feed):
        now = feed.now()
        bar_close = now.floor("2h")
        bar_time = bar_close - BAR  # vela que acabou de fechar
        warn = []
        if self.state["last_bar"] and pd.Timestamp(self.state["last_bar"]) >= bar_time:
            return {"skip": "vela já processada"}
        if self.state["last_bar"] and bar_time - pd.Timestamp(self.state["last_bar"]) > BAR:
            warn.append(f"velas perdidas desde {self.state['last_bar']}")

        # 1) saídas (TP/SL pelo caminho de 1 min; tempo pelo livro)
        trades = []
        for sym, p in list(self.state["positions"].items()):
            t_exit = pd.Timestamp(p["time_exit_at"])
            end = min(bar_close, t_exit)
            bars = feed.path(sym, pd.Timestamp(p["last_checked"]), end)
            hit = self._check_exit(p, bars)
            if hit:
                ts, px, reason = hit
                trades.append(self._close(feed, sym, p, ts, px, reason, observed=False))
                del self.state["positions"][sym]
            elif bar_close >= t_exit:
                bid, ask = feed.book(sym)
                trades.append(self._close(feed, sym, p, now, bid if p["side"] == 1 else ask, "tempo", observed=True))
                del self.state["positions"][sym]
            else:
                p["last_checked"] = str(end)
        _append(os.path.join(self.out, "trades.csv"), trades)

        # 2) sinais e entradas (mesmas regras de portfolio.simulate)
        sig = self.signals(feed, bar_time)
        sig["side"] = np.where(sig["edge_long"] >= sig["edge_short"], 1, -1)
        sig["score"] = sig[["edge_long", "edge_short"]].max(axis=1)
        sig = sig.sort_values("score", ascending=False).reset_index(drop=True)
        rows = []
        for r in sig.itertuples(index=False):
            dec = "abaixo_limiar"
            if r.score > self.sl_cfg["thr"]:
                net = sum(p["side"] for p in self.state["positions"].values())
                if r.symbol in self.state["positions"]:
                    dec = "ja_posicionado"
                elif len(self.state["positions"]) >= self.pf["max_pos"]:
                    dec = "max_posicoes"
                elif r.side * net >= self.pf["max_net"]:
                    dec = "max_liquido"
                elif not np.isfinite(r.atr_pct) or r.atr_pct <= 0:
                    dec = "atr_invalido"
                else:
                    dec = "ENTRADA"
                    self._open(feed, r, bar_time, bar_close)
            rows.append({"bar_time": str(bar_time), "run_time": str(now), "symbol": r.symbol,
                         "edge_long": r.edge_long, "edge_short": r.edge_short, "side": int(r.side),
                         "score": r.score, "atr_pct": r.atr_pct, "close_spot": r.close_spot,
                         "decision": dec, "model_hash": self.model_hash})
        _append(os.path.join(self.out, "signals.csv"), rows)

        # 3) equity marcada a mercado
        unreal = 0.0
        for sym, p in self.state["positions"].items():
            bid, ask = feed.book(sym)
            unreal += p["side"] * p["qty"] * ((bid + ask) / 2 - p["entry_price"]) - p["fee_entry"]
        _append(os.path.join(self.out, "equity.csv"), [{
            "time": str(now), "bar_time": str(bar_time), "equity_realized": self.state["equity_realized"],
            "equity_mtm": self.state["equity_realized"] + unreal, "open_positions": len(self.state["positions"])}])
        _append(os.path.join(self.out, "runs.csv"), [{
            "run_time": str(now), "bar_time": str(bar_time), "version": VERSION,
            "frozen_hash": self.state["frozen_hash"], "model_hash": self.model_hash,
            "closed": len(trades), "entries": sum(r["decision"] == "ENTRADA" for r in rows), "warnings": "; ".join(warn)}])
        self.state["last_bar"] = str(bar_time)
        self.state["created"] = self.state["created"] or str(now)
        json.dump(self.state, open(self.state_path, "w"), indent=1, default=str)
        return {"bar_time": str(bar_time), "closed": len(trades), "open": len(self.state["positions"]), "warnings": warn}

    def _open(self, feed, r, bar_time, bar_close):
        side = int(r.side)
        bid, ask = feed.book(r.symbol)
        mid = (bid + ask) / 2
        fill = ask if side == 1 else bid
        sl_pct, tp_pct = self.sl_cfg["sl"] * r.atr_pct, self.sl_cfg["tp"] * r.atr_pct
        notional = min(self.pf["risk"] / sl_pct, self.pf["max_lev"]) * self.state["equity_realized"]
        qty = notional / fill
        self.state["positions"][r.symbol] = {
            "symbol": r.symbol, "side": side, "signal_time": str(bar_time), "entry_time": str(bar_close),
            "score": float(r.score), "edge_long": float(r.edge_long), "edge_short": float(r.edge_short),
            "atr_pct": float(r.atr_pct), "expected_price": float(r.close_spot), "entry_price": fill,
            "entry_mid": mid, "entry_spread_bps": (ask - bid) / mid * 1e4,
            "entry_slip_vs_mid_bps": side * (fill / mid - 1) * 1e4,
            "entry_vs_spot_close_bps": side * (fill / r.close_spot - 1) * 1e4,
            "tp": fill * (1 + side * tp_pct), "sl": fill * (1 - side * sl_pct), "qty": qty,
            "fee_entry": TAKER * qty * fill, "last_checked": str(bar_close),
            "time_exit_at": str(bar_close + self.sl_cfg["H"] * BAR), "model_hash": self.model_hash}
