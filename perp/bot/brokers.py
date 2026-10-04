"""Corretoras com a mesma interface: PaperBroker (simulação) e BybitBroker (ordens reais).

Status de ordem no padrão da Bybit: New, PartiallyFilled, Filled, Cancelled, Rejected.
Lados: +1 = Buy, -1 = Sell.
"""
import itertools
import math
import time

import pandas as pd


def side_str(s):
    return "Buy" if s == 1 else "Sell"


def round_step(x, step):
    return math.floor(x / step + 1e-9) * step


def round_price(px, tick, mode="nearest"):
    f = {"down": math.floor, "up": math.ceil}.get(mode, round)
    return f(px / tick + (1e-9 if mode == "down" else -1e-9 if mode == "up" else 0)) * tick


def fmt(x, step):
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f"{x:.{dec}f}"


# ============================================================================ PAPER
class PaperBroker:
    """Simula a Bybit com dados reais (livro e velas de 1 min do perp).

    * PostOnly: rejeitada se cruzar o livro; executa (maker) só se o preço NEGOCIAR ALÉM do
      limite (low < preço para compra, high > preço para venda) — conservador quanto à fila.
    * Stop-market: dispara se a mínima/máxima tocar o gatilho; gap sai na abertura; +3 bps de
      slippage e taxa taker. Stop é processado antes de limite no mesmo minuto (pior caso).
    * Mercado: executa no bid/ask atual do livro real, com taxa taker.
    * Funding: taxa real da Bybit aplicada nos horários de funding.
    """

    name = "paper"
    STOP_SLIP = 0.0003

    def __init__(self, market, settings, state):
        self.m, self.s = market, settings
        st = state.setdefault("paper_broker", {})
        st.setdefault("cash", settings.capital)
        st.setdefault("orders", {})
        st.setdefault("positions", {})
        st.setdefault("fills", [])
        st.setdefault("funding", [])
        st.setdefault("last_tick", {})
        st.setdefault("seq", 0)
        self.st = st

    # ------------------------------------------------------------ util
    def _touch(self, sym):
        """Avança o relógio do símbolo para agora: nada antes desta ordem pode afetá-la."""
        now = str(self.m.now().floor("min"))
        last = self.st["last_tick"].get(sym)
        if last is None or pd.Timestamp(last) < pd.Timestamp(now):
            self.st["last_tick"][sym] = now

    def _oid(self):
        self.st["seq"] += 1
        return f"P{self.st['seq']}"

    def _pos(self, sym):
        return self.st["positions"].setdefault(sym, {"qty": 0.0, "avg": 0.0})

    def _fill(self, sym, side, qty, px, maker, ts, oid):
        fee = qty * px * (self.s.maker_fee if maker else self.s.taker_fee)
        p = self._pos(sym)
        q0 = p["qty"]
        q1 = q0 + side * qty
        if q0 == 0 or (q0 > 0) == (side > 0):  # abre/aumenta
            p["avg"] = (abs(q0) * p["avg"] + qty * px) / abs(q1)
        else:  # reduz/fecha
            closed = min(qty, abs(q0))
            self.st["cash"] += (px - p["avg"]) * closed * (1 if q0 > 0 else -1)
            if abs(q1) < 1e-12:
                q1, p["avg"] = 0.0, 0.0
        p["qty"] = q1
        self.st["cash"] -= fee
        self.st["fills"].append({"time": str(ts), "symbol": sym, "side": side, "qty": qty, "price": px,
                                 "fee": fee, "maker": maker, "order_id": oid})
        if p["qty"] == 0:  # posição zerada: TP/SL e ordens reduce-only somem (como na Bybit)
            for o in self.st["orders"].values():
                if o["symbol"] == sym and o["status"] in ("New", "PartiallyFilled") and o["reduce_only"]:
                    o["status"] = "Cancelled"
        return fee

    # ------------------------------------------------------------ interface
    def setup(self, symbols, leverage):
        return []

    def wallet(self):
        return self.st["cash"]

    def equity_mtm(self):
        u = 0.0
        for sym, p in self.st["positions"].items():
            if p["qty"]:
                u += p["qty"] * (self.m.last_price(sym) - p["avg"])
        return self.st["cash"] + u

    def book(self, sym):
        return self.m.book(sym)

    def instrument(self, sym):
        return self.m.instrument(sym)

    def position(self, sym):
        p = self._pos(sym)
        return {"size": p["qty"], "avg_price": p["avg"]}

    def post_only(self, sym, side, qty, price, reduce_only=False, tag=""):
        bid, ask = self.m.book(sym)
        oid = self._oid()
        crosses = price >= ask if side == 1 else price <= bid
        o = {"id": oid, "symbol": sym, "side": side, "qty": qty, "price": price, "type": "limit",
             "reduce_only": reduce_only, "status": "Rejected" if crosses else "New", "filled": 0.0,
             "avg_price": 0.0, "fee": 0.0, "maker": True, "created": str(self.m.now()), "tag": tag}
        if not self._busy(sym):
            self._touch(sym)
        self.st["orders"][oid] = o
        return {"id": oid, "status": o["status"]}

    def market(self, sym, side, qty, reduce_only=False, tag=""):
        if reduce_only:
            qty = min(qty, abs(self._pos(sym)["qty"]))
            if qty <= 0:
                return {"id": None, "status": "Rejected", "filled": 0.0, "avg_price": 0.0, "fee": 0.0}
        if not self._busy(sym):
            self._touch(sym)
        bid, ask = self.m.book(sym)
        px = ask if side == 1 else bid
        oid = self._oid()
        now = self.m.now()
        fee = self._fill(sym, side, qty, px, False, now, oid)
        self.st["orders"][oid] = {"id": oid, "symbol": sym, "side": side, "qty": qty, "price": px, "type": "market",
                                  "reduce_only": reduce_only, "status": "Filled", "filled": qty, "avg_price": px,
                                  "fee": fee, "maker": False, "created": str(now), "tag": tag}
        return {"id": oid, "status": "Filled", "filled": qty, "avg_price": px, "fee": fee}

    def _busy(self, sym):
        """Símbolo com posição ou ordem viva (o relógio dele está sendo acompanhado)."""
        return bool(self._pos(sym)["qty"]) or any(
            o["symbol"] == sym and o["status"] in ("New", "PartiallyFilled") for o in self.st["orders"].values())

    def order(self, sym, oid):
        o = self.st["orders"][oid]
        return {"status": o["status"], "filled": o["filled"], "avg_price": o["avg_price"], "fee": o["fee"]}

    def cancel(self, sym, oid):
        o = self.st["orders"].get(oid)
        if o and o["status"] in ("New", "PartiallyFilled"):
            o["status"] = "Cancelled"

    def set_stop(self, sym, pos_side, sl_price):
        for o in self.st["orders"].values():
            if o["symbol"] == sym and o["type"] == "stop" and o["status"] == "New":
                o["status"] = "Cancelled"
        oid = self._oid()
        self.st["orders"][oid] = {"id": oid, "symbol": sym, "side": -pos_side, "qty": None, "price": sl_price,
                                  "type": "stop", "reduce_only": True, "status": "New", "filled": 0.0,
                                  "avg_price": 0.0, "fee": 0.0, "maker": False, "created": str(self.m.now()), "tag": "sl"}
        return oid

    def set_tp_market(self, sym, tp_price):
        p = self._pos(sym)
        oid = self._oid()
        self.st["orders"][oid] = {"id": oid, "symbol": sym, "side": -1 if p["qty"] > 0 else 1, "qty": None,
                                  "price": tp_price, "type": "tpstop", "reduce_only": True, "status": "New",
                                  "filled": 0.0, "avg_price": 0.0, "fee": 0.0, "maker": False,
                                  "created": str(self.m.now()), "tag": "tp"}
        return oid

    def close_info(self, sym, since, pos_side):
        ex = [f for f in self.st["fills"] if f["symbol"] == sym and pd.Timestamp(f["time"]) >= pd.Timestamp(since)
              and f["side"] == -pos_side]
        qty = sum(f["qty"] for f in ex)
        fund = sum(f["pnl"] for f in self.st["funding"] if f["symbol"] == sym and pd.Timestamp(f["time"]) >= pd.Timestamp(since))
        return {"exit_qty": qty, "exit_price": sum(f["qty"] * f["price"] for f in ex) / qty if qty else float("nan"),
                "exit_fee": sum(f["fee"] for f in ex), "exit_maker": all(f["maker"] for f in ex) if ex else None,
                "funding": fund, "exit_time": ex[-1]["time"] if ex else None}

    # ------------------------------------------------------------ simulação no tempo
    def tick(self, now):
        """Processa os minutos desde a última checagem. Falha num símbolo não afeta os outros:
        o relógio dele não avança e o mesmo período é tentado de novo no próximo ciclo."""
        end = pd.Timestamp(now).floor("min")
        syms = {o["symbol"] for o in self.st["orders"].values() if o["status"] in ("New", "PartiallyFilled")}
        syms |= {s for s, p in self.st["positions"].items() if p["qty"]}
        errors = []
        for sym in sorted(syms):
            try:
                self._tick_symbol(sym, end)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sym}: {e}")
        if errors:
            raise RuntimeError("dados de 1 min indisponíveis (nova tentativa no próximo ciclo) — " + " | ".join(errors))

    def _tick_symbol(self, sym, end):
        start = pd.Timestamp(self.st["last_tick"].get(sym, str(end)))
        if end <= start:
            return
        bars = self.m.path(sym, start, end)
        if not len(bars):
            return  # minutos ainda não publicados: tenta de novo no próximo ciclo
        # avança só até a última vela recebida (nunca pula um minuto não publicado)
        end = min(end, bars.index[-1] + pd.Timedelta(minutes=1))
        fund = self.m.funding(sym, start, end) if self._pos(sym)["qty"] else []
        for ts, b in bars.iterrows():
            for ft, rate in [f for f in fund if start < f[0] <= ts]:
                p = self._pos(sym)
                if p["qty"]:
                    pnl = -p["qty"] * b.open * rate
                    self.st["cash"] += pnl
                    self.st["funding"].append({"time": str(ft), "symbol": sym, "rate": rate, "pnl": pnl})
            fund = [f for f in fund if f[0] > ts]
            self._process_bar(sym, ts, b)
        self.st["last_tick"][sym] = str(end)

    def _process_bar(self, sym, ts, b):
        live = [o for o in self.st["orders"].values() if o["symbol"] == sym and o["status"] in ("New", "PartiallyFilled")
                and pd.Timestamp(o["created"]) < ts + pd.Timedelta(minutes=1)]
        # 1) stops primeiro (pior caso)
        for o in [o for o in live if o["type"] == "stop"]:
            p = self._pos(sym)
            if not p["qty"] or o["status"] != "New":
                continue
            trig = o["price"]
            if o["side"] == -1 and (b.open <= trig or b.low <= trig):  # stop de long
                px = (b.open if b.open <= trig else trig) * (1 - self.STOP_SLIP)
            elif o["side"] == 1 and (b.open >= trig or b.high >= trig):  # stop de short
                px = (b.open if b.open >= trig else trig) * (1 + self.STOP_SLIP)
            else:
                continue
            q = abs(p["qty"])
            fee = self._fill(sym, o["side"], q, px, False, ts, o["id"])
            o.update(status="Filled", filled=q, avg_price=px, fee=fee)
        # 1b) take profit por gatilho a mercado (TP_ORDER=market)
        for o in [o for o in live if o["type"] == "tpstop" and o["status"] == "New"]:
            p = self._pos(sym)
            if not p["qty"]:
                continue
            trig = o["price"]
            if o["side"] == -1 and (b.open >= trig or b.high >= trig):
                px = (b.open if b.open >= trig else trig) * (1 - self.STOP_SLIP)
            elif o["side"] == 1 and (b.open <= trig or b.low <= trig):
                px = (b.open if b.open <= trig else trig) * (1 + self.STOP_SLIP)
            else:
                continue
            q = abs(p["qty"])
            fee = self._fill(sym, o["side"], q, px, False, ts, o["id"])
            o.update(status="Filled", filled=q, avg_price=px, fee=fee)
        # 2) limites (só executam se o preço negociar além do limite)
        for o in [o for o in live if o["type"] == "limit" and o["status"] in ("New", "PartiallyFilled")]:
            hit = b.low < o["price"] if o["side"] == 1 else b.high > o["price"]
            if not hit:
                continue
            q = o["qty"] - o["filled"]
            if o["reduce_only"]:
                pq = self._pos(sym)["qty"]
                q = min(q, abs(pq)) if pq and (pq > 0) != (o["side"] > 0) else 0
                if q <= 0:
                    o["status"] = "Cancelled"
                    continue
            fee = self._fill(sym, o["side"], q, o["price"], True, ts, o["id"])
            o["avg_price"] = (o["avg_price"] * o["filled"] + o["price"] * q) / (o["filled"] + q)
            o["filled"] += q
            o["fee"] += fee
            o["status"] = "Filled"

    def gc(self, keep_days=10):
        """Limpa ordens antigas encerradas para o estado não crescer indefinidamente."""
        lim = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=keep_days)
        self.st["orders"] = {k: o for k, o in self.st["orders"].items()
                             if o["status"] in ("New", "PartiallyFilled") or pd.Timestamp(o["created"]) > lim}
        self.st["fills"] = [f for f in self.st["fills"] if pd.Timestamp(f["time"]) > lim]
        self.st["funding"] = [f for f in self.st["funding"] if pd.Timestamp(f["time"]) > lim]


