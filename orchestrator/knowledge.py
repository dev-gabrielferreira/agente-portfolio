"""Base de conhecimento dos projetos: uma nota Markdown por projeto + um índice.

É o "segundo cérebro" que o Jarvis consulta (e que você pode abrir no Obsidian): em vez de carregar
banco, specs e históricos inteiros no contexto, ele lê o índice, abre a nota do projeto e só então vai
ao código. As notas são geradas pelo orquestrador a partir de fontes verificáveis (banco, SPEC.md,
ADRs, PROGRESS.md, features.json) e reescritas quando um job termina ou para — nunca editadas à mão
(notas suas ficam na pasta do Jarvis).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from orchestrator import safefs
from orchestrator.config import Config
from orchestrator.db import DB

log = logging.getLogger(__name__)

STATUS = {
    "draft": "rascunho",
    "building": "em construção",
    "live": "no ar",
    "down": "fora do ar",
    "archived": "arquivado",
}


def _read(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _section(markdown: str, *titles: str, limit: int = 1500) -> str:
    """Primeira seção cujo título (##) contém um dos termos, sem o título."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and any(t.lower() in line.lower() for t in titles):
            level = len(line) - len(line.lstrip("#"))
            body = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith("#") and len(nxt) - len(nxt.lstrip("#")) <= level:
                    break
                body.append(nxt)
            return "\n".join(body).strip()[:limit]
    return ""


def _adrs(pdir: Path) -> list[str]:
    out = []
    folder = pdir / "docs" / "adr"
    if folder.is_dir() and not folder.is_symlink() and not (pdir / "docs").is_symlink():
        for f in sorted(folder.glob("*.md")):
            rel = f.relative_to(pdir).as_posix()
            first = next((ln for ln in safefs.read_text(pdir, rel, 2000).splitlines() if ln.startswith("#")), f.stem)
            out.append(f"- `{rel}` — {first.lstrip('# ').strip()}")
    decisions = safefs.read_text(pdir, "docs/DECISIONS.md", 8000)
    for line in decisions.splitlines():
        if line.startswith("## "):
            out.append(f"- `docs/DECISIONS.md` — {line[3:].strip()}")
    return out[:30]


def _features(pdir: Path) -> tuple[int, int, list[str]]:
    try:
        data = json.loads(safefs.read_text(pdir, ".harness/features.json"))
        feats = data.get("features") or []
    except (ValueError, AttributeError):
        return 0, 0, []
    done = sum(1 for f in feats if f.get("passes"))
    pending = [f"{f.get('id')}: {f.get('title')}" for f in feats if not f.get("passes")]
    return len(feats), done, pending[:10]


def _stack_manifest(pdir: Path) -> dict[str, Any]:
    try:
        data = json.loads(safefs.read_text(pdir, ".harness/stack.json"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


class KnowledgeBase:
    def __init__(self, config: Config, db: DB):
        self.c = config
        self.db = db

    @property
    def root(self) -> Path:
        return self.c.knowledge_dir

    def note_path(self, slug: str) -> Path:
        return self.root / "projetos" / f"{slug}.md"

    def note(self, slug: str) -> str:
        return _read(self.note_path(slug), 60000)

    def refresh_all(self) -> int:
        projects = self.db.projects()
        for p in projects:
            self.refresh_project(p["id"], index=False)
        self.write_index()
        return len(projects)

    def refresh_project(self, project_id: int, health: dict | None = None, index: bool = True) -> Path | None:
        project = self.db.project(project_id)
        if not project:
            return None
        try:
            text = self.render_project(project, health)
            path = self.note_path(project["slug"])
            _write_atomic(path, text)
            if index:
                self.write_index()
            return path
        except OSError:
            log.exception("não consegui escrever a nota do projeto %s", project.get("slug"))
            return None

    def render_project(self, project: dict[str, Any], health: dict | None = None) -> str:
        slug = project["slug"]
        pdir = self.c.projects_dir / slug
        spec = safefs.read_text(pdir, "SPEC.md", 40000)
        stack = _stack_manifest(pdir)
        total, done, pending = _features(pdir)
        jobs = self.db.jobs(project["id"], limit=8)
        progress = safefs.read_text(pdir, ".harness/PROGRESS.md", 40000)
        tags = ", ".join(project.get("tags") or []) or "—"
        lines = [
            "---",
            f"projeto: {slug}",
            f"status: {project['status']}",
            f"atualizado: {project['updated_at']}",
            f"tags: [{tags}]",
            "fonte: gerado pelo orquestrador (não edite; notas suas vão para a pasta do Jarvis)",
            "---",
            "",
            f"# {project['name']}",
            "",
            project.get("description") or "(sem descrição)",
            "",
            "## Onde está",
            "",
            f"- Status: **{STATUS.get(project['status'], project['status'])}**",
            f"- Produção: {self.c.url(slug, 'production')} (versão `{project.get('prod_tag') or '—'}`)",
            f"- Staging: {self.c.url(slug, 'staging')} (versão `{project.get('staging_tag') or '—'}`)",
            f"- Repositório: {project.get('repo_url') or '(ainda não publicado)'}",
            f"- Código no VPS: `{pdir}`",
            f"- Stack: {project.get('stack') or '—'} · tags: {tags}",
        ]
        if health:
            ok = "respondendo" if health.get("ok") else f"FALHANDO ({health.get('error', '')})"
            lines.append(f"- Saúde (monitor): {ok}")
        if stack:
            lines += ["", "## Arquitetura (manifesto `.harness/stack.json`)", "", "```json"]
            lines += [json.dumps(stack, ensure_ascii=False, indent=2)[:3000], "```"]
        summary = _section(spec, "resumo", "visão", "objetivo", "problema") or spec[:1200]
        if summary:
            lines += ["", "## O que é (da SPEC.md)", "", summary.strip()]
        adrs = _adrs(pdir)
        if adrs:
            lines += ["", "## Decisões registradas", "", *adrs]
        if total:
            lines += ["", f"## Contrato de aceite: {done}/{total} features passando"]
            if pending:
                lines += ["", "Pendentes:", *[f"- {p}" for p in pending]]
        tail = _section(progress, "armadilha", "pitfall", "próximos", "pendente", limit=1200)
        if tail:
            lines += ["", "## Armadilhas e pendências (do PROGRESS.md)", "", tail]
        if jobs:
            lines += ["", "## Últimos jobs", ""]
            for j in jobs:
                extra = f" — esperando: {j['waiting_for']}" if j["status"] == "waiting" else ""
                req = re.sub(r"\s+", " ", j["request"])[:140]
                lines.append(
                    f"- #{j['id']} {j['type']} · {j['status']} ({j['phase']}){extra} · {j['created_at']}: {req}"
                )
        return "\n".join(lines).rstrip() + "\n"

    def write_index(self) -> Path:
        projects = self.db.projects()
        waiting = [j for j in self.db.jobs(limit=100) if j["status"] == "waiting"]
        lines = [
            "# Base de conhecimento do agente",
            "",
            "Índice gerado pelo orquestrador. Abra a nota do projeto antes de ir ao código.",
            "",
            "## Projetos",
            "",
        ]
        for p in projects:
            lines.append(
                f"- [[projetos/{p['slug']}|{p['name']}]] — {STATUS.get(p['status'], p['status'])}"
                f" · {p.get('stack') or 'stack a definir'} · {self.c.url(p['slug'], 'production')}"
            )
        if not projects:
            lines.append("- (nenhum projeto ainda)")
        lines += ["", "## Esperando o Gabriel", ""]
        by_id = {p["id"]: p for p in projects}
        for j in waiting:
            name = by_id.get(j["project_id"], {}).get("name", "?")
            lines.append(f"- job #{j['id']} ({name}): {j['waiting_for']} — {j.get('message') or ''}")
        if not waiting:
            lines.append("- nada pendente")
        path = self.root / "INDEX.md"
        _write_atomic(path, "\n".join(lines) + "\n")
        return path
