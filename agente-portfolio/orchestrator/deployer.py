"""Build e deploy dos projetos com Docker + Caddy. Só o orquestrador executa isto — o agente
(usuário `agent`) não tem acesso ao Docker.

Cada projeto roda em dois ambientes com a MESMA imagem:
  staging    → container <slug>-staging  → https://<slug>-staging.<domínio> (com senha opcional)
  production → container <slug>-production → https://<slug>.<domínio>
Produção é uma promoção da imagem validada em staging, nunca um build novo.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from orchestrator.config import Config
from orchestrator.infra import CADDY_LOCK
from orchestrator.workspace import ShellError, Workspace, valid_slug

ENVS = ("staging", "production")


class DeployError(RuntimeError):
    """`infra=True`: problema do servidor (Caddy, rede, DNS) — não adianta o agente mexer no código."""

    def __init__(self, message: str, logs: str = "", infra: bool = False):
        super().__init__(message)
        self.logs = logs
        self.infra = infra


class Deployer(Protocol):
    async def build(self, slug: str, project_dir: Path, tag: str) -> str: ...
    async def up(self, slug: str, env: str, tag: str) -> None: ...
    async def health(self, slug: str, env: str) -> None: ...
    async def logs(self, slug: str, env: str, tail: int = 200) -> str: ...
    async def stop(self, slug: str, env: str) -> None: ...
    async def cleanup(self, slug: str, keep_tags: set[str]) -> None: ...
    async def public_health(self, slug: str, env: str) -> tuple[bool, str]: ...


def image(slug: str, tag: str) -> str:
    return f"agente/{slug}:{tag}"


def container(slug: str, env: str) -> str:
    return f"{slug}-{env}"


class DockerDeployer:
    def __init__(self, config: Config, workspace: Workspace):
        self.c = config
        self.ws = workspace

    @staticmethod
    def _check(slug: str, env: str | None = None) -> None:
        if not valid_slug(slug):
            raise DeployError(f"slug inválido: {slug!r}")
        if env is not None and env not in ENVS:
            raise DeployError(f"ambiente inválido: {env!r}")

    async def _docker(self, *args: str, timeout: int = 900) -> str:
        try:
            _, out = await self.ws.sh("docker", *args, timeout=timeout)
        except FileNotFoundError as e:
            raise DeployError("cliente docker não encontrado no container do agente", infra=True) from e
        except ShellError as e:
            daemon_down = "cannot connect to the docker daemon" in e.output.lower()
            raise DeployError(f"docker {args[0]} falhou", e.output, infra=daemon_down) from e
        return out

    # build ------------------------------------------------------------------------------
    async def build(self, slug: str, project_dir: Path, tag: str) -> str:
        self._check(slug)
        if not (project_dir / "Dockerfile").exists():
            raise DeployError("o projeto não tem Dockerfile (contrato de deploy)")
        return await self._docker(
            "build",
            "--pull",
            "-t",
            image(slug, tag),
            "--label",
            f"agente.project={slug}",
            str(project_dir),
            timeout=1800,
        )

    # compose -----------------------------------------------------------------------------
    def compose_file(self, slug: str, env: str, tag: str) -> str:
        name = container(slug, env)
        env_file = self.c.secrets_dir / f"{slug}.{env}.env"
        return f"""# gerado pelo agente — não edite à mão
name: {name}
services:
  app:
    image: {image(slug, tag)}
    container_name: {name}
    restart: unless-stopped
    env_file: ["{env_file}"]
    environment:
      APP_ENV: {env}
      APP_NAME: {slug}
      DATA_DIR: /data
    volumes:
      - data:/data
    networks: [{self.c.docker_network}]
    mem_limit: {self.c.container_memory}
    cpus: {self.c.container_cpus}
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    labels:
      agente.project: {slug}
      agente.env: {env}
      agente.tag: "{tag}"
    logging:
      driver: json-file
      options: {{max-size: "10m", max-file: "3"}}
volumes:
  data:
    name: {name}-data
networks:
  {self.c.docker_network}:
    external: true
