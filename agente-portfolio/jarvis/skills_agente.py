"""Copia as skills do agente (harness) para as conversas do Jarvis como CONHECIMENTO.

São as mesmas skills que o planner, o builder e o test-engineer usam (plano-tecnico,
tickets-verticais, testes-que-importam, seguranca-web…). No Jarvis elas servem para ele escrever
pedidos de mudança precisos, revisar spec/plano/tickets e discutir testes e arquitetura com o mesmo
critério do pipeline. Entram com `user-invocable: false`: o Claude carrega quando precisa, mas elas
não poluem o menu de comandos `/` (lá ficam só os comandos do Jarvis). Skill com o mesmo nome de uma
do Jarvis fica de fora (a do Jarvis vale).

    python -m jarvis.skills_agente /opt/agente/harness/skills /srv/jarvis/central/.claude/skills
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def knowledge_only(text: str) -> str:
    """Frontmatter só com `name` e `description`, mais `user-invocable: false`. Nada de `allowed-tools`,
    `hooks` ou `model` de uma skill do harness passa para as conversas do Jarvis."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    keep = [ln for ln in m.group(1).splitlines() if re.match(r"^(name|description)\s*:", ln)]
    keep.append("user-invocable: false")
    return "---\n" + "\n".join(keep) + "\n---\n" + text[m.end() :]


def copy_all(src: Path, dest: Path) -> list[str]:
    copied: list[str] = []
    if not src.is_dir():
        return copied
    dest.mkdir(parents=True, exist_ok=True)
    own = {p.name for p in dest.iterdir()}
    for skill in sorted(src.iterdir()):
        if skill.is_symlink() or not (skill / "SKILL.md").is_file() or skill.name in own:
            continue
        target = dest / skill.name
        shutil.copytree(skill, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__"))
        md = target / "SKILL.md"
        md.write_text(knowledge_only(md.read_text(encoding="utf-8")), encoding="utf-8")
        copied.append(skill.name)
    return copied


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    names = copy_all(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"skills do agente como conhecimento: {len(names)} ({', '.join(names)})")
