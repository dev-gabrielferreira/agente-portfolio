"""Manifesto da arquitetura de cada projeto (`.harness/stack.json`) e os starters opcionais.

Não existe mais "o template" do projeto: o planner decide a arquitetura no plano técnico (com ADRs)
e declara no manifesto o que os sensores precisam saber para funcionar em qualquer escolha:

    start     comando que sobe o app localmente, com {port}  (e2e, avaliador)
    health    rota de saúde                                   (e2e, deploy)
    backend   Python: pacote, app ASGI, settings, openapi,     (cobertura, fixtures, fuzz de contrato,
              domain (pasta da regra de negócio)              mutation testing)
    frontend  pasta com package.json e comandos               (lint, tipos, testes e build no gate)
    ui_paths  onde mora a interface                           (detector de anti-padrões de design)
    starter   ponto de partida opcional copiado antes do build (python-fastapi, fastapi-react, nenhum)

O contrato da plataforma continua fixo (Dockerfile, porta 8000, /health, /data): é o que o deploy
exige, não uma escolha de stack.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from orchestrator import safefs

MANIFEST = ".harness/stack.json"
STARTER_MARKER = ".harness/.starter-applied"
REF_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_][\w.]*$")
REL_RE = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[\w.@/-]+$")
PACKAGE_MANAGERS = {"npm", "pnpm"}
SKIP_PARTS = {"node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", ".venv", "dist"}


def starters(harness_dir: Path) -> dict[str, Path]:
    root = harness_dir / "starters"
    if not root.is_dir():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if (p / "stack.json").is_file()}


def load(pdir: Path) -> dict[str, Any]:
    try:
        data = json.loads(safefs.read_text(pdir, MANIFEST, default="{}"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def validate(data: dict[str, Any], known_starters: set[str] | None = None) -> list[str]:
    """Problemas do manifesto (lista vazia = válido)."""
    if not data:
        return [f"{MANIFEST} ausente ou não é um objeto JSON"]
    problems: list[str] = []
    if data.get("version") != 1:
        problems.append("version deve ser 1")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        problems.append("summary (a stack em uma linha) é obrigatório")
    start = data.get("start")
    if not isinstance(start, str) or "{port}" not in start:
        problems.append(
            "start deve ser o comando que sobe o app, com {port} (ex.: python -m uvicorn app.main:app --port {port})"
        )
    health = data.get("health", "/health")
    if not isinstance(health, str) or not health.startswith("/"):
        problems.append("health deve ser uma rota começando com /")
    starter = data.get("starter")
    if starter not in (None, "", "nenhum") and known_starters is not None and starter not in known_starters:
        problems.append(f"starter '{starter}' não existe (opções: {', '.join(sorted(known_starters)) or '—'}, nenhum)")

    backend = data.get("backend")
    if backend is not None:
        if not isinstance(backend, dict):
            problems.append("backend deve ser objeto ou null")
        else:
            if backend.get("language") != "python":
                problems.append("backend.language deve ser 'python' (os gates de teste, tipos e mutation são Python)")
            package = backend.get("package")
            if package is not None and (not isinstance(package, str) or not REL_RE.match(package)):
                problems.append("backend.package deve ser o caminho relativo do pacote (ex.: app)")
            for key in ("asgi", "settings"):
                ref = backend.get(key)
                if ref is not None and (not isinstance(ref, str) or not REF_RE.match(ref)):
                    problems.append(f"backend.{key} deve ter o formato 'modulo:objeto'")
            domain = backend.get("domain")
            if domain is not None and (not isinstance(domain, str) or not REL_RE.match(domain)):
                problems.append(
                    "backend.domain deve ser a pasta relativa da regra de negócio (alvo do mutation testing)"
                )
            if backend.get("openapi") and not backend.get("asgi"):
                problems.append("backend.openapi exige backend.asgi (o fuzz de contrato importa o app)")

    frontend = data.get("frontend")
    if frontend is not None:
        if not isinstance(frontend, dict):
            problems.append("frontend deve ser objeto ou null")
        else:
            fdir = frontend.get("dir")
            if not isinstance(fdir, str) or not REL_RE.match(fdir):
                problems.append("frontend.dir deve ser uma pasta relativa com package.json (ex.: frontend)")
            if frontend.get("package_manager", "npm") not in PACKAGE_MANAGERS:
                problems.append("frontend.package_manager deve ser npm ou pnpm")
            for key in ("lint", "typecheck", "test", "build"):
                value = frontend.get(key)
                if value is not None and (not isinstance(value, str) or "\n" in value):
                    problems.append(f"frontend.{key} deve ser um comando de uma linha")

    ui_paths = data.get("ui_paths", [])
    if not isinstance(ui_paths, list) or not all(isinstance(p, str) and REL_RE.match(p) for p in ui_paths):
        problems.append("ui_paths deve ser uma lista de pastas relativas")
    if backend is None and frontend is None:
        problems.append("declare ao menos backend ou frontend")
    return problems


def gate_keys(data: dict[str, Any]) -> dict[str, Any]:
    """A parte do manifesto que liga sensores. É travada quando o plano é validado: mudar isso depois
    (tirar o frontend, trocar o pacote ou a pasta de regra de negócio) desligaria gates em silêncio."""
    backend = data.get("backend") if isinstance(data.get("backend"), dict) else {}
    frontend = data.get("frontend") if isinstance(data.get("frontend"), dict) else None
    return {
        "backend.package": backend.get("package"),
        "backend.domain": backend.get("domain"),
        "backend.asgi": backend.get("asgi"),
        "backend.openapi": bool(backend.get("openapi")),
        "frontend.dir": frontend.get("dir") if frontend else None,
        "frontend.commands": sorted(k for k in ("lint", "typecheck", "test", "build") if frontend and frontend.get(k)),
        "ui_paths": sorted(p for p in (data.get("ui_paths") or []) if isinstance(p, str)),
    }


def weakened(locked: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Campos travados que foram removidos ou trocados (descrição legível de cada um)."""
    out = []
    for key in ("backend.package", "backend.domain", "backend.asgi", "frontend.dir"):
        if locked.get(key) and current.get(key) != locked[key]:
            out.append(f"{key}: '{locked[key]}' → '{current.get(key) or 'removido'}'")
    if locked.get("backend.openapi") and not current.get("backend.openapi"):
        out.append("backend.openapi: desligado")
    lost_cmds = sorted(set(locked.get("frontend.commands") or []) - set(current.get("frontend.commands") or []))
    if lost_cmds:
        out.append(f"frontend: comandos removidos ({', '.join(lost_cmds)})")
    lost_ui = sorted(set(locked.get("ui_paths") or []) - set(current.get("ui_paths") or []))
    if lost_ui:
        out.append(f"ui_paths: removidos ({', '.join(lost_ui)})")
    return out


