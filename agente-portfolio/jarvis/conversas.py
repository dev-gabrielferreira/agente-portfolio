"""Uma conversa do Jarvis por projeto no app do Claude (Remote Control em modo servidor).

Para cada projeto com nota na base de conhecimento, o root mantém a pasta
`/srv/jarvis/projetos/<slug>` com as MESMAS regras, skills, comandos, subagentes, hooks e MCP da
sessão Jarvis, mais uma seção dizendo de qual projeto é a conversa. O supervisor sobe nela

    claude remote-control --name "Jarvis · <Projeto>" --spawn same-dir …

numa janela do tmux "projetos" (usuário jarvis). No app (Code), cada projeto aparece como uma
conversa com o nome dele; o histórico volta sozinho quando o container reinicia.

    python -m jarvis.conversas sync   # (root) gera/atualiza as pastas e imprime os slugs que devem estar no ar

Variáveis: JARVIS_CONVERSAS (1 liga), JARVIS_CONVERSAS_MAX (quantos projetos ao mesmo tempo, pelos
mais recentes), JARVIS_CONVERSAS_FIXAS (slugs sempre no ar), JARVIS_CONVERSAS_CAPACIDADE (conversas
extras por projeto), JARVIS_MODEL.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")  # sempre com fullmatch
MEMORY = "/srv/jarvis/central/memoria"
OK_MARK = "__ok__"  # última linha do sync: sem ela o supervisor não fecha conversa nenhuma
J = Path(os.environ.get("JARVIS_ROOT", "/srv/jarvis"))
KNOWLEDGE = Path(os.environ.get("JARVIS_KNOWLEDGE", "/srv/conhecimento"))

SECTION = """

## Esta conversa é do projeto {name} (`{slug}`)

O Gabriel abriu esta conversa no app para cuidar do projeto **{name}**. Tudo o que ele pedir aqui é
sobre ele, a menos que cite outro projeto: use `{slug}` como slug nas ferramentas e comandos
(`/status`, `/mudar`, `/aprovar`… sem argumento valem para este projeto).

- Estado: `projeto` e `nota_do_projeto` com slug `{slug}`; jobs com `jobs` filtrando `projeto={slug}`.
- Código (somente leitura): `/srv/projetos/{slug}` · nota: `conhecimento/projetos/{slug}.md`.
- Pedido de outro projeto: atenda normalmente e lembre que ele tem a conversa própria no app.
- Novo projeto pedido aqui: crie com `/novo`; a conversa dele aparece no app em ~1 minuto.
"""


@dataclass(frozen=True)
class Project:
    slug: str
    name: str
    updated: str
    status: str


def _frontmatter(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not text.startswith("---"):
        return out
    for line in text.split("\n---", 1)[0].splitlines()[1:]:
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def projects(knowledge: Path = KNOWLEDGE) -> list[Project]:
    """Projetos da base de conhecimento, do mais recente para o mais antigo (arquivados ficam de fora)."""
    out: list[Project] = []
    folder = knowledge / "projetos"
    if not folder.is_dir():
        return out
    for note in folder.glob("*.md"):
        slug = note.stem
        if not SLUG_RE.fullmatch(slug) or note.is_symlink():
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        meta = _frontmatter(text)
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), slug)
        out.append(Project(slug, clean_name(title, slug), meta.get("atualizado", ""), meta.get("status", "")))
    out = [p for p in out if p.status != "archived"]
    return sorted(out, key=lambda p: (p.updated, p.slug), reverse=True)


def clean_name(title: str, fallback: str) -> str:
    """Nome para o título da conversa e o CLAUDE.md: sem controle, formatação invisível nem caracteres
    que signifiquem algo para o shell (o texto vem de nota gerada a partir do que o usuário pediu)."""
    kept = "".join(
        ch
        for ch in unicodedata.normalize("NFC", title)
        if unicodedata.category(ch)[0] not in {"C"} and ch not in '"`$\\'
    )
    return re.sub(r"\s+", " ", kept).strip()[:60] or fallback


def wanted(all_projects: list[Project], limit: int, pinned: set[str]) -> list[Project]:
    """Os fixos sempre; depois os mais recentes até completar o limite."""
    chosen = [p for p in all_projects if p.slug in pinned]
    for p in all_projects:
        if len(chosen) >= limit:
            break
        if p.slug not in pinned:
            chosen.append(p)
    return chosen


def _read(path: Path) -> str:
    """Leitura do root que nunca segue link simbólico (a origem é do root, mas o custo é zero)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as f:
        return f.read()


def _write(path: Path, text: str, mode: int = 0o644) -> bool:
    if path.is_symlink():
        path.unlink()
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(mode)
    os.replace(tmp, path)
    return True


