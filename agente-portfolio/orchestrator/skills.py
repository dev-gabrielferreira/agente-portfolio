"""Biblioteca do harness: skills, plugins e MCPs escolhidos por papel e por stack do projeto.

    python -m orchestrator.skills sync      # baixa as fontes externas fixadas no catálogo
    python -m orchestrator.skills status    # mostra o que está disponível
    python -m orchestrator.skills mcp-setup # registra MCPs de escopo "user" no usuário agent

Cada projeto recebe em `.claude/skills/` a união das skills úteis para a stack dele; cada subagente
recebe no frontmatter `skills:` só as que deve carregar por inteiro no início; cada TASK.md lista
as skills recomendadas para aquele papel. Assim o contexto fica enxuto e específico (Ashby: o
regulador precisa ter a variedade do sistema que regula — nem menos, nem ruído a mais).
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROLES = ("planner", "designer", "builder", "tester", "evaluator", "security", "reviewer", "retro")
TAGS = ("ui", "react", "api", "data", "llm", "auth", "realtime", "scheduler", "mcp", "charts", "figma")

# arquivo do subagente -> papel
AGENT_ROLE = {
    "planner": "planner",
    "designer": "designer",
    "builder": "builder",
    "test-engineer": "tester",
    "evaluator": "evaluator",
    "security-reviewer": "security",
    "code-reviewer": "reviewer",
}


@dataclass
class Item:
    name: str
    source: str
    path: str
    roles: list[str]
    tags: list[str] = field(default_factory=lambda: ["all"])
    preload: list[str] = field(default_factory=list)
    toggle: str = ""
    kind: str = "skill"

    def matches(self, role: str, tags: set[str]) -> bool:
        return role in self.roles and ("all" in self.tags or bool(tags & set(self.tags)))


@dataclass
class Mcp:
    name: str
    scope: str
    roles: list[str]
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    requires: str = ""
    deny: dict[str, list[str]] = field(default_factory=dict)
    tags: list[str] = field(default_factory=lambda: ["all"])

    def fits(self, tags: set[str] | None) -> bool:
        """Sem tags informadas (chamada antiga) vale o papel; com tags, a stack precisa casar."""
        return tags is None or "all" in self.tags or bool(tags & set(self.tags))


def skill_description(skill_dir: Path) -> str:
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.+?)\s*$", text.split("---")[1] if text.startswith("---") else "", re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


class Library:
    def __init__(self, harness_dir: Path, vendor_dir: Path, env: dict[str, str] | None = None):
        self.harness_dir = harness_dir
        self.vendor_dir = vendor_dir
        self.env = env or {}
        data = tomllib.loads((harness_dir / "catalog.toml").read_text(encoding="utf-8"))
        self.sources: dict[str, dict[str, Any]] = data["sources"]
        self.skills = [Item(**s) for s in data.get("skills", [])]
        self.plugins = [Item(**p, kind="plugin") for p in data.get("plugins", [])]
        self.mcps = [Mcp(name=name, **cfg) for name, cfg in data.get("mcp", {}).items()]
        self._validate()

    def _validate(self) -> None:
        names = [s.name for s in self.skills]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"skills duplicadas no catálogo: {dupes}")
        for item in [*self.skills, *self.plugins]:
            if item.source not in self.sources:
                raise ValueError(f"{item.name}: fonte desconhecida {item.source}")
            bad_roles = set(item.roles) - set(ROLES)
            bad_tags = set(item.tags) - set(TAGS) - {"all"}
            if bad_roles or bad_tags:
                raise ValueError(f"{item.name}: papel/tag inválido {bad_roles | bad_tags}")
        for m in self.mcps:
            bad = (set(m.roles) - set(ROLES)) | (set(m.tags) - set(TAGS) - {"all"})
            if bad:
                raise ValueError(f"MCP {m.name}: papel/tag inválido {bad}")

    # --------------------------------------------------------------------------- caminhos
    def source_root(self, source: str) -> Path:
        cfg = self.sources[source]
        if source == "local":
            return self.harness_dir / cfg.get("path", ".")
        return self.vendor_dir / f"{source}@{cfg['ref'][:12]}"

    def item_dir(self, item: Item) -> Path:
        return self.source_root(item.source) / item.path

    def available(self, item: Item) -> bool:
        d = self.item_dir(item)
        if item.kind == "plugin":
            return (d / ".claude-plugin" / "plugin.json").is_file()
        return (d / "SKILL.md").is_file()

    # --------------------------------------------------------------------------- seleção
    def project_tags(self, tags: list[str] | set[str] | None) -> set[str]:
        out = {t for t in (tags or []) if t in TAGS}
        if self.env.get("FIGMA_ENABLED", "").lower() in {"1", "true", "yes", "sim"} and "ui" in out:
            out.add("figma")
        return out

    def skills_for(self, role: str, tags: set[str]) -> list[Item]:
        return [s for s in self.skills if s.matches(role, tags) and self.available(s)]

    def preload_for(self, role: str, tags: set[str]) -> list[str]:
        return [s.name for s in self.skills_for(role, tags) if role in s.preload]

    def plugins_for(self, role: str) -> list[Path]:
        out = []
        for p in self.plugins:
            if role not in p.roles or not self.available(p):
                continue
            if p.toggle and self.env.get(p.toggle, "true").lower() in {"0", "false", "no", "nao", "não"}:
                continue
            out.append(self.item_dir(p))
        return out

    def brief(self, role: str, tags: set[str]) -> str:
        """Lista para o TASK.md: feedforward explícito, não depende de o modelo descobrir sozinho."""
        items = self.skills_for(role, tags)
        if not items:
            return "(nenhuma)"
        preload = set(self.preload_for(role, tags))
        lines = []
        for s in items:
            mark = " (já carregada)" if s.name in preload else ""
            desc = skill_description(self.item_dir(s))[:220]
            lines.append(f"- `{s.name}`{mark} — {desc}")
        return "\n".join(lines)

    # --------------------------------------------------------------------------- MCP
    def mcp_enabled(self, m: Mcp) -> bool:
        return not m.requires or self.env.get(m.requires, "").lower() in {"1", "true", "yes", "sim"}

    def mcp_config(self, role: str, chromium: str = "", tags: set[str] | None = None) -> dict[str, Any]:
        servers = {}
        for m in self.mcps:
            if m.scope != "session" or role not in m.roles or not self.mcp_enabled(m) or not m.fits(tags):
                continue
            args = [a.replace("{chromium}", chromium) for a in m.args]
            if "{chromium}" in " ".join(m.args) and not chromium:
                continue
            servers[m.name] = {"command": m.command, "args": args}
        return {"mcpServers": servers}

    def mcp_disallowed(self, role: str, project_dir: Path | None = None, tags: set[str] | None = None) -> list[str]:
        """Bloqueia servidores que o papel não deve usar (inclusive um .mcp.json do projeto) e
        ferramentas específicas (ex.: escrita no Figma para o builder)."""
        denied = []
        for m in self.mcps:
            if role not in m.roles or not self.mcp_enabled(m) or not m.fits(tags):
                denied.append(f"mcp__{m.name}")
            else:
                denied += [f"mcp__{m.name}__{tool}" for tool in m.deny.get(role, [])]
        if project_dir and (project_dir / ".mcp.json").is_file():
            try:
                extra = json.loads((project_dir / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {})
                denied += [f"mcp__{name}" for name in extra]
            except (ValueError, OSError):
                pass
        return sorted(set(denied))

    # --------------------------------------------------------------------------- instalação no projeto
    def install(self, project_dir: Path, tags: set[str]) -> dict[str, list[str]]:
        """Copia para o projeto as skills úteis a qualquer papel nesta stack e grava o preload de
        cada subagente. Retorna o manifesto papel -> skills."""
        dest = project_dir / ".claude" / "skills"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        manifest: dict[str, list[str]] = {}
        installed: set[str] = set()
        for role in ROLES:
            items = self.skills_for(role, tags)
            manifest[role] = [s.name for s in items]
            for s in items:
                if s.name in installed:
                    continue
                shutil.copytree(self.item_dir(s), dest / s.name, ignore=shutil.ignore_patterns(".git", "*.zip"))
                if s.source != "local":
                    src = self.sources[s.source]
                    (dest / s.name / "NOTICE.agente.md").write_text(
                        f"Origem: https://github.com/{src['repo']}/tree/{src['ref']}/{s.path}\n"
                        f"Licença: {src.get('license', 'ver repositório de origem')}\n"
                        "Copiado sem alterações pelo agente de portfólio.\n",
                        encoding="utf-8",
                    )
                installed.add(s.name)
        # skills de terceiros não vão para o repositório público do projeto (licenças e ruído):
        # ficam fora do git e são reinstaladas pelo orquestrador a cada job
        vendored = sorted(s.name for s in self.skills if s.name in installed and s.source != "local")
        (dest / ".gitignore").write_text(
            "# skills de terceiros instaladas pelo agente (origem e licença em NOTICE.agente.md)\n"
            + "".join(f"/{name}/\n" for name in vendored),
            encoding="utf-8",
        )
        self._render_agents(project_dir, tags)
        return manifest

    def _render_agents(self, project_dir: Path, tags: set[str]) -> None:
        agents = project_dir / ".claude" / "agents"
        for f in agents.glob("*.md"):
            role = AGENT_ROLE.get(f.stem)
            text = f.read_text(encoding="utf-8")
            names = self.preload_for(role, tags) if role else []
            line = f"skills: {', '.join(names)}\n" if names else ""
            text = re.sub(r"^skills: \{\{SKILLS\}\}\n", line, text, count=1, flags=re.M)
            f.write_text(text, encoding="utf-8")

    # --------------------------------------------------------------------------- download das fontes
    def sync(self, client: httpx.Client | None = None) -> dict[str, str]:
        """Baixa cada fonte externa no commit fixado (tarball do GitHub). Idempotente."""
        status = {}
        own = client is None
        client = client or httpx.Client(timeout=120, follow_redirects=True)
        try:
            for name, cfg in self.sources.items():
                if name == "local":
                    continue
                target = self.source_root(name)
                if target.is_dir() and any(target.iterdir()):
                    status[name] = "ok (cache)"
                    continue
                try:
                    _git_fetch(cfg["repo"], cfg["ref"], target)
                    status[name] = "baixado (git)"
                    continue
                except (OSError, subprocess.SubprocessError) as e:
                    git_error = str(e)[:200]
                url = f"https://codeload.github.com/{cfg['repo']}/tar.gz/{cfg['ref']}"
                try:
                    r = client.get(url)
                    r.raise_for_status()
                    _extract(r.content, target)
                    status[name] = "baixado (tarball)"
                except (httpx.HTTPError, tarfile.TarError, OSError) as e:
                    status[name] = f"falhou: git ({git_error}); tarball ({e})"
        finally:
            if own:
                client.close()
        return status

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for item in [*self.skills, *self.plugins]:
            rows.append(
                {
                    "name": item.name,
                    "kind": item.kind,
                    "source": item.source,
                    "license": self.sources[item.source].get("license", "próprio"),
                    "roles": item.roles,
                    "tags": item.tags,
                    "preload": item.preload,
                    "available": self.available(item),
                    "description": "" if item.kind == "plugin" else skill_description(self.item_dir(item)),
                }
            )
        return rows


def _git_fetch(repo: str, ref: str, target: Path) -> None:
    """Busca exatamente o commit fixado (raso) e remove o .git."""
    tmp = target.with_name(target.name + ".git-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    run = {"cwd": tmp, "check": True, "capture_output": True, "timeout": 300}
    subprocess.run(["git", "init", "-q"], **run)
    subprocess.run(["git", "fetch", "-q", "--depth", "1", f"https://github.com/{repo}.git", ref], **run)
    subprocess.run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], **run)
    shutil.rmtree(tmp / ".git")
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(tmp), str(target))


def _extract(data: bytes, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(tmp, filter="data")
    roots = list(tmp.iterdir())
    root = roots[0] if len(roots) == 1 and roots[0].is_dir() else tmp
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(root), str(target))
    shutil.rmtree(tmp, ignore_errors=True)


def mcp_setup_commands(lib: Library, env: dict[str, str]) -> list[list[str]]:
    """Comandos `claude mcp add-json --scope user` para os MCPs de escopo user."""
    cmds = []
    for m in lib.mcps:
        if m.scope != "user" or (m.requires and not lib.mcp_enabled(m)):
            continue  # o Figma é registrado pelo scripts/setup-figma.sh, junto com o login
        cfg: dict[str, Any] = {"type": "http", "url": m.url}
        if m.name == "context7" and env.get("CONTEXT7_API_KEY"):
            cfg["headers"] = {"Authorization": f"Bearer {env['CONTEXT7_API_KEY']}"}
        cmds.append(["claude", "mcp", "add-json", "--scope", "user", m.name, json.dumps(cfg)])
    return cmds


def main(argv: list[str]) -> int:
    import os

    from orchestrator.config import Config

    c = Config()
    lib = Library(c.harness_dir, c.vendor_dir, dict(os.environ))
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "sync":
        for name, st in lib.sync().items():
            print(f"{name:26} {st}")
        return 0
    if cmd == "status":
        for row in lib.status():
            flag = "✔" if row["available"] else "✖"
            print(f"{flag} {row['kind']:6} {row['name']:32} {row['source']:24} {','.join(row['roles'])}")
        return 0
    if cmd == "mcp-setup":
        # roda como o usuário agent (entrypoint): registra MCPs de escopo user uma vez
        for args in mcp_setup_commands(lib, dict(os.environ)):
            name = args[5]
            exists = subprocess.run(["claude", "mcp", "get", name], capture_output=True).returncode == 0
            if not exists:
                subprocess.run(args, check=False)
                print(f"MCP {name} registrado")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
