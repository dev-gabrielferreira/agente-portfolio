"""Segundo fator humano para ações de produção pedidas pelo Jarvis (RFC 6238, sem dependências).

O Jarvis é um modelo: pode ser enganado por prompt injection ou simplesmente errar. Por isso aprovar
deploy em produção e fazer rollback pela API exigem um código de 6 dígitos do app autenticador do
Gabriel (Google Authenticator, Aegis, 1Password…). Quem confere é o orquestrador; o modelo nunca vê
o segredo. Cada código vale uma vez só (anti-replay) e tentativas erradas em sequência bloqueiam a API
de aprovação por alguns minutos.

    python -m orchestrator.totp novo        # gera o segredo e o link otpauth:// para o app
    python -m orchestrator.totp testar      # confere um código digitado (sem gastar o anti-replay)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
WINDOW = 1  # aceita o código anterior e o seguinte (relógio do celular adiantado/atrasado)
MAX_FAILURES = 5
LOCK_S = 600


class KV(Protocol):
    def kv_get(self, key: str, default: str = "") -> str: ...
    def kv_set(self, key: str, value: str) -> None: ...


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    clean = secret.strip().replace(" ", "").upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def code_at(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def counter_now(ts: float | None = None) -> int:
    return int((time.time() if ts is None else ts) // STEP_S)


def uri(secret: str, account: str = "gabriel", issuer: str = "Agente Portfolio") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_S}"


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


class TotpGuard:
    """Verifica códigos com anti-replay e bloqueio por tentativas, guardando estado no banco."""

    def __init__(self, secret: str, store: KV, clock=time.time):
        self.secret = secret.strip()
        self.store = store
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self.secret)

    def verify(self, code: str, purpose: str) -> Verdict:
        if not self.enabled:
            return Verdict(
                False, "segundo fator não configurado: defina ADMIN_TOTP_SECRET (python -m orchestrator.totp novo)"
            )
        now = self.clock()
        locked_until = float(self.store.kv_get("totp:locked_until", "0") or 0)
        if now < locked_until:
            return Verdict(False, f"muitas tentativas erradas; tente de novo em {int(locked_until - now)}s")
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        current = counter_now(now)
        last_used = int(self.store.kv_get("totp:last_counter", "-1") or -1)
        match = None
        if len(digits) == DIGITS:
            for delta in range(-WINDOW, WINDOW + 1):
                if hmac.compare_digest(code_at(self.secret, current + delta), digits):
                    match = current + delta
                    break
        if match is None:
            failures = int(self.store.kv_get("totp:failures", "0") or 0) + 1
            if failures >= MAX_FAILURES:
                self.store.kv_set("totp:locked_until", str(now + LOCK_S))
                failures = 0
            self.store.kv_set("totp:failures", str(failures))
            return Verdict(False, "código inválido ou expirado")
        if match <= last_used:
            return Verdict(False, "código já usado; espere o próximo")
        self.store.kv_set("totp:last_counter", str(match))
        self.store.kv_set("totp:failures", "0")
        self.store.kv_set("totp:last_purpose", purpose[:200])
        return Verdict(True)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "ajuda"
    if cmd == "novo":
        secret = new_secret()
        print("Segredo (guarde no .env do VPS como ADMIN_TOTP_SECRET):\n")
        print(f"  ADMIN_TOTP_SECRET={secret}\n")
        print("No app autenticador, escolha 'inserir chave de configuração' e cole o segredo acima,")
        print("ou gere um QR code a partir deste link (ex.: qrencode -t ansiutf8 '<link>'):\n")
        print(f"  {uri(secret)}\n")
        print("Depois reinicie o container do agente para ele ler a variável.")
        return 0
    if cmd == "testar":
        secret = os.environ.get("ADMIN_TOTP_SECRET", "")
        if not secret:
            print("ADMIN_TOTP_SECRET não está definido neste ambiente.")
            return 1
        code = argv[1] if len(argv) > 1 else input("código de 6 dígitos: ")
        current = counter_now()
        ok = any(code_at(secret, current + d) == code.strip() for d in range(-WINDOW, WINDOW + 1))
        print("código válido" if ok else "código inválido (confira o relógio do celular e do servidor)")
        return 0 if ok else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
