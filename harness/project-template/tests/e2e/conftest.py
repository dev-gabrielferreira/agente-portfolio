"""E2E com Playwright contra o app rodando de verdade (processo separado).

O comando vem de `start` no manifesto `.harness/stack.json` (com `{port}`), e a espera usa `health`.
Fixtures: `live_server` (URL base, com DATA_DIR temporário) e `page` (do pytest-playwright).
Todos os testes desta pasta recebem o marcador `e2e` automaticamente.
"""

import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
DEFAULT_START = "python -m uvicorn app.main:app --host 127.0.0.1 --port {port}"


def pytest_collection_modifyitems(items):
    for item in items:
        if Path(str(item.fspath)).is_relative_to(HERE):
            item.add_marker(pytest.mark.e2e)


def _stack() -> dict:
    try:
        data = json.loads((ROOT / ".harness" / "stack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_command(port: int) -> list[str]:
    cmd = shlex.split((_stack().get("start") or DEFAULT_START).replace("{port}", str(port)))
    if cmd and cmd[0] in {"python", "python3"}:
        cmd[0] = sys.executable  # o Python do venv do projeto
    return cmd


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("data")
    env = {**os.environ, "DATA_DIR": str(data_dir), "APP_ENV": "test", "PORT": str(port)}
    proc = subprocess.Popen(
        start_command(port), cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
    )
    url = f"http://127.0.0.1:{port}"
    health = _stack().get("health") or "/health"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            cmd = " ".join(start_command(port))
            pytest.fail(f"o app saiu com código {proc.returncode} antes de responder ({cmd})")
        try:
            if urllib.request.urlopen(f"{url}{health}", timeout=1).status == 200:
                break
        except OSError:
            time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail(
            f"o app não respondeu {health} em 60 s (comando `start` do .harness/stack.json)"
        )
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
