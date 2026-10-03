"""Envio de mensagens ao Telegram (falhas nunca derrubam o robô)."""
import html
import time

import requests


class Telegram:
    def __init__(self, token, chat_id, prefix=""):
        self.token, self.chat, self.prefix = token, chat_id, prefix
        self._last = {}

    @property
    def enabled(self):
        return bool(self.token and self.chat)

    def send(self, text, key=None, every=600):
        """key/every: limita mensagens repetidas (ex.: erros) a 1 a cada `every` segundos."""
        if key:
            now = time.time()
            if now - self._last.get(key, 0) < every:
                return False
            self._last[key] = now
        msg = f"<b>{html.escape(self.prefix)}</b> {text}" if self.prefix else text
        print(msg.replace("<b>", "").replace("</b>", ""), flush=True)
        if not self.enabled:
            return False
        for k in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                  data={"chat_id": self.chat, "text": msg, "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"}, timeout=15)
                if r.ok:
                    return True
            except Exception:
                pass
            time.sleep(2 * (k + 1))
        return False


def esc(x):
    return html.escape(str(x))
