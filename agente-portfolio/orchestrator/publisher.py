"""Publica o código no GitHub depois do deploy aprovado.

O push é feito a partir de um clone temporário criado pelo orquestrador: a configuração e os hooks
do repositório de trabalho (escritos pelo agente) nunca rodam com o token do GitHub no ambiente.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from orchestrator.config import Config
from orchestrator.workspace import Workspace

API = "https://api.github.com"


class PublishError(RuntimeError):
    pass


class Publisher(Protocol):
    async def publish(self, project: dict, project_dir: Path, tag: str) -> str: ...


class GitHubPublisher:
    def __init__(self, config: Config, workspace: Workspace):
        self.c = config
        self.ws = workspace

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.c.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def ensure_repo(self, project: dict) -> str:
        """Cria o repositório público se não existir. Retorna a URL https do repo."""
        if project.get("repo_url"):
            return project["repo_url"]
        owner, name = self.c.github_owner, project["slug"]
        homepage = self.c.url(project["slug"], "production")
        async with httpx.AsyncClient(timeout=30, headers=self._headers()) as client:
            r = await client.get(f"{API}/repos/{owner}/{name}")
            if r.status_code == 404:
                r = await client.post(
                    f"{API}/user/repos",
                    json={
                        "name": name,
                        "description": (project.get("description") or project["name"])[:300],
                        "homepage": homepage,
                        "private": False,
                        "has_wiki": False,
                    },
                )
                if r.status_code >= 300:
                    raise PublishError(f"não consegui criar o repositório: {r.status_code} {r.text[:300]}")
                await client.put(
                    f"{API}/repos/{owner}/{name}/topics",
                    json={"names": ["portfolio", "fastapi", "python", "built-with-claude"]},
                )
            elif r.status_code >= 300:
                raise PublishError(f"GitHub respondeu {r.status_code}: {r.text[:300]}")
        return f"https://github.com/{owner}/{name}"

    async def publish(self, project: dict, project_dir: Path, tag: str) -> str:
        if not self.c.github_token:
            raise PublishError("GITHUB_TOKEN não configurado; código não publicado")
        repo_url = await self.ensure_repo(project)
        tmp = Path(tempfile.mkdtemp(prefix="publish-"))
        try:
            clean = tmp / "repo"
            base = ("git", "-c", "safe.directory=*", "-c", "core.hooksPath=/dev/null")
            await self.ws.sh(*base, "clone", "-q", "--no-hardlinks", str(project_dir), str(clean))
            env = self.ws.git_auth_env()
            await self.ws.sh(*base, "tag", "-f", f"deploy-{tag}", cwd=clean)
            # sem --force: se o remoto divergiu (commit feito fora do agente), o push falha e
            # o painel avisa — o agente nunca sobrescreve histórico de ninguém
            await self.ws.sh(
                *base,
                "push",
                "-q",
                f"{repo_url}.git",
                "HEAD:refs/heads/main",
                f"refs/tags/deploy-{tag}",
                cwd=clean,
                env=env,
                timeout=600,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return repo_url


class NullPublisher:
    """Usado em testes ou quando o GitHub não está configurado."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, project: dict, project_dir: Path, tag: str) -> str:
        self.published.append((project["slug"], tag))
        return project.get("repo_url") or f"https://github.com/exemplo/{project['slug']}"
