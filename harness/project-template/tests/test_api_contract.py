"""Fuzz de contrato: gera requisições a partir do OpenAPI e reprova qualquer erro 500, resposta fora
do schema declarado ou status não documentado. Dono: test-engineer (template do harness).

A aplicação vem de `backend.asgi` no `.harness/stack.json` (padrão `app.main:app`) e o schema de
`backend.openapi_path` (padrão `/openapi.json`)."""

import importlib
import json
import tempfile
from pathlib import Path

import pytest
import schemathesis

ROOT = Path(__file__).resolve().parents[1]
try:
    BACKEND = (
        json.loads((ROOT / ".harness" / "stack.json").read_text(encoding="utf-8")).get("backend")
        or {}
    )
except (OSError, ValueError):
    BACKEND = {}


def _load(ref: str):
    module, _, attr = ref.partition(":")
    obj = importlib.import_module(module)
    for part in filter(None, attr.split(".")):
        obj = getattr(obj, part)
    return obj


try:  # nunca toca dados reais
    _settings = _load(BACKEND.get("settings", "app.config:settings"))
    if hasattr(_settings, "data_dir"):
        _settings.data_dir = Path(tempfile.mkdtemp(prefix="fuzz-"))
except (ImportError, AttributeError):
    pass

app = _load(BACKEND.get("asgi", "app.main:app"))
schema = schemathesis.openapi.from_asgi(BACKEND.get("openapi_path", "/openapi.json"), app)


@pytest.mark.fuzz
@schema.parametrize()
def test_api_respeita_o_contrato(case):
    case.call_and_validate()