def _link(path: Path, target: Path) -> bool:
    if path.is_symlink() and os.readlink(path) == str(target):
        return False
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    path.symlink_to(target)
    return True


def render(project: Project, central: Path, root: Path, model: str) -> bool:
    """Gera (ou atualiza) a pasta do projeto a partir da pasta central. Devolve True se algo mudou.

    Tudo aqui é do root e só leitura para o jarvis: ele não troca as próprias regras nem planta link
    simbólico onde o root escreve. O que ele escreve (memória) está no destino do link `memoria`."""
    dest = root / project.slug
    dest.mkdir(parents=True, exist_ok=True)
    changed = False
    base = _read(central / "CLAUDE.md")
    changed |= _write(dest / "CLAUDE.md", base.rstrip() + SECTION.format(name=project.name, slug=project.slug))
    changed |= _write(dest / ".mcp.json", _read(central / ".mcp.json"))
    claude = dest / ".claude"
    (claude / "skills").mkdir(parents=True, exist_ok=True)
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    settings = json.loads(_read(central / ".claude" / "settings.json"))
    settings["model"] = model  # o modo servidor não repassa --model às conversas: vai pelo settings
    extra = settings.setdefault("permissions", {}).setdefault("additionalDirectories", [])
    if MEMORY not in extra:
        extra.append(MEMORY)  # a memória do Jarvis fica na central: acessível sem pedir permissão
    changed |= _write(claude / "settings.json", json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    skills = {p.name for p in (central / ".claude" / "skills").iterdir() if p.is_dir() and not p.is_symlink()}
    for name in sorted(skills):
        changed |= _link(claude / "skills" / name, (central / ".claude" / "skills" / name).resolve())
    for stale in (claude / "skills").iterdir():
        if stale.name not in skills:
            if stale.is_symlink() or stale.is_file():
                stale.unlink()
            else:
                shutil.rmtree(stale)
            changed = True
    agents = {p.name: p for p in (central / ".claude" / "agents").glob("*.md") if not p.is_symlink()}
    for name, src in agents.items():
        changed |= _write(claude / "agents" / name, _read(src))
    for stale in (claude / "agents").glob("*.md"):
        if stale.name not in agents:
            stale.unlink()
            changed = True
    changed |= _link(dest / "conhecimento", KNOWLEDGE)
    changed |= _link(dest / "memoria", central / "memoria")
    for folder in (dest, claude, claude / "skills", claude / "agents"):
        folder.chmod(0o755)
    return changed


def run_script(project: Project, root: Path, capacity: int) -> str:
    folder = root / project.slug
    args = [
        "claude",
        "remote-control",
        "--name",
        f"Jarvis · {project.name}",
        "--remote-control-session-name-prefix",
        f"jarvis-{project.slug}",
        "--spawn",
        "same-dir",
        "--capacity",
        str(max(1, capacity)),
        "--permission-mode",
        "acceptEdits",
    ]
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "# gerado pelo jarvis.conversas — a conversa do projeto no app (Remote Control em modo servidor)",
            f"cd {shlex.quote(str(folder))} || exit 1",
            " ".join(shlex.quote(a) for a in args),
            "sleep 5",
            "",
        ]
    )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def sync(
    central: Path = J / "central",
    root: Path = J / "projetos",
    scripts: Path = J / "run" / "projetos",
    knowledge: Path = KNOWLEDGE,
    warn: Callable[[str], None] | None = None,
) -> Iterator[Project]:
    """Gera cada projeto e o devolve assim que fica pronto. Projeto que falhar ao gerar continua no ar
    se já tinha script (a conversa não cai por causa de uma nota estranha)."""
    warn = warn or (lambda msg: print(msg, file=sys.stderr))
    limit = _int_env("JARVIS_CONVERSAS_MAX", 6)
    fixed = os.environ.get("JARVIS_CONVERSAS_FIXAS", "").split(",")
    pinned = {x.strip() for x in fixed if SLUG_RE.fullmatch(x.strip())}
    capacity = _int_env("JARVIS_CONVERSAS_CAPACIDADE", 2)
    model = os.environ.get("JARVIS_MODEL", "claude-opus-5-5")
    chosen = wanted(projects(knowledge), limit, pinned)
    for folder in (root, scripts):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o755)
    for p in chosen:
        try:
            render(p, central, root, model)
            _write(scripts / f"{p.slug}.sh", run_script(p, root, capacity), mode=0o755)
        except (OSError, ValueError) as e:
            warn(f"conversas: não consegui gerar a pasta de {p.slug}: {e}")
            if not (scripts / f"{p.slug}.sh").is_file():
                continue
        yield p


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "sync":
        print(__doc__)
        return 2
    for p in sync():
        print(p.slug, flush=True)
    print(OK_MARK)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
