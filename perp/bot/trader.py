"""Robô da v1-congelada: mesmas regras do backtest, execução maker/taker, paper ou live.

Regras congeladas (config.json): sinal na vela 2h; edge > 0,12; TP 3×ATR / SL 1,5×ATR a partir do
preço médio de entrada; saída por tempo 48 velas após o fechamento da vela do sinal; risco 0,5% da
equity por trade; no máximo 4 posições e 3 líquidas na mesma direção.
"""
import json
import os
import shutil
import subprocess
import sys
import traceback

import numpy as np
import pandas as pd

from .brokers import round_price, round_step
from .telegram import esc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "paper"))

from engine import FROZEN_FILES, VERSION, ModelSignals, file_hash, models_hash  # noqa: E402
from lib import SYMBOLS  # noqa: E402

BAR = pd.Timedelta(hours=2)


def _append(path, rows):
    if rows:
        pd.DataFrame(rows).to_csv(path, mode="a", header=not os.path.exists(path), index=False)


class Trader:
    def __init__(self, s, broker_factory, market, tg, model_dir=None, clock=None, signals=None):
        self.s, self.m, self.tg = s, market, tg
        self.clock = clock or market.now
        self.dir = s.data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.model_dir = model_dir or os.path.join(ROOT, "models")
        frozen = file_hash(FROZEN_FILES)
        if os.path.exists(self.state_path):
            self.state = json.load(open(self.state_path))
            if self.state.get("frozen_hash") != frozen:
                raise SystemExit("Arquivos congelados mudaram desde o início deste registro. Isso é uma NOVA versão: "
                                 "use outro DATA_DIR.")
            if self.state.get("mode_label") != s.label:
                raise SystemExit(f"DATA_DIR {self.dir} pertence ao modo {self.state.get('mode_label')}; use outra pasta.")
        else:
            self.state = {"version": VERSION, "frozen_hash": frozen, "mode_label": s.label, "equity0": None,
                          "peak": None, "paused": False, "last_bar": None, "pending": {}, "positions": {},
                          "exits": {}, "last_daily": None, "last_retrain": None, "created": str(self.clock())}
        self.broker = broker_factory(self.state)
        self.signals = signals or ModelSignals(self.model_dir, s.sleeve)
        self.model_hash = models_hash(self.model_dir) if signals is None else "teste"
        self.retrain_proc = None
        if self.state["equity0"] is None:
            self.state["equity0"] = self.sizing_equity()
            self.state["peak"] = self.state["equity0"]
        self.save()

    # ------------------------------------------------------------------ util
    def save(self):
        tmp = self.state_path + ".tmp"
        json.dump(self.state, open(tmp, "w"), indent=1, default=str)
        os.replace(tmp, self.state_path)

    def log(self, text):
        """Imprime no terminal e grava em console.log."""
        line = f"[{self.clock():%Y-%m-%d %H:%M:%S}] {text}"
        print(line, flush=True)
        try:
            with open(os.path.join(self.dir, "console.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def status_line(self):
        now = self.clock()
        nxt = now.floor("2h") + BAR + pd.Timedelta(seconds=self.s.signal_delay)
        mins = int((nxt - now).total_seconds() // 60)
        try:
            mtm = self.broker.equity_mtm()
            eq = f"equity {mtm:,.2f} ({mtm / self.state['equity0'] - 1:+.2%})"
        except Exception as e:  # noqa: BLE001
            eq = f"equity indisponível ({type(e).__name__})"
        pos = []
        for sym, p in self.state["positions"].items():
            try:
                bid, ask = self.broker.book(sym)
                r = p["side"] * ((bid + ask) / 2 / p["entry_price"] - 1)
                pos.append(f"{sym.replace('USDT', '')} {'L' if p['side'] == 1 else 'S'} {r:+.2%}")
            except Exception:  # noqa: BLE001
                pos.append(sym.replace("USDT", ""))
        pend = [f"{s.replace('USDT', '')}(aguardando maker)" for s in self.state["pending"]]
        self.log(f"{self.s.label} | {eq} | posições {len(self.state['positions'])}/{self.s.pf['max_pos']}: "
                 f"{', '.join(pos + pend) or 'nenhuma'} | próxima vela em {mins // 60}h{mins % 60:02d}m"
                 + (" | ⏸ PAUSADO" if self.state["paused"] else ""))

    def event(self, kind, **kw):
        _append(os.path.join(self.dir, "events.csv"), [{"time": str(self.clock()), "event": kind,
                                                        "data": json.dumps(kw, default=str)}])

    def sizing_equity(self):
        w = self.broker.wallet()
        return min(w, self.s.capital) if self.s.mode == "live" else w

    def startup(self):
        msgs = self.broker.setup(SYMBOLS, self.s.leverage)
        for m in msgs:
            self.tg.send(f"⚠️ Configuração: {esc(m)}")
        if hasattr(self.broker, "all_positions"):
            known = set(self.state["positions"]) | set(self.state["pending"])
            for sym, size in self.broker.all_positions().items():
                if sym not in known:
                    self.tg.send(f"⚠️ Posição em {sym} ({size}) não foi aberta pelo robô. Ela será ignorada.")
        eq = self.sizing_equity()
        self.tg.send(f"🚀 Robô iniciado — {esc(self.s.label)}\nVersão {VERSION} | equity p/ sizing {eq:,.2f} USDT\n"
                     f"Entrada {self.s.entry_mode} (fallback {self.s.maker_fallback}) | TP {self.s.tp_order} | "
                     f"saída por tempo {self.s.exit_mode}\nPosições abertas: {len(self.state['positions'])}"
                     + ("\n⏸️ Novas entradas PAUSADAS (kill-switch)" if self.state["paused"] else ""))

    # ------------------------------------------------------------------ loop
    def step(self):
        now = self.clock()
        try:
            self.broker.tick(now)
            for sym in list(self.state["pending"]):
                self._manage_entry(sym, now)
            for sym in list(self.state["positions"]):
                self._manage_position(sym, now)
            self._maybe_signal_cycle(now)
            self._maybe_daily(now)
            self._maybe_retrain(now)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            self.log(tb)
            self.event("erro", erro=str(e), tb=tb[-2000:])
            self.tg.send(f"❗ Erro: {esc(str(e)[:300])}", key=f"err:{type(e).__name__}", every=900)
        self.save()

    # ------------------------------------------------------------------ sinais
    def _maybe_signal_cycle(self, now):
        bar_close = now.floor("2h")
        bar_time = bar_close - BAR
        if self.state["last_bar"] and pd.Timestamp(self.state["last_bar"]) >= bar_time:
            return
        if now < bar_close + pd.Timedelta(seconds=self.s.signal_delay):
            return
        if (now - bar_close).total_seconds() > self.s.max_late:
            self.log(f"Vela {bar_time:%d/%m %H:%M} ignorada: robô iniciou {int((now - bar_close).total_seconds() // 60)} min "
                     f"após o fechamento (limite {self.s.max_late // 60} min). Aguardando a próxima.")
            self.state["last_bar"] = str(bar_time)
            self.event("vela_ignorada_atraso", bar_time=bar_time)
            return
        sig = self.signals(self.m, bar_time)
        if sig is None or len(sig) == 0:
            self.tg.send(f"⚠️ Sem dados para a vela {bar_time:%d/%m %H:%M}", key="nodata", every=3600)
            if (now - bar_close).total_seconds() > 600:
                self.state["last_bar"] = str(bar_time)
            return
        if len(sig) < len(SYMBOLS):
            self.event("sinais_incompletos", n=len(sig))
        sig["side"] = np.where(sig["edge_long"] >= sig["edge_short"], 1, -1)
        sig["score"] = sig[["edge_long", "edge_short"]].max(axis=1)
        sig = sig.sort_values("score", ascending=False).reset_index(drop=True)
        rows, entries = [], []
        eq = self.sizing_equity()
        for r in sig.itertuples(index=False):
            dec = "abaixo_limiar"
            if r.score > self.s.sleeve["thr"]:
                book = {**self.state["positions"], **self.state["pending"]}
                net = sum(p["side"] for p in book.values())
                if self.state["paused"]:
                    dec = "pausado_killswitch"
                elif r.symbol in book:
                    dec = "ja_posicionado"
                elif len(book) >= self.s.pf["max_pos"]:
                    dec = "max_posicoes"
                elif r.side * net >= self.s.pf["max_net"]:
                    dec = "max_liquido"
                elif not np.isfinite(r.atr_pct) or r.atr_pct <= 0:
                    dec = "atr_invalido"
                else:
                    dec = self._start_entry(r, bar_time, bar_close, eq, now)
                    if dec == "ENTRADA":
                        entries.append(r)
            rows.append({"bar_time": str(bar_time), "run_time": str(now), "symbol": r.symbol,
                         "edge_long": r.edge_long, "edge_short": r.edge_short, "side": int(r.side), "score": r.score,
                         "atr_pct": r.atr_pct, "close_spot": r.close_spot, "decision": dec, "model_hash": self.model_hash})
        _append(os.path.join(self.dir, "signals.csv"), rows)
        lines = [f"Vela {bar_time:%d/%m %H:%M} UTC — limiar {self.s.sleeve['thr']}"]
        for r in rows:
            lado = "LONG " if r["side"] == 1 else "SHORT"
            mark = "  <<<" if r["decision"] == "ENTRADA" else ""
            lines.append(f"   {r['symbol']:9s} {lado} score {r['score']:+.3f}  {r['decision']}{mark}")
        self.log("\n".join(lines))
        mtm = self.broker.equity_mtm()
        _append(os.path.join(self.dir, "equity.csv"), [{
            "time": str(now), "bar_time": str(bar_time), "equity_realized": self.broker.wallet(), "equity_mtm": mtm,
            "open_positions": len(self.state["positions"]), "pending": len(self.state["pending"])}])
        self._killswitch(mtm)
        self.state["last_bar"] = str(bar_time)
        top = sig.iloc[0]
        self.event("ciclo", bar_time=bar_time, entradas=len(entries), top=top.symbol, top_score=round(float(top.score), 4))

    def _killswitch(self, mtm):
        self.state["peak"] = max(self.state["peak"] or mtm, mtm)
        dd = 1 - mtm / self.state["peak"]
        if dd * 100 >= self.s.max_dd and not self.state["paused"]:
            self.state["paused"] = True
            self.tg.send(f"⏸️ KILL-SWITCH: drawdown {dd:.1%} ≥ {self.s.max_dd}%. Novas entradas pausadas; "
                         f"posições abertas seguem com stop.\nPara retomar: <code>python run_bot.py --resume</code>")

    # ------------------------------------------------------------------ entradas
    def _start_entry(self, r, bar_time, bar_close, eq, now):
        sym, side = r.symbol, int(r.side)
        inst = self.broker.instrument(sym)
        bid, ask = self.broker.book(sym)
        mid = (bid + ask) / 2
        sl_pct = self.s.sleeve["sl"] * r.atr_pct
        notional = min(self.s.pf["risk"] / sl_pct, self.s.pf["max_lev"]) * eq
        qty = round_step(notional / mid, inst["step"])
        if qty < inst["min_qty"] or qty * mid < max(inst["min_notional"], 5.0):
            self.tg.send(f"ℹ️ {sym}: sinal {'LONG' if side == 1 else 'SHORT'} (score {r.score:.3f}) abaixo do "
                         f"tamanho mínimo da Bybit (notional {notional:.2f} USDT). Aumente CAPITAL_USDT.",
                         key=f"min:{sym}", every=86400)
            return "abaixo_minimo"
        pend = {"symbol": sym, "side": side, "qty_target": qty, "filled": 0.0, "cost": 0.0, "fees": 0.0,
                "maker_qty": 0.0, "order_id": None, "order_filled": 0.0, "price": None,
                "ref_mid": mid, "ref_bid": bid, "ref_ask": ask, "started": str(now), "last_reprice": None,
                "signal_time": str(bar_time), "time_exit_at": str(bar_close + self.s.sleeve["H"] * BAR),
                "score": float(r.score), "edge_long": float(r.edge_long), "edge_short": float(r.edge_short),
                "atr_pct": float(r.atr_pct), "close_spot": float(r.close_spot), "risk_usdt": self.s.pf["risk"] * eq}
        self.state["pending"][sym] = pend
        self.event("entrada_iniciada", **{k: pend[k] for k in ["symbol", "side", "qty_target", "ref_mid", "score"]})
        if self.s.entry_mode == "taker":
            self._entry_market(sym, pend, qty)
            self._finalize_entry(sym, now)
        else:
            self._entry_post(sym, pend, now)
        return "ENTRADA"

    def _entry_post(self, sym, pend, now):
        bid, ask = self.broker.book(sym)
        inst = self.broker.instrument(sym)
        side = pend["side"]
        px = bid if side == 1 else ask
        chase = side * (px / pend["ref_mid"] - 1) * 1e4
        if chase > self.s.max_chase_bps:
            return False  # preço fugiu além do limite de perseguição: aguarda (ou prazo → fallback)
        px = round_price(px, inst["tick"], "down" if side == 1 else "up")
        rem = round_step(pend["qty_target"] - pend["filled"], inst["step"])
        if rem < inst["min_qty"]:
            return False
        o = self.broker.post_only(sym, side, rem, px, reduce_only=False, tag="E")
        pend.update(order_id=o["id"], order_filled=0.0, price=px, last_reprice=str(now))
        self.event("maker_colocada", symbol=sym, price=px, qty=rem, status=o["status"])
        return True

    def _absorb(self, pend, sym):
        """Atualiza o executado da ordem maker atual; devolve o status."""
        o = self.broker.order(sym, pend["order_id"])
        new = o["filled"] - pend["order_filled"]
        if new > 0:
            pend["filled"] += new
            pend["maker_qty"] += new
            pend["cost"] += new * (o["avg_price"] or pend["price"])
            pend["fees"] += o["fee"] * new / max(o["filled"], 1e-12)
            pend["order_filled"] = o["filled"]
        return o["status"]

    def _entry_market(self, sym, pend, qty):
        o = self.broker.market(sym, pend["side"], qty, tag="ET")
        if o.get("filled"):
            pend["filled"] += o["filled"]
            pend["cost"] += o["filled"] * o["avg_price"]
            pend["fees"] += o["fee"]

    def _manage_entry(self, sym, now):
        pend = self.state["pending"][sym]
        inst = self.broker.instrument(sym)
        if pend["order_id"]:
            st = self._absorb(pend, sym)
            if st in ("Cancelled", "Rejected", "Unknown"):
                pend["order_id"] = None
        done = pend["qty_target"] - pend["filled"] < inst["min_qty"]
        elapsed = (now - pd.Timestamp(pend["started"])).total_seconds()
        if done:
            return self._finalize_entry(sym, now)
        if elapsed >= self.s.maker_timeout:
            if pend["order_id"]:
                self.broker.cancel(sym, pend["order_id"])
                self._absorb(pend, sym)
                pend["order_id"] = None
            rem = round_step(pend["qty_target"] - pend["filled"], inst["step"])
            if self.s.maker_fallback == "taker" and rem >= inst["min_qty"]:
                self._entry_market(sym, pend, rem)
            return self._finalize_entry(sym, now)
        if pend["order_id"] is None:
            self._entry_post(sym, pend, now)
            return
        if (now - pd.Timestamp(pend["last_reprice"])).total_seconds() >= self.s.reprice:
            bid, ask = self.broker.book(sym)
            touch = bid if pend["side"] == 1 else ask
            if abs(touch - pend["price"]) >= inst["tick"] / 2:
                self.broker.cancel(sym, pend["order_id"])
                self._absorb(pend, sym)
                pend["order_id"] = None
                if pend["qty_target"] - pend["filled"] >= inst["min_qty"]:
                    self._entry_post(sym, pend, now)
                else:
                    self._finalize_entry(sym, now)

    def _finalize_entry(self, sym, now):
        pend = self.state["pending"].pop(sym)
        if pend["filled"] <= 0:
            self.event("entrada_nao_executada", symbol=sym)
            self.tg.send(f"⌛ {sym}: entrada maker não executada em {self.s.maker_timeout // 60} min "
                         f"(MAKER_FALLBACK=skip). Trade descartado.")
            _append(os.path.join(self.dir, "missed.csv"), [{**{k: pend[k] for k in ["symbol", "side", "signal_time",
                                                                                      "score", "ref_mid"]}, "time": str(now)}])
            return
        side, qty = pend["side"], pend["filled"]
        avg = pend["cost"] / qty
        inst = self.broker.instrument(sym)
        tp = round_price(avg * (1 + side * self.s.sleeve["tp"] * pend["atr_pct"]), inst["tick"])
        sl = round_price(avg * (1 - side * self.s.sleeve["sl"] * pend["atr_pct"]), inst["tick"])
        self.broker.set_stop(sym, side, sl)
        pos = {**pend, "qty": qty, "entry_price": avg, "entry_time": str(now), "entry_fee": pend["fees"],
               "entry_maker_frac": pend["maker_qty"] / qty, "fill_wait_s": (now - pd.Timestamp(pend["started"])).total_seconds(),
               "tp": tp, "sl": sl, "tp_order_id": None, "exit": None}
        self.state["positions"][sym] = pos
        self._ensure_tp(sym, pos)
        risk = qty * abs(avg - sl)
        self.event("posicao_aberta", symbol=sym, side=side, qty=qty, avg=avg, tp=tp, sl=sl, maker=pos["entry_maker_frac"])
        self.tg.send(
            f"{'🟢 LONG' if side == 1 else '🔴 SHORT'} <b>{sym}</b> aberto\n"
            f"Preço {avg:.6g} ({'maker' if pos['entry_maker_frac'] > 0.99 else 'taker' if pos['entry_maker_frac'] < 0.01 else 'misto'}"
            f", {pos['fill_wait_s']:.0f}s) | qtd {qty:g} | notional {qty * avg:,.2f}\n"
            f"TP {tp:.6g} | SL {sl:.6g} | risco {risk:,.2f} USDT\nScore {pend['score']:.3f} | saída por tempo "
            f"{pd.Timestamp(pend['time_exit_at']):%d/%m %H:%M} UTC")

    # ------------------------------------------------------------------ posições
    def _ensure_tp(self, sym, pos):
        if self.s.tp_order == "market":
            if not pos.get("tp_set"):
                self.broker.set_tp_market(sym, pos["tp"])
                pos["tp_set"] = True
            return
        if pos["tp_order_id"]:
            st = self.broker.order(sym, pos["tp_order_id"])["status"]
            if st in ("New", "PartiallyFilled", "Filled"):
                return
        bid, ask = self.broker.book(sym)
        beyond = bid >= pos["tp"] if pos["side"] == 1 else ask <= pos["tp"]
        if beyond:  # TP já ultrapassado: realiza a mercado
            self.broker.market(sym, -pos["side"], pos["qty"], reduce_only=True, tag="TPM")
            pos["exit"] = {"reason": "tp_mercado", "started": str(self.clock())}
            return
        o = self.broker.post_only(sym, -pos["side"], pos["qty"], pos["tp"], reduce_only=True, tag="TP")
        pos["tp_order_id"] = o["id"]

    def _manage_position(self, sym, now):
        pos = self.state["positions"][sym]
        size = self.broker.position(sym)["size"]
        inst = self.broker.instrument(sym)
        if abs(size) < inst["min_qty"] / 2:
            return self._record_close(sym, now)
        ex = pos.get("exit")
        if ex and ex["reason"] == "tempo":
            return self._manage_time_exit(sym, pos, now, size)
        if now >= pd.Timestamp(pos["time_exit_at"]):
            if pos["tp_order_id"]:
                self.broker.cancel(sym, pos["tp_order_id"])
            pos["exit"] = {"reason": "tempo", "started": str(now), "order_id": None, "last": None}
            return self._manage_time_exit(sym, pos, now, size)
        if not ex:
            self._ensure_tp(sym, pos)

    def _manage_time_exit(self, sym, pos, now, size):
        ex = pos["exit"]
        qty = abs(size)
        elapsed = (now - pd.Timestamp(ex["started"])).total_seconds()
        if self.s.exit_mode == "taker" or elapsed >= self.s.exit_maker_timeout:
            if ex.get("order_id"):
                self.broker.cancel(sym, ex["order_id"])
            self.broker.market(sym, -pos["side"], qty, reduce_only=True, tag="XT")
            return
        if ex.get("order_id"):
            st = self.broker.order(sym, ex["order_id"])["status"]
            alive = st in ("New", "PartiallyFilled")
            if alive and (now - pd.Timestamp(ex["last"])).total_seconds() < self.s.reprice:
                return
            if alive:
                self.broker.cancel(sym, ex["order_id"])
        bid, ask = self.broker.book(sym)
        inst = self.broker.instrument(sym)
        px = round_price(ask if pos["side"] == 1 else bid, inst["tick"], "up" if pos["side"] == 1 else "down")
        o = self.broker.post_only(sym, -pos["side"], qty, px, reduce_only=True, tag="X")
        ex.update(order_id=o["id"], last=str(now))

    def _record_close(self, sym, now):
        pos = self.state["positions"].pop(sym)
        if pos.get("tp_order_id"):
            try:
                self.broker.cancel(sym, pos["tp_order_id"])
            except Exception:  # noqa: BLE001
                pass
        info = self.broker.close_info(sym, pos["entry_time"], pos["side"])
        side, qty, entry = pos["side"], pos["qty"], pos["entry_price"]
        exit_px = info["exit_price"] if np.isfinite(info["exit_price"]) else entry
        reason = (pos.get("exit") or {}).get("reason")
        if not reason:
            reason = "tp" if abs(exit_px - pos["tp"]) < abs(exit_px - pos["sl"]) else "sl"
        gross = side * qty * (exit_px - entry)
        fees = pos["entry_fee"] + info["exit_fee"]
        net = gross - fees + info["funding"]
        notional = qty * entry
        exit_time = info["exit_time"] or str(now)
        risk = qty * abs(entry - pos["sl"])
        row = {"symbol": sym, "side": side, "signal_time": pos["signal_time"], "entry_time": pos["entry_time"],
               "score": pos["score"], "edge_long": pos["edge_long"], "edge_short": pos["edge_short"],
               "atr_pct": pos["atr_pct"], "expected_price": pos["close_spot"], "entry_price": entry,
               "entry_mid": pos["ref_mid"], "entry_spread_bps": (pos["ref_ask"] - pos["ref_bid"]) / pos["ref_mid"] * 1e4,
               "entry_slip_vs_mid_bps": side * (entry / pos["ref_mid"] - 1) * 1e4,
               "entry_vs_spot_close_bps": side * (entry / pos["close_spot"] - 1) * 1e4,
               "entry_maker_frac": pos["entry_maker_frac"], "fill_wait_s": pos["fill_wait_s"],
               "tp": pos["tp"], "sl": pos["sl"], "qty": qty, "notional": notional, "exit_time": exit_time,
               "exit_reason": reason, "exit_price": exit_px, "exit_maker": info["exit_maker"],
               "gross_pnl": gross, "fees": fees, "funding_pnl": info["funding"], "net_pnl": net,
               "net_ret_on_notional": net / notional, "R": net / risk if risk else np.nan,
               "bars_held": (pd.Timestamp(exit_time) - pd.Timestamp(pos["entry_time"])) / BAR,
               "equity_after": self.broker.wallet(), "model_hash": self.model_hash, "mode": self.s.label}
        _append(os.path.join(self.dir, "trades.csv"), [row])
        self.event("posicao_fechada", symbol=sym, reason=reason, net=net)
        icon = {"tp": "✅", "tp_mercado": "✅", "sl": "🛑", "tempo": "⏱️"}.get(reason, "•")
        self.tg.send(f"{icon} <b>{sym}</b> {'LONG' if side == 1 else 'SHORT'} fechado: {reason.upper()}\n"
                     f"Entrada {entry:.6g} → saída {exit_px:.6g}\nPnL líquido <b>{net:+,.2f} USDT</b> "
                     f"({row['R']:+.2f}R) | taxas {fees:.2f} | funding {info['funding']:+.2f}\n"
                     f"Equity {row['equity_after']:,.2f}")

    # ------------------------------------------------------------------ rotinas
    def _maybe_daily(self, now):
        if not self.s.tg_daily or now.hour != 0 or now.minute < 5:
            return
        day = now.strftime("%Y-%m-%d")
        if self.state["last_daily"] == day:
            return
        self.state["last_daily"] = day
        mtm = self.broker.equity_mtm()
        p = os.path.join(self.dir, "trades.csv")
        tr = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame(columns=["exit_time", "net_pnl", "entry_maker_frac"])
        last = tr[pd.to_datetime(tr["exit_time"], utc=True, format="mixed") >= now - pd.Timedelta(days=1)] if len(tr) else tr
        pos = "\n".join(f"  {s} {'L' if p['side'] == 1 else 'S'} @ {p['entry_price']:.6g}"
                        for s, p in self.state["positions"].items()) or "  nenhuma"
        self.tg.send(f"📊 Resumo diário ({esc(self.s.label)})\nEquity MTM {mtm:,.2f} "
                     f"({mtm / self.state['equity0'] - 1:+.2%} desde o início)\n"
                     f"Trades 24h: {len(last)} | PnL 24h {last['net_pnl'].sum():+,.2f}\n"
                     f"Total de trades: {len(tr)} | maker nas entradas: "
                     f"{tr['entry_maker_frac'].mean():.0%}\n" if len(tr) else
                     f"📊 Resumo diário ({esc(self.s.label)})\nEquity MTM {mtm:,.2f}\nNenhum trade fechado ainda.\n")
        self.tg.send(f"Posições abertas:\n{pos}" + ("\n⏸️ Entradas pausadas (kill-switch)" if self.state["paused"] else ""))

    def _maybe_retrain(self, now):
        if self.retrain_proc is not None:
            rc = self.retrain_proc.poll()
            if rc is None:
                return
            self.retrain_proc = None
            new = os.path.join(ROOT, "models_new")
            if rc == 0 and os.path.exists(os.path.join(new, f"{self.s.sleeve['name']}_meta.json")):
                for f in os.listdir(new):
                    shutil.copy2(os.path.join(new, f), os.path.join(self.model_dir, f))
                self.signals = ModelSignals(self.model_dir, self.s.sleeve)
                self.model_hash = models_hash(self.model_dir)
                self.tg.send(f"🔁 Retreino mensal concluído. Modelos: {self.model_hash[:12]}")
            else:
                self.tg.send(f"❗ Retreino mensal falhou (código {rc}). Mantidos os modelos anteriores. Veja runtime/retrain.log")
            return
        if not self.s.auto_retrain or now.day != 1 or (now.hour, now.minute) < (0, 30):
            return
        month = now.strftime("%Y-%m")
        if self.state["last_retrain"] == month:
            return
        self.state["last_retrain"] = month
        py = sys.executable
        cmd = (f'"{py}" fetch_history.py pq 2h && "{py}" train_final.py pq config.json models_new')
        log = open(os.path.join(ROOT, "runtime", "retrain.log"), "a")
        self.retrain_proc = subprocess.Popen(cmd, shell=True, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                             env={**os.environ, "SEEDS": "7,11", "NT": "2"})
        self.tg.send("🔁 Retreino mensal iniciado (baixando histórico e treinando; leva alguns minutos).")
