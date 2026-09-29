"""Leitura e escrita seguras, pelo orquestrador (root), dentro das pastas dos projetos.

As pastas dos projetos pertencem ao usuário `agent`, que é um modelo executando comandos. Um link
simbólico plantado ali (`SPEC.md -> /proc/self/environ`, `.harness/tickets.json -> /srv/agente/agente.db`)
faria o root ler segredos para dentro de um prompt ou sobrescrever o banco. Toda leitura cujo conteúdo
vai para prompts, painel, API ou base de conhecimento, e toda escrita do orquestrador num projeto,
passam por aqui:

- o caminho é relativo e fica dentro do projeto;
- nenhum componente pode ser link simbólico (leitura: recusa; escrita: o link é removido e trocado
  por arquivo/pasta de verdade);
- o arquivo final é aberto com O_NOFOLLOW.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


class UnsafePath(ValueError):
    pass


def _parts(rel: str | Path) -> tuple[str, ...]:
    rel = Path(rel)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise UnsafePath(f"caminho fora do projeto: {rel}")
    return rel.parts


def contained(base: Path, rel: str | Path) -> Path:
    """Caminho dentro de `base` sem nenhum link simbólico no caminho (o alvo pode não existir)."""
    root = base.resolve()
    cur = root
    for part in _parts(rel):
        cur = cur / part
        if cur.is_symlink():
            raise UnsafePath(f"{rel}: link simbólico no caminho ({cur.relative_to(root)})")
    return cur


def is_safe_file(base: Path, rel: str | Path) -> bool:
    try:
        return contained(base, rel).is_file()
    except UnsafePath:
        return False


def read_text(base: Path, rel: str | Path, limit: int | None = None, default: str = "") -> str:
    """Conteúdo do arquivo, ou `default` se não existir, não for arquivo comum ou for inseguro."""
    try:
        path = contained(base, rel)
    except UnsafePath:
        return default
    try:
        # O_NONBLOCK: um FIFO plantado no lugar do arquivo não trava o orquestrador
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return default
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return default
        data = f.read(limit * 4 if limit else -1)
    text = data.decode("utf-8", "replace")
    return text[:limit] if limit else text


def real_dir(base: Path, rel: str | Path) -> Path:
    """Garante uma pasta de verdade em `base/rel`: links simbólicos no caminho são removidos."""
    root = base.resolve()
    cur = root
    for part in _parts(rel):
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            cur.unlink()
        cur.mkdir(exist_ok=True)
    return cur


def _prepare_file(base: Path, rel: str | Path) -> Path:
    parts = _parts(rel)
    parent = real_dir(base, Path(*parts[:-1])) if len(parts) > 1 else base.resolve()
    path = parent / parts[-1]
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    return path


def write_bytes(base: Path, rel: str | Path, data: bytes, mode: int = 0o644) -> Path:
    path = _prepare_file(base, rel)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def write_text(base: Path, rel: str | Path, text: str, mode: int = 0o644) -> Path:
    return write_bytes(base, rel, text.encode("utf-8"), mode)


def copy_file(src: Path, base: Path, rel: str | Path, mode: int | None = None) -> Path:
    """Copia um arquivo do harness (confiável) para dentro do projeto sem seguir links do destino."""
    path = write_bytes(base, rel, src.read_bytes(), mode if mode is not None else (src.stat().st_mode & 0o777))
    shutil.copystat(src, path)
    return path