def detect(pdir: Path) -> dict[str, Any]:
    """Manifesto inferido para projeto adotado ou legado (sem stack.json)."""
    data: dict[str, Any] = {"version": 1, "summary": "detectado pelo orquestrador", "health": "/health"}
    if (pdir / "app" / "main.py").is_file():
        data["backend"] = {
            "language": "python",
            "package": "app",
            "asgi": "app.main:app",
            "settings": "app.config:settings",
            "openapi": True,
        }
        data["start"] = "python -m uvicorn app.main:app --host 127.0.0.1 --port {port}"
    elif (pdir / "pyproject.toml").is_file():
        data["backend"] = {"language": "python"}
        data["start"] = "python -m app --port {port}"
    else:
        data["backend"] = None
        data["start"] = "python -m http.server {port} --bind 127.0.0.1"
    front = next((d for d in ("frontend", "web", "client", ".") if (pdir / d / "package.json").is_file()), None)
    data["frontend"] = (
        {"dir": front, "package_manager": "pnpm" if (pdir / front / "pnpm-lock.yaml").exists() else "npm"}
        if front
        else None
    )
    data["ui_paths"] = [p for p in ("app/templates", "app/static", "frontend/src", "src") if (pdir / p).is_dir()]
    return data


def apply_starter(pdir: Path, harness_dir: Path) -> list[str]:
    """Copia o starter escolhido no manifesto (só arquivos que ainda não existem, uma vez só).
    Devolve os arquivos copiados. O manifesto do projeto ganha os campos que faltarem do starter."""
    manifest = load(pdir)
    name = manifest.get("starter")
    if not name or name == "nenhum" or os.path.lexists(pdir / STARTER_MARKER):
        return []
    source = starters(harness_dir).get(name)
    if source is None:
        return []
    copied: list[str] = []
    for src in sorted(source.rglob("*")):
        rel = src.relative_to(source)
        if src.is_dir() or rel.as_posix() == "stack.json" or SKIP_PARTS & set(rel.parts):
            continue
        if os.path.lexists(pdir / rel):  # nunca sobrescreve (nem segue link plantado)
            continue
        try:
            safefs.copy_file(src, pdir, rel)
        except safefs.UnsafePath:
            continue
        copied.append(rel.as_posix())
    defaults = json.loads((source / "stack.json").read_text(encoding="utf-8"))
    merged = {**defaults, **{k: v for k, v in manifest.items() if v is not None or k not in defaults}}
    for key in ("backend", "frontend"):
        if isinstance(defaults.get(key), dict) and isinstance(manifest.get(key), dict):
            merged[key] = {**defaults[key], **manifest[key]}
    safefs.write_text(pdir, MANIFEST, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    safefs.write_text(pdir, STARTER_MARKER, name + "\n")
    return copied
