"""Cliente mínimo da API do orquestrador (sem dependências: roda em qualquer Python do container)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class AgenteApi:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 30):
        self.base_url = (base_url or os.environ.get("AGENTE_API_URL") or "http://agente:8080/api/v1").rstrip("/")
        self.token = token if token is not None else os.environ.get("JARVIS_API_TOKEN", "")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "null"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except (ValueError, AttributeError):
                detail = raw
            raise ApiError(e.code, str(detail)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ApiError(0, f"orquestrador inacessível em {self.base_url}: {e}") from None

    def get(self, path: str, **query: Any) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self.request("POST", path, body=body or {})