# ============================================================================ BYBIT (real/demo/testnet)
class BybitBroker:
    name = "bybit"

    def __init__(self, settings, market):
        from pybit.unified_trading import HTTP
        self.s, self.m = settings, market
        self.http = HTTP(testnet=settings.bybit_env == "testnet", demo=settings.bybit_env == "demo",
                         api_key=settings.api_key, api_secret=settings.api_secret, recv_window=10000,
                         max_retries=5, retry_delay=2)
        self._seq = itertools.count(int(time.time()) % 100000)

    def _call(self, fn, ignore=(), **kw):
        from pybit.exceptions import InvalidRequestError
        try:
            return getattr(self.http, fn)(**kw)
        except InvalidRequestError as e:
            if e.status_code in ignore:
                return {"retCode": e.status_code, "result": {}}
            raise

    def setup(self, symbols, leverage):
        msgs = []
        # one-way mode (110025 = já está nesse modo)
        try:
            self._call("switch_position_mode", ignore=(110025,), category="linear", coin="USDT", mode=0)
        except Exception as e:  # noqa: BLE001
            msgs.append(f"modo one-way: {e}")
        for s in symbols:
            try:  # 110043 = alavancagem já configurada
                self._call("set_leverage", ignore=(110043,), category="linear", symbol=s,
                           buyLeverage=str(leverage), sellLeverage=str(leverage))
            except Exception as e:  # noqa: BLE001
                msgs.append(f"alavancagem {s}: {e}")
        return msgs

    def wallet(self):
        r = self._call("get_wallet_balance", accountType="UNIFIED", coin="USDT")["result"]["list"][0]
        coin = [c for c in r["coin"] if c["coin"] == "USDT"][0]
        return float(coin["walletBalance"])

    def equity_mtm(self):
        r = self._call("get_wallet_balance", accountType="UNIFIED", coin="USDT")["result"]["list"][0]
        coin = [c for c in r["coin"] if c["coin"] == "USDT"][0]
        return float(coin["equity"])

    def book(self, sym):
        r = self._call("get_orderbook", category="linear", symbol=sym, limit=1)["result"]
        return float(r["b"][0][0]), float(r["a"][0][0])

    def instrument(self, sym):
        return self.m.instrument(sym)

    def position(self, sym):
        lst = self._call("get_positions", category="linear", symbol=sym)["result"]["list"]
        p = lst[0] if lst else {}
        size = float(p.get("size") or 0)
        sgn = 1 if p.get("side") == "Buy" else -1
        return {"size": sgn * size, "avg_price": float(p.get("avgPrice") or 0)}

    def all_positions(self):
        lst = self._call("get_positions", category="linear", settleCoin="USDT")["result"]["list"]
        return {p["symbol"]: float(p["size"]) * (1 if p["side"] == "Buy" else -1) for p in lst if float(p["size"] or 0) > 0}

    def _link(self, tag):
        return f"v1{tag}{next(self._seq)}"[:36]

    def post_only(self, sym, side, qty, price, reduce_only=False, tag=""):
        inst = self.instrument(sym)
        r = self._call("place_order", category="linear", symbol=sym, side=side_str(side), orderType="Limit",
                       qty=fmt(qty, inst["step"]), price=fmt(price, inst["tick"]), timeInForce="PostOnly",
                       reduceOnly=reduce_only, positionIdx=0, orderLinkId=self._link(tag))
        return {"id": r["result"]["orderId"], "status": "New"}

    def market(self, sym, side, qty, reduce_only=False, tag=""):
        inst = self.instrument(sym)
        r = self._call("place_order", category="linear", symbol=sym, side=side_str(side), orderType="Market",
                       qty=fmt(qty, inst["step"]), reduceOnly=reduce_only, positionIdx=0, orderLinkId=self._link(tag))
        oid = r["result"]["orderId"]
        for _ in range(10):
            time.sleep(0.5)
            o = self.order(sym, oid)
            if o["status"] in ("Filled", "Cancelled", "Rejected"):
                break
        return {"id": oid, **o}

    def order(self, sym, oid):
        lst = self._call("get_open_orders", category="linear", symbol=sym, orderId=oid)["result"]["list"]
        if not lst:
            lst = self._call("get_order_history", category="linear", symbol=sym, orderId=oid)["result"]["list"]
        if not lst:
            return {"status": "Unknown", "filled": 0.0, "avg_price": 0.0, "fee": 0.0}
        o = lst[0]
        st = o["orderStatus"]
        st = {"PartiallyFilledCanceled": "Cancelled", "Deactivated": "Cancelled"}.get(st, st)
        return {"status": st, "filled": float(o.get("cumExecQty") or 0), "avg_price": float(o.get("avgPrice") or 0),
                "fee": float(o.get("cumExecFee") or 0), "reject": o.get("rejectReason", "")}

    def cancel(self, sym, oid):
        # 110001 = ordem não existe / já encerrada
        self._call("cancel_order", ignore=(110001, 170213), category="linear", symbol=sym, orderId=oid)

    def set_stop(self, sym, pos_side, sl_price):
        inst = self.instrument(sym)
        self._call("set_trading_stop", category="linear", symbol=sym, stopLoss=fmt(sl_price, inst["tick"]),
                   slTriggerBy="LastPrice", tpslMode="Full", slOrderType="Market", positionIdx=0)

    def set_tp_market(self, sym, tp_price):
        inst = self.instrument(sym)
        self._call("set_trading_stop", category="linear", symbol=sym, takeProfit=fmt(tp_price, inst["tick"]),
                   tpTriggerBy="LastPrice", tpslMode="Full", tpOrderType="Market", positionIdx=0)

    def close_info(self, sym, since, pos_side):
        ms = int(pd.Timestamp(since).timestamp() * 1000)
        ex = self._call("get_executions", category="linear", symbol=sym, startTime=ms, limit=100)["result"]["list"]
        ex = [e for e in ex if e.get("execType") == "Trade" and e["side"] == side_str(-pos_side)]
        qty = sum(float(e["execQty"]) for e in ex)
        px = sum(float(e["execQty"]) * float(e["execPrice"]) for e in ex) / qty if qty else float("nan")
        fee = sum(float(e["execFee"]) for e in ex)
        fund = 0.0
        try:  # funding: mudança de saldo nos registros SETTLEMENT (negativo = pago)
            tl = self._call("get_transaction_log", accountType="UNIFIED", category="linear", currency="USDT",
                            type="SETTLEMENT", startTime=ms, limit=50)["result"]["list"]
            fund = sum(float(t.get("change") or 0) for t in tl if t.get("symbol") == sym)
        except Exception:  # noqa: BLE001
            pass
        last = max((int(e["execTime"]) for e in ex), default=None)
        return {"exit_qty": qty, "exit_price": px, "exit_fee": fee,
                "exit_maker": all(e.get("isMaker") for e in ex) if ex else None, "funding": fund,
                "exit_time": str(pd.to_datetime(last, unit="ms", utc=True)) if last else None}

    def tick(self, now):
        pass

    def gc(self, keep_days=10):
        pass
