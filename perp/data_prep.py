"""Monta os datasets OHLCV em parquet a partir das fontes locais.

Fontes (as APIs das exchanges estão bloqueadas neste ambiente):
  - finom/static-klines (cache diário da API pública da Binance, spot) — 10 pares USDT, 1h/2h/4h
  - ff137/bitstamp-btcusd-minute-data — BTC/USD 1min desde 2012 (histórico longo do BTC)

Uso: python perp/data_prep.py <dir_static_klines> <dir_bitstamp> <dir_saida>
"""
import glob
import json
import os
import sys

import pandas as pd

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def load_static_klines(root, symbol, interval):
    rows = []
    for f in sorted(glob.glob(os.path.join(root, ".klines-cache", symbol, interval, "*.json"))):
        with open(f) as fh:
            rows.extend(json.load(fh))
    df = pd.DataFrame(rows, columns=COLS)
    if df.empty:
        return df
    df = df.drop(columns=["ignore", "close_time"])
    for c in df.columns:
        df[c] = pd.to_numeric(df[c])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").sort_values("ts").set_index("ts").drop(columns="open_time")
    return df


def load_bitstamp(root, rule):
    a = pd.read_csv(os.path.join(root, "btc_hist.csv.gz"))
    b = pd.read_csv(os.path.join(root, "btc_latest.csv"))
    df = pd.concat([a, b]).drop_duplicates("timestamp").sort_values("timestamp")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts").drop(columns="timestamp")
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna()


if __name__ == "__main__":
    sk, bs, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
    for itv in ["1h", "2h", "4h"]:
        for s in symbols:
            df = load_static_klines(sk, s, itv)
            df.to_parquet(os.path.join(out, f"{s}_{itv}.parquet"))
            print(itv, s, len(df), df.index.min(), df.index.max())
    for rule, name in [("1h", "1h"), ("4h", "4h")]:
        df = load_bitstamp(bs, rule)
        df = df[df.index >= "2014-01-01"]
        df.to_parquet(os.path.join(out, f"BTCUSD-BITSTAMP_{name}.parquet"))
        print("bitstamp", name, len(df), df.index.min(), df.index.max())