"""

    def caddy_site(self, slug: str, env: str) -> str:
        host = self.c.host(slug, env)
        lines = [f"{host} {{", "\tencode zstd gzip"]
        if env == "staging":
            lines.append('\theader X-Robots-Tag "noindex, nofollow"')
            if self.c.staging_auth_hash:
                lines += [
                    "\tbasic_auth {",
                    f"\t\t{self.c.staging_auth_user} {self.c.staging_auth_hash}",
                    "\t}",
                ]
        lines += [f"\treverse_proxy {container(slug, env)}:8000", "}", ""]
        return "\n".join(lines)

    async def up(self, slug: str, env: str, tag: str) -> None:
        self._check(slug, env)
        env_file = self.c.secrets_dir / f"{slug}.{env}.env"
        if not env_file.exists():
            env_file.touch(mode=0o600)
        folder = self.c.deploy_dir / container(slug, env)
        folder.mkdir(parents=True, exist_ok=True)
        compose = folder / "compose.yml"
        compose.write_text(self.compose_file(slug, env, tag), encoding="utf-8")
        try:
            await self._docker("compose", "-f", str(compose), "up", "-d", "--remove-orphans", timeout=600)
        except DeployError as e:
            raise DeployError(f"docker compose up falhou em {env}", e.logs, infra=True) from e
        await self._ensure_caddy(slug, env)

    async def _ensure_caddy(self, slug: str, env: str) -> None:
        async with CADDY_LOCK:  # mudança no Caddyfile principal nunca no meio de um deploy
            await self._write_site(slug, env)

    async def _write_site(self, slug: str, env: str) -> None:
        site = self.c.caddy_sites_dir / f"{container(slug, env)}.caddy"
        content = self.caddy_site(slug, env)
        if site.exists() and site.read_text(encoding="utf-8") == content:
            return
        previous = site.read_text(encoding="utf-8") if site.exists() else None
        site.write_text(content, encoding="utf-8")
        try:
            await self._docker(
                "exec",
                self.c.caddy_container,
                "caddy",
                "reload",
                "--config",
                self.c.caddy_config_path,
                "--adapter",
                "caddyfile",
            )
        except DeployError as e:
            if previous is None:
                site.unlink(missing_ok=True)
            else:
                site.write_text(previous, encoding="utf-8")
            raise DeployError("reload do Caddy falhou", e.logs, infra=True) from e

    # verificação ---------------------------------------------------------------------------
    async def health(self, slug: str, env: str) -> None:
        """Espera o HEALTHCHECK do container ficar 'healthy' e confirma o /health público."""
        self._check(slug, env)
        name = container(slug, env)
        deadline = time.monotonic() + self.c.health_timeout_s
        fmt = "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"
        state = ""
        while time.monotonic() < deadline:
            state = (await self._docker("inspect", "-f", fmt, name)).strip()
            if state in {"healthy", "running"}:
                break
            if state in {"unhealthy", "exited", "dead"}:
                raise DeployError(f"container {name} ficou {state}", await self.logs(slug, env))
            await asyncio.sleep(3)
        else:
            raise DeployError(f"container {name} não ficou saudável (estado: {state})", await self.logs(slug, env))
        # confirma pelo caminho real do usuário: DNS → Caddy → container
        last = ""
        while time.monotonic() < deadline + 60:
            ok, last = await self.public_health(slug, env)
            if ok:
                return
            await asyncio.sleep(5)
        raise DeployError(
            f"container saudável, mas {self.c.url(slug, env)}/health não respondeu 200 ({last}): verifique DNS e Caddy",
            await self.logs(slug, env),
            infra=True,
        )

    async def public_health(self, slug: str, env: str) -> tuple[bool, str]:
        auth = None
        if env == "staging" and self.c.staging_auth_password:
            auth = (self.c.staging_auth_user, self.c.staging_auth_password)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, auth=auth) as client:
                r = await client.get(f"{self.c.url(slug, env)}/health")
            return r.status_code == 200, f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return False, f"{type(e).__name__}: {e}"

    async def logs(self, slug: str, env: str, tail: int = 200) -> str:
        self._check(slug, env)
        try:
            _, out = await self.ws.sh(
                "docker", "logs", "--tail", str(tail), container(slug, env), check=False, timeout=60
            )
            return out[-20000:]
        except ShellError as e:
            return e.output

    async def stop(self, slug: str, env: str) -> None:
        self._check(slug, env)
        compose = self.c.deploy_dir / container(slug, env) / "compose.yml"
        if compose.exists():
            await self._docker("compose", "-f", str(compose), "down")

    async def cleanup(self, slug: str, keep_tags: set[str]) -> None:
        self._check(slug)
        out = await self._docker("images", f"agente/{slug}", "--format", "{{.Tag}}")
        tags = [t for t in out.split() if t and t != "<none>"]  # docker lista do mais novo
        for tag in tags[self.c.keep_images :]:
            if tag not in keep_tags:
                await self.ws.sh("docker", "rmi", image(slug, tag), check=False)


@dataclass
class FakeDeployer:
    """Deployer em memória para testes e para DEPLOY_DRY_RUN=1."""

    fail_health: dict[str, int] = field(default_factory=dict)  # "slug:env" -> nº de falhas
    calls: list[tuple] = field(default_factory=list)
    running: dict[str, str] = field(default_factory=dict)

    async def build(self, slug: str, project_dir: Path, tag: str) -> str:
        self.calls.append(("build", slug, tag))
        return f"built {slug}:{tag}"

    async def up(self, slug: str, env: str, tag: str) -> None:
        self.calls.append(("up", slug, env, tag))
        self.running[f"{slug}:{env}"] = tag

    async def health(self, slug: str, env: str) -> None:
        self.calls.append(("health", slug, env))
        key = f"{slug}:{env}"
        if self.fail_health.get(key, 0) > 0:
            self.fail_health[key] -= 1
            raise DeployError(f"{key} não ficou saudável", "Traceback: erro simulado")

    async def public_health(self, slug: str, env: str) -> tuple[bool, str]:
        return f"{slug}:{env}" in self.running, "fake"

    async def logs(self, slug: str, env: str, tail: int = 200) -> str:
        return f"logs de {slug}-{env}"

    async def stop(self, slug: str, env: str) -> None:
        self.calls.append(("stop", slug, env))
        self.running.pop(f"{slug}:{env}", None)

    async def cleanup(self, slug: str, keep_tags: set[str]) -> None:
        self.calls.append(("cleanup", slug))
