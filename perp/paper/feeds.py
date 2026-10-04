"""Fontes de dados do paper trading.

LiveFeed   — APIs públicas (sem chave): candles SPOT 2h da Binance para o sinal (mesma fonte do
             treino) e livro, candles de 1 min e funding do PERP linear da Bybit para a execução.
ReplayFeed — reprodução offline a partir dos parquets locais, para testar a mecânica do motor.
"""
import time

import numpy as np
import pandas as pd
import requests

BINANCE = "https://api.binance.com/api/v3/klines"
BYBIT = "https://api.bybit.com/v5/market"
BCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


class ApiError(RuntimeError):
    pass


# códigos da Bybit que valem nova tentativa (limite de requisições, sobrecarga, timeout interno)
_RETRY_CODES = {10000, 10006, 10016, 10018, 10429}


def _get(url, params, tries=4):
    """GET com novas tentativas. Respostas da Bybit com retCode != 0 viram ApiError com a mensagem."""
    last = None
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            j = r.json()
        except Exception as e:  # noqa: BLE001
            last = e
        else:
            if not isinstance(j, dict) or j.get("retCode", 0) == 0:
                return j
            last = ApiError(f"Bybit {url.rsplit('/', 1)[-1]} {params.get('symbol', '')}: "
                            f"{j.get('retMsg')} (retCode {j.get('retCode')})")
            if j.get("retCode") not in _RETRY_CODES:
                raise last
        if k < tries - 1:
            time.sleep(2 ** (k + 1))
    raise last


def _ms(ts):
    return int(pd.Timestamp(ts).timestamp() * 1000)


class LiveFeed:
    interval = "2h"

    def now(self):
        return pd.Timestamp.now(tz="UTC")

    def signal_klines(self, symbol, until):
        """Velas 2h FECHADAS da Binance spot com abertura < until."""
        df = pd.DataFrame(_get(BINANCE, {"symbol": symbol, "interval": "2h", "limit": 1000}), columns=BCOLS)
        df = df[df["close_time"] < _ms(until)]
        df = df.drop(columns=["ignore", "close_time"]).apply(pd.to_numeric)
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df.set_index("ts").drop(columns="open_time")

    def book(self, symbol):
        j = _get(f"{BYBIT}/orderbook", {"category": "linear", "symbol": symbol, "limit": 1})["result"]
        return float(j["b"][0][0]), float(j["a"][0][0])

    def path(self, symbol, start, end):
        """Velas de 1 min do perp Bybit com abertura em [start, end)."""
        try:
            return self._path_forward(symbol, start, end)
        except ApiError:
            return self._path_backward(symbol, start, end)

    def _path_backward(self, symbol, start, end):
        """Alternativa: pede blocos terminando em `end` (só end + limit) até cobrir `start`."""
        rows, stop, first = [], _ms(end) - 1, _ms(start)
        while stop >= first:
            j = _get(f"{BYBIT}/kline", {"category": "linear", "symbol": symbol, "interval": "1",
                                        "end": stop, "limit": 1000})["result"]["list"]
            if not j:
                break
            rows += j
            oldest = min(int(x[0]) for x in j)
            if oldest <= first or len(j) < 1000:
                break
            stop = oldest - 1
        return self._frame(rows, start, end)

    @staticmethod
    def _frame(rows, start, end):
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "turnover"]).astype(float)
        df["ts"] = pd.to_datetime(df["t"].astype(np.int64), unit="ms", utc=True)
        df = df.drop_duplicates("ts").set_index("ts").sort_index()
        return df[(df.index >= pd.Timestamp(start)) & (df.index + pd.Timedelta(minutes=1) <= pd.Timestamp(end))]

    def _path_forward(self, symbol, start, end):
        rows, cur = [], _ms(start)
        stop = _ms(end)
        while cur < stop:
            j = _get(f"{BYBIT}/kline", {"category": "linear", "symbol": symbol, "interval": "1",
                                        "start": cur, "end": stop - 1, "limit": 1000})["result"]["list"]
            if not j:
                break
            j = sorted(j, key=lambda x: int(x[0]))
            rows += j
            nxt = int(j[-1][0]) + 60_000
            if nxt <= cur:
                break
            cur = nxt
        return self._frame(rows, start, end)

    def funding(self, symbol, start, end):
        """Eventos de funding (timestamp, taxa) do perp Bybit em (start, end]."""
        j = _get(f"{BYBIT}/funding/history", {"category": "linear", "symbol": symbol,
                                              "startTime": _ms(start), "endTime": _ms(end), "limit": 200})
        ev = [(pd.to_datetime(int(x["fundingRateTimestamp"]), unit="ms", utc=True), float(x["fundingRate"]))
              for x in j["result"]["list"]]
        return [(t, r) for t, r in ev if pd.Timestamp(start) < t <= pd.Timestamp(end)]

    def exec_klines_2h(self, symbol, start, end):
        """Velas 2h do perp Bybit (usadas no relatório para controles e previsto × realizado)."""
        rows, cur, stop = [], _ms(start), _ms(end)
        while cur < stop:
            j = _get(f"{BYBIT}/kline", {"category": "linear", "symbol": symbol, "interval": "120",
                                        "start": cur, "end": stop - 1, "limit": 1000})["result"]["list"]
            if not j:
                break
            j = sorted(j, key=lambda x: int(x[0]))
            rows += j
            nxt = int(j[-1][0]) + 7_200_000
            if nxt <= cur:
                break
            cur = nxt
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "turnover"]).astype(float)
        df["ts"] = pd.to_datetime(df["t"].astype(np.int64), unit="ms", utc=True)
        return df.drop_duplicates("ts").set_index("ts").drop(columns=["t"])


class ReplayFeed:
    """Reproduz o passado com os parquets locais. A execução usa as mesmas velas 2h.
    Livro = abertura da vela seguinte ± slippage; funding constante de 0,01% a cada 8h."""

    interval = "2h"

    def __init__(self, data, clock, slip=0.0003, funding_rate=0.0001):
        self.data, self.clock, self.slip, self.fr = data, clock, slip, funding_rate

    def now(self):
        return self.clock

    def signal_klines(self, symbol, until):
        d = self.data[symbol]
        return d[d.index + pd.Timedelta(hours=2) <= until].iloc[-1000:]

    def book(self, symbol):
        d = self.data[symbol]
        nxt = d[d.index >= self.clock.floor("2h")]
        px = nxt["open"].iloc[0] if len(nxt) else d["close"].iloc[-1]
        return px * (1 - self.slip), px * (1 + self.slip)

    def path(self, symbol, start, end):
        d = self.data[symbol]
        return d[(d.index >= start) & (d.index + pd.Timedelta(hours=2) <= end)][["open", "high", "low", "close"]]

    def funding(self, symbol, start, end):
        ts = pd.date_range(pd.Timestamp(start).ceil("8h"), end, freq="8h")
        return [(t, self.fr) for t in ts if pd.Timestamp(start) < t <= pd.Timestamp(end)]

    def exec_klines_2h(self, symbol, start, end):
        d = self.data[symbol]
        return d[(d.index >= start) & (d.index < end)]
