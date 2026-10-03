"""Baixa o histórico completo (candles SPOT da Binance, mesma fonte do treino) para PQ_DIR.

Rodar na sua máquina antes do retreino mensal. Uso: python fetch_history.py PQ_DIR [intervalo=2h]
"""
import os
import sys
import time

import pandas as pd
import requests

from lib import SYMBOLS

URL = "https://api.binance.com/api/v3/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def fetch_all(symbol, interval, start_ms=1500000000000):
    rows, t = [], start_ms
    while True:
        r = requests.get(URL, params={"symbol": symbol, "interval": interval, "startTime": t, "limit": 1000}, timeout=30)
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        rows += k
        t = k[-1][0] + 1
        if len(k) < 1000:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=COLS)
    df = df[df["close_time"] < int(time.time() * 1000)]  # só velas fechadas
    df = df.drop(columns=["ignore", "close_time"]).apply(pd.to_numeric)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.drop_duplicates("ts").set_index("ts").drop(columns="open_time")


if __name__ == "__main__":
    out, itv = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "2h")
    os.makedirs(out, exist_ok=True)
    for s in SYMBOLS:
        df = fetch_all(s, itv)
        df.to_parquet(os.path.join(out, f"{s}_{itv}.parquet"))
        print(s, len(df), df.index.min(), df.index.max())
