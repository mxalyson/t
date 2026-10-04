"""Robô perp 2h (v1-congelada) — paper ou live, configurado pelo arquivo .env.

    python run_bot.py            inicia o robô (fica rodando; Ctrl+C para parar)
    python run_bot.py --check    testa .env, conexões, Telegram e tamanhos mínimos (não envia ordens)
    python run_bot.py --status   mostra posições e equity registradas
    python run_bot.py --report   gera o relatório de avaliação do protocolo (paper/report.py)
    python run_bot.py --resume   retoma entradas pausadas pelo kill-switch
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from bot.settings import load_settings  # noqa: E402
from bot.telegram import Telegram  # noqa: E402


def build(s):
    from bot.brokers import BybitBroker, PaperBroker
    from bot.market import Market
    from bot.trader import Trader
    m = Market("testnet" if s.mode == "live" and s.bybit_env == "testnet" else "mainnet")
    tg = Telegram(s.tg_token, s.tg_chat, prefix=s.label)
    if s.mode == "paper":
        factory = lambda st: PaperBroker(m, s, st)  # noqa: E731
    else:
        factory = lambda st: BybitBroker(s, m)  # noqa: E731
    return Trader(s, factory, m, tg), m, tg


def check(s):
    from bot.brokers import round_step
    from bot.market import Market
    from lib import SYMBOLS
    print(f"Modo: {s.label} | pasta: {s.data_dir}")
    print(f"Execução: entrada={s.entry_mode} TP={s.tp_order} saída={s.exit_mode} -> perfil {s.exec_profile}")
    if s.exec_profile != "oficial":
        print("  ATENÇÃO: perfil maker é uma VARIANTE de execução e não conta para o protocolo da v1 (VEREDITO.md).")
    m = Market("testnet" if s.mode == "live" and s.bybit_env == "testnet" else "mainnet")
    ok = True
    try:
        k = m.signal_klines("BTCUSDT", m.now())
        print(f"[ok] Binance spot: última vela fechada {k.index[-1]}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[ERRO] Binance: {e}")
    try:
        b, a = m.book("BTCUSDT")
        print(f"[ok] Bybit perp: BTCUSDT bid {b} ask {a}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[ERRO] Bybit público: {e}")
    eq = s.capital
    if s.mode == "live":
        try:
            from bot.brokers import BybitBroker
            br = BybitBroker(s, m)
            w = br.wallet()
            eq = min(w, s.capital)
            print(f"[ok] Conta Bybit ({s.bybit_env}): saldo USDT {w:,.2f} | usado p/ sizing {eq:,.2f}")
            pos = br.all_positions()
            print(f"[ok] Posições abertas na conta: {pos or 'nenhuma'}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[ERRO] API privada da Bybit: {e}")
    print("\nTamanho típico por trade (risco 0,5%, SL = 1,5×ATR):")
    for sym in SYMBOLS:
        try:
            inst = m.instrument(sym)
            k = m.signal_klines(sym, m.now())
            from lib import atr
            ap = float((atr(k) / k["close"]).iloc[-1])
            px = float(k["close"].iloc[-1])
            notional = min(0.005 / (1.5 * ap), 2) * eq
            q = round_step(notional / px, inst["step"])
            flag = "ok" if q >= inst["min_qty"] and q * px >= max(5, inst["min_notional"]) else "ABAIXO DO MÍNIMO"
            print(f"  {sym:9s} ATR {ap:.2%} notional {notional:9.2f} qtd {q:g} (mín {inst['min_qty']:g}) {flag}")
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: erro {e}")
    tg = Telegram(s.tg_token, s.tg_chat, prefix=s.label)
    if tg.enabled:
        print("[ok] Telegram: mensagem de teste enviada" if tg.send("✅ Teste de conexão do robô") else "[ERRO] Telegram")
    else:
        print("[aviso] Telegram não configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    print("\nTudo certo." if ok else "\nHá erros acima.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"))
    a = ap.parse_args()
    if not os.path.exists(a.env):
        raise SystemExit("Arquivo .env não encontrado. Copie .env.example para .env e preencha.")
    s = load_settings(a.env)
    st_path = os.path.join(s.data_dir, "state.json")
    if a.check:
        return check(s)
    if a.status or a.resume:
        if not os.path.exists(st_path):
            raise SystemExit(f"Nenhum registro em {s.data_dir}")
        st = json.load(open(st_path))
        if a.resume:
            st["paused"] = False
            st["peak"] = None
            json.dump(st, open(st_path, "w"), indent=1, default=str)
            print("Entradas retomadas (pico de equity reiniciado).")
            return
        print(json.dumps({k: st[k] for k in ["version", "mode_label", "equity0", "peak", "paused", "last_bar"]}, indent=1))
        for sym, p in st["positions"].items():
            print(f"  {sym} {'LONG' if p['side'] == 1 else 'SHORT'} qtd {p['qty']} @ {p['entry_price']} TP {p['tp']} SL {p['sl']}")
        return
    if a.report:
        return subprocess.call([sys.executable, os.path.join(ROOT, "paper", "report.py"), "--out", s.data_dir])

    os.makedirs(os.path.join(ROOT, "runtime"), exist_ok=True)
    trader, m, tg = build(s)
    trader.startup()
    print(f"Rodando ({s.label}). Registros em {s.data_dir}. Ctrl+C para parar.", flush=True)
    trader.status_line()
    last_status = time.time()
    try:
        while True:
            trader.step()
            if s.console_status and time.time() - last_status >= s.console_status:
                trader.status_line()
                last_status = time.time()
            time.sleep(s.loop_sec)
    except KeyboardInterrupt:
        trader.save()
        tg.send("🛑 Robô parado manualmente. As posições abertas continuam com stop na corretora"
                + (" (no paper, elas ficam congeladas até reiniciar)." if s.mode == "paper" else "."))


if __name__ == "__main__":
    main()
