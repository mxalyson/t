"""Dados públicos de mercado (sem chave): sinal na Binance spot, execução no perp da Bybit."""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "paper"))

from feeds import BYBIT, LiveFeed, _get  # noqa: E402


class Market(LiveFeed):
    """Herda do LiveFeed do paper: signal_klines, book, path (1 min), funding, exec_klines_2h."""

    def __init__(self, bybit_env="mainnet"):
        # demo usa preços da mainnet; testnet tem livro próprio
        self.base = "https://api-testnet.bybit.com/v5/market" if bybit_env == "testnet" else BYBIT
        self._inst = {}

    def book(self, symbol):
        j = _get(f"{self.base}/orderbook", {"category": "linear", "symbol": symbol, "limit": 1})["result"]
        return float(j["b"][0][0]), float(j["a"][0][0])

    def instrument(self, symbol):
        if symbol not in self._inst:
            j = _get(f"{self.base}/instruments-info", {"category": "linear", "symbol": symbol})["result"]["list"][0]
            self._inst[symbol] = {"tick": float(j["priceFilter"]["tickSize"]),
                                  "step": float(j["lotSizeFilter"]["qtyStep"]),
                                  "min_qty": float(j["lotSizeFilter"]["minOrderQty"]),
                                  "min_notional": float(j["lotSizeFilter"].get("minNotionalValue", 0) or 0)}
        return self._inst[symbol]

    def last_price(self, symbol):
        b, a = self.book(symbol)
        return (b + a) / 2

    @staticmethod
    def now():
        return pd.Timestamp.now(tz="UTC")
