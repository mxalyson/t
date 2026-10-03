"""[OBSOLETO — use run_bot.py, veja GUIA_ROBO.md]

Executor ao vivo na Bybit (perp USDT linear). Rodar logo após o fechamento de cada vela
(ex.: 2h → cron "1 */2 * * *" UTC).

  * Sinal: mesmas features do backtest, calculadas com candles SPOT da Binance (mesma fonte do
    treino; a API pública não exige chave). Execução na Bybit.
  * Entrada a mercado; TP/SL definidos a partir do preço médio de execução (= backtest).
  * Saída por tempo após H velas; sizing por risco (perda no SL = risk × equity).
  * DRY-RUN por padrão. Para operar de verdade: --live e variáveis BYBIT_API_KEY / BYBIT_API_SECRET.
    Use --testnet para a testnet da Bybit.

Uso: python live.py config.json MODEL_DIR STATE.json [--live] [--testnet]
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests

from lib import SYMBOLS, atr, panel_features

BINANCE = "https://api.binance.com/api/v3/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def fetch(symbol, interval, limit=1000):
    r = requests.get(BINANCE, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=20)
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=COLS)
    now_ms = int(time.time() * 1000)
    df = df[df["close_time"] < now_ms]  # apenas velas FECHADAS
    df = df.drop(columns=["ignore", "close_time"]).apply(pd.to_numeric)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("ts").drop(columns="open_time")


def signals(cfg, model_dir):
    itv = cfg["interval"]
    bh = {"1h": 1, "2h": 2, "4h": 4}[itv]
    data = {s: fetch(s, itv) for s in SYMBOLS}
    X = panel_features(data, 24 // bh)
    last = X.index.max()
    X = X[X.index == last]
    meta = json.load(open(os.path.join(model_dir, f"{cfg['name']}_meta.json")))
    out = X[["symbol"]].copy()
    for side in ["long", "short"]:
        ms = [lgb.Booster(model_file=os.path.join(model_dir, f"{cfg['name']}_{side}_s{sd}.txt")) for sd in meta["seeds"]]
        out[f"edge_{side}"] = np.mean([m.predict(X[meta["features"]]) for m in ms], axis=0) - meta[f"base_{side}"]
    out["side"] = np.where(out["edge_long"] >= out["edge_short"], 1, -1)
    out["score"] = out[["edge_long", "edge_short"]].max(axis=1)
    out["atr_pct"] = [float((atr(data[s]) / data[s]["close"]).iloc[-1]) for s in out["symbol"]]
    out["bar_time"] = last
    return out.sort_values("score", ascending=False)


def round_step(x, step):
    return math.floor(x / step) * step


def main():
    cfg_path, model_dir, state_path = sys.argv[1:4]
    live, testnet = "--live" in sys.argv, "--testnet" in sys.argv
    cfg = json.load(open(cfg_path))["sleeves"][0]
    pf = json.load(open(cfg_path))["portfolio"]
    bh = {"1h": 1, "2h": 2, "4h": 4}[cfg["interval"]]
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"positions": {}, "log": []}
    now = datetime.now(timezone.utc)
    sig = signals(cfg, model_dir)
    print(sig.round(4).to_string())

    sess = None
    if live:
        from pybit.unified_trading import HTTP
        sess = HTTP(testnet=testnet, api_key=os.environ["BYBIT_API_KEY"], api_secret=os.environ["BYBIT_API_SECRET"])
        # sincroniza estado com posições reais (TP/SL podem ter fechado posições)
        pos = sess.get_positions(category="linear", settleCoin="USDT")["result"]["list"]
        open_syms = {p["symbol"] for p in pos if float(p["size"]) > 0}
        state["positions"] = {s: v for s, v in state["positions"].items() if s in open_syms}
        equity = float(sess.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]["totalEquity"])
    else:
        equity = state.get("paper_equity", 10000.0)

    # 1) saídas por tempo (H velas após a entrada)
    for s, p in list(state["positions"].items()):
        if now >= pd.Timestamp(p["entry_time"]) + pd.Timedelta(hours=bh * cfg["H"]):
            print("saida por tempo", s)
            if live:
                sess.place_order(category="linear", symbol=s, side="Sell" if p["side"] == 1 else "Buy",
                                 orderType="Market", qty=p["qty"], reduceOnly=True)
            state["positions"].pop(s)

    # 2) novas entradas respeitando limites da carteira
    for _, r in sig[sig["score"] > cfg["thr"]].iterrows():
        s, side = r["symbol"], int(r["side"])
        n_open = len(state["positions"])
        net = sum(p["side"] for p in state["positions"].values())
        if s in state["positions"] or n_open >= pf["max_pos"] or side * net >= pf["max_net"]:
            continue
        sl_pct, tp_pct = cfg["sl"] * r["atr_pct"], cfg["tp"] * r["atr_pct"]
        notional = min(pf["risk"] / sl_pct, pf["max_lev"]) * equity
        rec = {"side": side, "entry_time": str(now), "score": float(r["score"]), "notional": notional,
               "sl_pct": sl_pct, "tp_pct": tp_pct}
        if live:
            info = sess.get_instruments_info(category="linear", symbol=s)["result"]["list"][0]
            step = float(info["lotSizeFilter"]["qtyStep"])
            tick = float(info["priceFilter"]["tickSize"])
            px = float(sess.get_tickers(category="linear", symbol=s)["result"]["list"][0]["lastPrice"])
            qty = round_step(notional / px, step)
            if qty < float(info["lotSizeFilter"]["minOrderQty"]):
                continue
            sess.place_order(category="linear", symbol=s, side="Buy" if side == 1 else "Sell",
                             orderType="Market", qty=str(qty))
            time.sleep(1)
            p = [x for x in sess.get_positions(category="linear", symbol=s)["result"]["list"] if float(x["size"]) > 0][0]
            entry = float(p["avgPrice"])
            tp = round(round_step(entry * (1 + side * tp_pct), tick), 10)
            slp = round(round_step(entry * (1 - side * sl_pct), tick), 10)
            sess.set_trading_stop(category="linear", symbol=s, takeProfit=str(tp), stopLoss=str(slp),
                                  tpTriggerBy="LastPrice", slTriggerBy="LastPrice", tpslMode="Full", positionIdx=0)
            rec.update({"qty": str(qty), "entry": entry, "tp": tp, "sl": slp})
        print("ENTRADA", s, "LONG" if side == 1 else "SHORT", rec)
        state["positions"][s] = rec
        state["log"].append({"time": str(now), "symbol": s, **rec})
    json.dump(state, open(state_path, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
