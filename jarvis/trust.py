"""Marca pastas como confiáveis para o Claude Code do usuário atual (sem diálogo interativo).

É o caminho que a própria CLI indica quando ignora as permissões de uma pasta ainda não confiável:
`projects["<pasta>"].hasTrustDialogAccepted: true` em `~/.claude.json`. Só usamos para as pastas
que o próprio harness do Jarvis cria e controla (central e laboratório).

    python -m jarvis.trust /srv/jarvis/central /srv/jarvis/lab
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def trust(paths: list[str], config: Path | None = None) -> list[str]:
    config = config or Path(os.environ.get("CLAUDE_CONFIG_JSON", Path.home() / ".claude.json"))
    try:
        data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    except ValueError:
        return []  # arquivo em uso/corrompido: não arrisca sobrescrever o estado do Claude Code
    if not isinstance(data, dict):
        return []
    projects = data.setdefault("projects", {})
    changed = []
    for raw in paths:
        path = str(Path(raw).resolve())
        entry = projects.setdefault(path, {})
        if entry.get("hasTrustDialogAccepted") is not True:
            entry["hasTrustDialogAccepted"] = True
            changed.append(path)
    if changed:
        config.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=config.parent, prefix=".claude.json.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, config)
    return changed


if __name__ == "__main__":
    done = trust(sys.argv[1:])
    print("pastas confiáveis: " + (", ".join(done) if done else "nenhuma mudança"))
