"""Fixtures compartilhadas por todas as suítes. Dono: test-engineer.

A arquitetura vem do manifesto `.harness/stack.json` (escrito no plano técnico): `backend.asgi`
aponta a aplicação ASGI ("modulo:objeto") e `backend.settings` o objeto de configuração, cujo
`data_dir` é trocado por uma pasta temporária — cada teste começa com dados limpos. Sem manifesto,
vale o padrão `app.main:app` / `app.config:settings`.

Se o projeto não for ASGI (ex.: WSGI, CLI, pipeline de dados), reescreva estas fixtures para a
arquitetura do plano. Fixtures só dos testes de unidade do builder ficam em tests/unit/conftest.py.
"""

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def stack() -> dict:
    try:
        data = json.loads((ROOT / ".harness" / "stack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(ref: str):
    """Importa 'pacote.modulo:atributo.sub'."""
    module, _, attr = ref.partition(":")
    obj = importlib.import_module(module)
    for part in filter(None, attr.split(".")):
        obj = getattr(obj, part)
    return obj


def backend() -> dict:
    return stack().get("backend") or {}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Pasta de dados isolada: variável DATA_DIR e, se existir, settings.data_dir."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        settings = load(backend().get("settings", "app.config:settings"))
    except (ImportError, AttributeError):
        settings = None
    if settings is not None and hasattr(settings, "data_dir"):
        monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def client(data_dir):
    from starlette.testclient import TestClient

    app = load(backend().get("asgi", "app.main:app"))
    with TestClient(app) as c:
        yield c
