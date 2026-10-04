"""Leitura do .env e da configuração congelada (config.json)."""
import json
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    for k in list(env):
        env[k] = os.environ.get(k, env[k])
    return env


@dataclass
class Settings:
    mode: str = "paper"
    bybit_env: str = "demo"
    api_key: str = ""
    api_secret: str = ""
    live_confirm: str = ""
    capital: float = 1000.0
    leverage: int = 5
    entry_mode: str = "taker"
    maker_timeout: int = 600
    reprice: int = 20
    max_chase_bps: float = 15.0
    maker_fallback: str = "taker"
    tp_order: str = "market"
    exit_mode: str = "taker"
    exit_maker_timeout: int = 300
    maker_fee: float = 0.0002
    taker_fee: float = 0.00055
    max_dd: float = 15.0
    tg_token: str = ""
    tg_chat: str = ""
    tg_daily: bool = True
    auto_retrain: bool = True
    data_dir: str = ""
    loop_sec: int = 15
    signal_delay: int = 60
    max_late: int = 900
    console_status: int = 300
    frozen: dict = field(default_factory=dict)

    @property
    def sleeve(self):
        return self.frozen["sleeves"][0]

    @property
    def pf(self):
        return self.frozen["portfolio"]

    @property
    def exec_profile(self):
        """'oficial' = execução idêntica ao backtest (protocolo do VEREDITO). Qualquer uso de maker
        é uma VARIANTE de execução, registrada separadamente."""
        if self.entry_mode == "taker" and self.tp_order == "market" and self.exit_mode == "taker":
            return "oficial"
        return "maker"

    @property
    def label(self):
        base = "PAPER" if self.mode == "paper" else f"LIVE-{self.bybit_env.upper()}"
        return f"{base}-{self.exec_profile.upper()}"


def load_settings(env_path=None):
    e = load_env(env_path or os.path.join(ROOT, ".env"))
    g = lambda k, d: e.get(k, "") or d  # noqa: E731
    s = Settings(
        mode=g("MODE", "paper").lower(), bybit_env=g("BYBIT_ENV", "demo").lower(),
        api_key=g("BYBIT_API_KEY", ""), api_secret=g("BYBIT_API_SECRET", ""), live_confirm=g("LIVE_CONFIRM", ""),
        capital=float(g("CAPITAL_USDT", 1000)), leverage=int(g("LEVERAGE", 5)),
        entry_mode=g("ENTRY_MODE", "taker").lower(), maker_timeout=int(g("MAKER_TIMEOUT_SEC", 600)),
        reprice=int(g("REPRICE_SEC", 20)), max_chase_bps=float(g("MAX_CHASE_BPS", 15)),
        maker_fallback=g("MAKER_FALLBACK", "taker").lower(), tp_order=g("TP_ORDER", "market").lower(),
        exit_mode=g("EXIT_MODE", "taker").lower(), exit_maker_timeout=int(g("EXIT_MAKER_TIMEOUT_SEC", 300)),
        maker_fee=float(g("MAKER_FEE", 0.0002)), taker_fee=float(g("TAKER_FEE", 0.00055)),
        max_dd=float(g("MAX_DRAWDOWN_PCT", 15)), tg_token=g("TELEGRAM_BOT_TOKEN", ""),
        tg_chat=g("TELEGRAM_CHAT_ID", ""), tg_daily=g("TELEGRAM_DAILY", "1") == "1",
        auto_retrain=g("AUTO_RETRAIN", "1") == "1", data_dir=g("DATA_DIR", ""),
        loop_sec=int(g("LOOP_SEC", 15)), signal_delay=int(g("SIGNAL_DELAY_SEC", 60)),
        max_late=int(g("MAX_LATE_SEC", 900)), console_status=int(g("CONSOLE_STATUS_SEC", 300)),
        frozen=json.load(open(os.path.join(ROOT, "config.json"))))
    if not s.data_dir:
        base = "paper" if s.mode == "paper" else f"live_{s.bybit_env}"
        s.data_dir = os.path.join(ROOT, "runtime", f"{base}_{s.exec_profile}")
    validate(s)
    return s


def validate(s):
    err = []
    if s.mode not in ("paper", "live"):
        err.append("MODE deve ser paper ou live")
    if s.bybit_env not in ("demo", "testnet", "mainnet"):
        err.append("BYBIT_ENV deve ser demo, testnet ou mainnet")
    if s.mode == "live":
        if not s.api_key or not s.api_secret:
            err.append("MODE=live exige BYBIT_API_KEY e BYBIT_API_SECRET")
        if s.bybit_env == "mainnet" and s.live_confirm != "EU_ENTENDO_O_RISCO":
            err.append("Conta REAL (mainnet) exige LIVE_CONFIRM=EU_ENTENDO_O_RISCO no .env")
    for k, ok in [("ENTRY_MODE", s.entry_mode in ("maker", "taker")), ("MAKER_FALLBACK", s.maker_fallback in ("taker", "skip")),
                  ("TP_ORDER", s.tp_order in ("maker", "market")), ("EXIT_MODE", s.exit_mode in ("maker", "taker"))]:
        if not ok:
            err.append(f"valor inválido em {k}")
    if s.capital <= 0:
        err.append("CAPITAL_USDT deve ser > 0")
    if err:
        raise SystemExit("Erro no .env:\n  - " + "\n  - ".join(err))
