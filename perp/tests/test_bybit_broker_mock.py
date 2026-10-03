"""Testa o BybitBroker contra respostas simuladas no formato da API v5 (sem rede).

Não substitui o teste real na conta Demo da Bybit: só verifica parâmetros enviados e leitura dos campos.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pybit.exceptions import InvalidRequestError  # noqa: E402

from bot.brokers import BybitBroker  # noqa: E402
from bot.settings import Settings  # noqa: E402


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def _r(self, result):
        return {"retCode": 0, "retMsg": "OK", "result": result}

    def switch_position_mode(self, **kw):
        self.calls.append(("switch_position_mode", kw))
        raise InvalidRequestError("x", "Position mode is not modified", 110025, "t", {})

    def set_leverage(self, **kw):
        self.calls.append(("set_leverage", kw))
        raise InvalidRequestError("x", "leverage not modified", 110043, "t", {})

    def get_wallet_balance(self, **kw):
        return self._r({"list": [{"totalEquity": "1020.5", "coin": [{"coin": "USDT", "walletBalance": "1000.25",
                                                                      "equity": "1010.75"}]}]})

    def get_orderbook(self, **kw):
        return self._r({"s": kw["symbol"], "b": [["64000.1", "2.1"]], "a": [["64000.2", "1.5"]]})

    def get_positions(self, **kw):
        if "settleCoin" in kw:
            return self._r({"list": [{"symbol": "BTCUSDT", "side": "Sell", "size": "0.010", "avgPrice": "64000"}]})
        return self._r({"list": [{"symbol": kw["symbol"], "side": "Sell", "size": "0.010", "avgPrice": "64000"}]})

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return self._r({"orderId": "abc123", "orderLinkId": kw.get("orderLinkId")})

    def get_open_orders(self, **kw):
        return self._r({"list": []})

    def get_order_history(self, **kw):
        return self._r({"list": [{"orderId": kw["orderId"], "orderStatus": "Filled", "cumExecQty": "0.010",
                                  "avgPrice": "64000.5", "cumExecFee": "0.352", "rejectReason": "EC_NoError"}]})

    def cancel_order(self, **kw):
        self.calls.append(("cancel_order", kw))
        raise InvalidRequestError("x", "order not exists or too late to cancel", 110001, "t", {})

    def set_trading_stop(self, **kw):
        self.calls.append(("set_trading_stop", kw))
        return self._r({})

    def get_executions(self, **kw):
        return self._r({"list": [
            {"execType": "Trade", "side": "Sell", "execQty": "0.010", "execPrice": "64000", "execFee": "0.35",
             "isMaker": False, "execTime": "1767225600000"},
            {"execType": "Trade", "side": "Buy", "execQty": "0.006", "execPrice": "63000", "execFee": "0.13",
             "isMaker": True, "execTime": "1767240000000"},
            {"execType": "Trade", "side": "Buy", "execQty": "0.004", "execPrice": "63100", "execFee": "0.09",
             "isMaker": True, "execTime": "1767240060000"},
            {"execType": "Funding", "side": "Sell", "execQty": "0.010", "execPrice": "63500", "execFee": "0.01",
             "isMaker": False, "execTime": "1767230000000"}]})

    def get_transaction_log(self, **kw):
        return self._r({"list": [{"symbol": "BTCUSDT", "type": "SETTLEMENT", "change": "0.064"},
                                 {"symbol": "ETHUSDT", "type": "SETTLEMENT", "change": "-0.5"}]})


class FakeMarket:
    def instrument(self, s):
        return {"tick": 0.1, "step": 0.001, "min_qty": 0.001, "min_notional": 5.0}


def main():
    s = Settings(mode="live", bybit_env="demo", api_key="k", api_secret="s")
    b = BybitBroker(s, FakeMarket())
    assert b.http.endpoint == "https://api-demo.bybit.com", b.http.endpoint
    b.http = FakeHTTP()
    assert b.setup(["BTCUSDT"], 5) == []
    assert b.wallet() == 1000.25 and b.equity_mtm() == 1010.75
    assert b.book("BTCUSDT") == (64000.1, 64000.2)
    assert b.position("BTCUSDT") == {"size": -0.010, "avg_price": 64000.0}
    assert b.all_positions() == {"BTCUSDT": -0.010}
    o = b.post_only("BTCUSDT", -1, 0.01, 64000.25, tag="E")
    kw = b.http.calls[-1][1]
    assert kw["timeInForce"] == "PostOnly" and kw["orderType"] == "Limit" and kw["side"] == "Sell"
    assert kw["qty"] == "0.010" and kw["price"] == "64000.2" and kw["positionIdx"] == 0 and kw["reduceOnly"] is False
    assert len(kw["orderLinkId"]) <= 36
    st = b.order("BTCUSDT", o["id"])
    assert st == {"status": "Filled", "filled": 0.01, "avg_price": 64000.5, "fee": 0.352, "reject": "EC_NoError"}
    b.cancel("BTCUSDT", "abc123")  # 110001 ignorado
    b.set_stop("BTCUSDT", -1, 65000.04)
    kw = b.http.calls[-1][1]
    assert kw["stopLoss"] == "65000.0" and kw["slOrderType"] == "Market" and kw["tpslMode"] == "Full"
    b.set_tp_market("BTCUSDT", 61000)
    assert b.http.calls[-1][1]["tpOrderType"] == "Market"
    m = b.market("BTCUSDT", 1, 0.01, reduce_only=True, tag="XT")
    assert m["status"] == "Filled" and b.http.calls[-1][1]["reduceOnly"] is True
    ci = b.close_info("BTCUSDT", "2026-01-01 00:00", -1)
    assert abs(ci["exit_qty"] - 0.01) < 1e-12 and abs(ci["exit_price"] - 63040) < 1e-6
    assert abs(ci["exit_fee"] - 0.22) < 1e-12 and ci["exit_maker"] is True and abs(ci["funding"] - 0.064) < 1e-12
    print("OK BybitBroker (mock): parâmetros e leitura de campos conferidos")


if __name__ == "__main__":
    main()
