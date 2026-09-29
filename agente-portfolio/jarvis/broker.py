"""Broker de sessões do laboratório (roda como root dentro do container do Jarvis).

O Jarvis (usuário `jarvis`) tem o token da API do orquestrador; as sessões do laboratório não podem
ter. Sessões do mesmo usuário leem o ambiente umas das outras (/proc/<pid>/environ), então a
separação precisa ser por usuário: o Jarvis pede, por um socket Unix que só o grupo `jarvis`
alcança, e o broker executa `claude` como o usuário `lab`, com ambiente limpo e argumentos
validados (jarvis/sessions.py).

Protocolo: uma linha JSON por conexão, resposta em uma linha JSON.
    {"op": "list", "todas": false}
    {"op": "create", "tarefa": "...", "nome": "...", "agente": "", "modelo": "", "projeto": ""}
    {"op": "logs", "id": "...", "tamanho": 6000}
    {"op": "stop", "id": "..."}
    {"op": "message", "id": "...", "texto": "..."}
"""

from __future__ import annotations

import grp
import json
import os
import shutil
import socket
import socketserver
import sys
from pathlib import Path
from typing import Any

from jarvis.sessions import SessionError, Sessions

SOCKET = Path(os.environ.get("JARVIS_BROKER_SOCKET", "/run/jarvis/broker.sock"))
LAB_USER = os.environ.get("JARVIS_LAB_USER", "lab")
LAB_HOME = os.environ.get("JARVIS_LAB_HOME", "/srv/jarvis/lab-home")
TOKEN_FILE = Path(os.environ.get("JARVIS_LAB_TOKEN_FILE", "/srv/jarvis/secrets/lab-token"))
MAX_REQUEST = 64_000


def lab_sessions() -> Sessions:
    """Sessões executadas como `lab`, com ambiente mínimo (sem nada do Jarvis)."""
    env = {
        "HOME": LAB_HOME,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": os.environ.get("TZ", "UTC"),
        "DISABLE_AUTOUPDATER": "1",
        "RTK_TELEMETRY_DISABLED": "1",
    }
    try:  # token só de modelo (claude setup-token): sessões em segundo plano não precisam de login completo
        token = TOKEN_FILE.read_text().strip()
    except OSError:
        token = ""
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    runuser = shutil.which("runuser") or "/usr/sbin/runuser"
    prefix = [runuser, "-u", LAB_USER, "--", "env", "-i", *[f"{k}={v}" for k, v in env.items()]]
    return Sessions(prefix=prefix, env={"PATH": env["PATH"] + ":/usr/sbin:/sbin"}, timeout=90)


def handle(request: dict[str, Any], sessions: Sessions) -> Any:
    op = request.get("op")
    if op == "list":
        return sessions.list(include_done=bool(request.get("todas")))
    if op == "create":
        return sessions.create(
            str(request.get("tarefa") or ""),
            str(request.get("nome") or ""),
            agent=str(request.get("agente") or ""),
            model=str(request.get("modelo") or ""),
            project=str(request.get("projeto") or ""),
        )
    if op == "logs":
        return sessions.logs(str(request.get("id") or ""), int(request.get("tamanho") or 6000))
    if op == "stop":
        return sessions.stop(str(request.get("id") or ""))
    if op == "message":
        return sessions.message(str(request.get("id") or ""), str(request.get("texto") or ""))
    raise SessionError(f"operação desconhecida: {op}")


class Handler(socketserver.StreamRequestHandler):
    factory = staticmethod(lab_sessions)

    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST)
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("pedido deve ser um objeto JSON")
            response = {"ok": True, "result": handle(request, self.factory())}
        except (ValueError, SessionError) as e:
            response = {"ok": False, "error": str(e)}
        except Exception as e:  # nunca derruba o broker
            response = {"ok": False, "error": f"erro interno: {e}"}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve(path: Path = SOCKET, group: str = "jarvis") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = Server(str(path), Handler)
    gid = grp.getgrnam(group).gr_gid if os.geteuid() == 0 else -1
    os.chown(path.parent, 0 if gid >= 0 else -1, gid)
    os.chmod(path.parent, 0o750)
    os.chown(path, -1, gid)
    os.chmod(path, 0o660)
    server.serve_forever()


class BrokerClient:
    """Lado do Jarvis (servidor MCP): mesma interface de Sessions, via socket."""

    def __init__(self, path: Path | str = SOCKET, timeout: float = 120):
        self.path = str(path)
        self.timeout = timeout

    def _call(self, **request: Any) -> Any:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.path)
                sock.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode())
                data = b""
                while not data.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except OSError as e:
            raise SessionError(f"broker de sessões indisponível ({e}); veja `docker logs jarvis`") from None
        try:
            response = json.loads(data)
        except ValueError:
            raise SessionError("resposta inválida do broker de sessões") from None
        if not response.get("ok"):
            raise SessionError(response.get("error") or "falha no broker")
        return response.get("result")

    def list(self, include_done: bool = False) -> Any:
        return self._call(op="list", todas=include_done)

    def create(self, task: str, name: str, agent: str = "", model: str = "", project: str = "") -> Any:
        return self._call(op="create", tarefa=task, nome=name, agente=agent, modelo=model, projeto=project)

    def logs(self, session_id: str, limit: int = 6000) -> Any:
        return self._call(op="logs", id=session_id, tamanho=limit)

    def stop(self, session_id: str) -> Any:
        return self._call(op="stop", id=session_id)

    def message(self, session_id: str, text: str) -> Any:
        return self._call(op="message", id=session_id, texto=text)


if __name__ == "__main__":
    print(f"broker de sessões em {SOCKET} (usuário {LAB_USER})", flush=True)
    serve()
    sys.exit(0)
