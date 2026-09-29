"""Notificações para o celular. Compatível com ntfy (https://ntfy.sh): um POST com texto puro.

Configure NOTIFY_URL=https://ntfy.sh/<um-tópico-difícil-de-adivinhar> e instale o app ntfy.
"""

from __future__ import annotations

import logging
import unicodedata

import httpx

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, url: str, panel_url: str = ""):
        self.url = url
        self.panel_url = panel_url
        self.sent: list[tuple[str, str]] = []

    async def send(self, title: str, message: str, path: str = "", priority: str = "default") -> None:
        self.sent.append((title, message))
        if not self.url:
            return
        ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
        headers = {"Title": ascii_title, "Priority": priority}
        if path and self.panel_url:
            headers["Click"] = self.panel_url.rstrip("/") + path
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.url, content=message.encode("utf-8"), headers=headers)
        except httpx.HTTPError as e:  # notificação nunca derruba o pipeline
            log.warning("falha ao notificar: %s", e)
