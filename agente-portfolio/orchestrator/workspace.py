"""Operações nas pastas dos projetos: criar do template, adotar, sincronizar o harness, git.

Os arquivos dos projetos pertencem ao usuário `agent`. O orquestrador roda como root no container,
então tudo que ele grava em projeto é devolvido ao `agent` (chown) e o git roda como `agent`.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator import safefs
from orchestrator import stack as stack_manifest
from orchestrator.config import Config

HARNESS_OWNED_SCRIPTS = ("check.sh", "test_quality.py", "mutation.sh")
TESTER_SCAFFOLD = (
    "tests/acceptance/__init__.py",
    "tests/acceptance/conftest.py",
    "tests/e2e/__init__.py",
    "tests/e2e/conftest.py",
    "tests/properties/__init__.py",
    "tests/properties/conftest.py",
    "tests/test_api_contract.py",
)

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")

GITIGNORE_LINES = [
    "AGENT_STOP",
    "mutants/",
    ".harness/mcp.json",
    ".harness/TEST_FINDINGS.md",
    "STEER.md",
    ".harness/.evidence-reads",
    ".harness/evidence/",
    ".harness/TASK.md",
    ".harness/NEXT_FINDINGS.md",
    ".env",
    ".venv/",
]


class ShellError(RuntimeError):
    def __init__(self, cmd: list[str], code: int, output: str):
        super().__init__(f"{' '.join(cmd[:4])}… saiu com {code}: {output[-800:]}")
        self.code = code
        self.output = output


def slugify(name: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:40].strip("-")


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug)) and slug not in {"agent", "www", "api", "staging", "admin"}


class Workspace:
    def __init__(self, config: Config):
        self.config = config

    # shell -------------------------------------------------------------------------
    @property
    def as_agent(self) -> bool:
        return bool(self.config.run_as) and hasattr(os, "geteuid") and os.geteuid() == 0

    async def sh(
        self,
        *cmd: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        agent: bool = False,
        check: bool = True,
        timeout: int = 900,
    ) -> tuple[int, str]:
        argv = list(cmd)
        full_env = {**os.environ, **(env or {})}
        if agent and self.as_agent:
            # `env -i`: o processo do agent NÃO herda o ambiente do orquestrador (tokens, senhas)
            base = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": f"/home/{self.config.run_as}",
                "LANG": "C.UTF-8",
                **(env or {}),
            }
            extra = [f"{k}={v}" for k, v in base.items()]
            argv = ["runuser", "-u", self.config.run_as, "--", "env", "-i", *extra, *argv]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            raise ShellError(argv, -1, f"timeout após {timeout}s") from None
        text = out.decode("utf-8", "replace")
        if check and proc.returncode != 0:
            raise ShellError(argv, proc.returncode or -1, text)
        return proc.returncode or 0, text

    async def git(self, project_dir: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
        # hooks e fsmonitor desligados: o repositório é escrito pelo agente e não é confiável
        safe = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
        _, out = await self.sh("git", *safe, *args, cwd=project_dir, agent=True, check=check, env=env)
        return out.strip()

    async def give_to_agent(self, path: Path) -> None:
        if self.as_agent:
            await self.sh("chown", "-R", f"{self.config.run_as}:{self.config.run_as}", str(path))

    def project_dir(self, slug: str) -> Path:
        return self.config.projects_dir / slug

    # criação -----------------------------------------------------------------------
    async def create_from_template(self, slug: str, name: str) -> Path:
        """Projeto novo nasce só com o andaime do harness (gates, pastas de teste, contrato de aceite):
        a arquitetura vem do plano técnico e o código, do starter escolhido ou do primeiro ticket."""
        dest = self.project_dir(slug)
        if dest.exists():
            raise FileExistsError(f"a pasta do projeto {slug} já existe")
        # o fuzz de contrato só entra quando o manifesto declarar uma API OpenAPI (sync_harness)
        shutil.copytree(
            self.config.harness_dir / "project-template",
            dest,
            ignore=lambda d, names: ["test_api_contract.py"] if Path(d).name == "tests" else [],
        )
        readme = dest / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("(nome do projeto)", name))
        env_example = dest / ".env.example"
        env_example.write_text(env_example.read_text(encoding="utf-8").replace("APP_NAME=app", f"APP_NAME={slug}"))
        await self.give_to_agent(dest)
        await self.git(dest, "init", "-q", "-b", "main")
        await self._git_identity(dest)
        await self.sync_harness(dest, commit=False)
        await self.git(dest, "add", "-A")
        await self.git(dest, "commit", "-q", "-m", "chore: andaime do harness (arquitetura vem do plano)")
        return dest

    async def adopt(self, slug: str, repo_url: str) -> Path:
        dest = self.project_dir(slug)
        if dest.exists():
            raise FileExistsError(f"a pasta do projeto {slug} já existe")
        self.config.projects_dir.mkdir(parents=True, exist_ok=True)
        await self.give_to_agent(self.config.projects_dir)
        await self.sh("git", "clone", "-q", repo_url, str(dest), agent=True, env=self.git_auth_env(), timeout=1800)
        await self._git_identity(dest)
        # projeto adotado recebe só o que falta do template, nunca sobrescreve código existente
        template = self.config.harness_dir / "project-template"
        for rel in (".harness/PROGRESS.md", ".harness/features.json", "docs/adr/README.md"):
            target = dest / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template / rel, target)
        await self.give_to_agent(dest)
        await self.sync_harness(dest, commit=True)
        return dest

    async def _git_identity(self, project_dir: Path) -> None:
        await self.git(project_dir, "config", "user.name", self.config.git_name)
        await self.git(project_dir, "config", "user.email", self.config.git_email)

    # harness -----------------------------------------------------------------------
    async def sync_harness(
        self, project_dir: Path, commit: bool = True, install_skills: Callable[[Path], Any] | None = None
    ) -> bool:
        """Copia a versão atual do harness para o projeto: regras, hooks, agentes, skills do catálogo
        (via `install_skills`), lições e os gates. Toda melhoria do harness vale no próximo job."""
        src = self.config.harness_dir
        template = src / "project-template"
        claude_dir = project_dir / ".claude"
        if claude_dir.is_symlink():  # o projeto é do agente: um link aqui seria plantado
            claude_dir.unlink()
        elif claude_dir.exists():
            shutil.rmtree(claude_dir)
        shutil.copytree(src / ".claude", claude_dir)
        for hook in (claude_dir / "hooks").glob("*.sh"):
            hook.chmod(0o755)
        (claude_dir / "rules").mkdir(exist_ok=True)
        shutil.copy2(self.config.lessons_file, claude_dir / "rules" / "lessons.md")
        if install_skills is not None:
            install_skills(project_dir)
        safefs.copy_file(src / "CLAUDE.md", project_dir, "CLAUDE.md")
        safefs.real_dir(project_dir, "scripts")
        for gate in HARNESS_OWNED_SCRIPTS:  # gates são sempre os do harness
            safefs.copy_file(template / "scripts" / gate, project_dir, f"scripts/{gate}", mode=0o755)
        wants_contract = self._wants_contract_fuzz(project_dir)
        for rel in TESTER_SCAFFOLD:  # estrutura da suíte do test-engineer, só se faltar
            target = project_dir / rel
            if not os.path.lexists(target) and (rel != "tests/test_api_contract.py" or wants_contract):
                safefs.copy_file(template / rel, project_dir, rel)
        # manifestos de referência dos starters, para o planner escolher (somente leitura, via .claude/)
        refs = claude_dir / "starters"
        refs.mkdir(exist_ok=True)
        for name, path in stack_manifest.starters(src).items():
            shutil.copy2(path / "stack.json", refs / f"{name}.json")
        safefs.real_dir(project_dir, ".harness/evidence")
        self._ensure_gitignore(project_dir)
        await self.give_to_agent(project_dir)
        if not commit:
            return False
        vendored = safefs.read_text(project_dir, ".claude/skills/.gitignore")
        if vendored:  # projetos antigos podem ter skills de terceiros versionadas
            names = [n.strip("/") for n in vendored.splitlines() if n.startswith("/")]
            if names:
                await self.git(
                    project_dir,
                    "rm",
                    "-r",
                    "-q",
                    "--cached",
                    "--ignore-unmatch",
                    "--",
                    *[f".claude/skills/{n}" for n in names],
                    check=False,
                )
        status = await self.git(project_dir, "status", "--porcelain")
        if status:
            await self.git(project_dir, "add", "-A")
            await self.git(project_dir, "commit", "-q", "-m", "chore(harness): sincroniza harness")
            return True
        return False

    @staticmethod
    def _wants_contract_fuzz(project_dir: Path) -> bool:
        manifest = stack_manifest.load(project_dir)
        if manifest:
            backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
            return bool(backend.get("openapi") and backend.get("asgi"))
        return (project_dir / "app" / "main.py").exists()  # projetos de antes do manifesto

    @staticmethod
    def _ensure_gitignore(project_dir: Path) -> None:
        current = safefs.read_text(project_dir, ".gitignore").splitlines()
        missing = [line for line in GITIGNORE_LINES if line not in current]
        if missing:
            safefs.write_text(project_dir, ".gitignore", "\n".join([*current, *missing]) + "\n")

    # arquivos de controle ----------------------------------------------------------------
    async def write(self, project_dir: Path, rel: str, content: str) -> Path:
        """Escrita do orquestrador (root) no projeto do agente: nunca segue link simbólico."""
        path = safefs.write_text(project_dir, rel, content)
        await self.give_to_agent(path)
        return path

    def remove(self, project_dir: Path, rel: str) -> None:
        (project_dir / rel).unlink(missing_ok=True)

    async def set_stop(self, project_dir: Path, stop: bool) -> None:
        if stop:
            if not project_dir.exists():  # job ainda não criou a pasta: nada a travar
                return
            await self.write(project_dir, "AGENT_STOP", "pausado pelo operador\n")
        else:
            self.remove(project_dir, "AGENT_STOP")

    async def steer(self, project_dir: Path, message: str) -> None:
        await self.write(project_dir, "STEER.md", message.strip() + "\n")

    # git: leitura ------------------------------------------------------------------------
    async def head(self, project_dir: Path) -> str:
        return await self.git(project_dir, "rev-parse", "HEAD")

    async def short_head(self, project_dir: Path) -> str:
        return await self.git(project_dir, "rev-parse", "--short=8", "HEAD")

    async def changes_since(self, project_dir: Path, base: str) -> str:
        if not base:
            return ""
        log = await self.git(project_dir, "log", "--oneline", f"{base}..HEAD", check=False)
        stat = await self.git(project_dir, "diff", "--stat", f"{base}..HEAD", check=False)
        return f"{log}\n\n{stat}".strip()

    async def commit_all(self, project_dir: Path, message: str) -> None:
        if await self.git(project_dir, "status", "--porcelain"):
            await self.git(project_dir, "add", "-A")
            await self.git(project_dir, "commit", "-q", "-m", message)

    # autenticação git (só para clone/push feitos pelo orquestrador) ------------------------
    def git_auth_env(self) -> dict[str, str]:
        if not self.config.github_token:
            return {"GIT_TERMINAL_PROMPT": "0"}
        askpass = self.config.base_dir / "git-askpass.sh"
        if not askpass.exists():
            askpass.write_text('#!/bin/sh\ncase "$1" in *sername*) echo x-access-token;; *) echo "$GIT_TOKEN";; esac\n')
            askpass.chmod(0o755)
        return {
            "GIT_ASKPASS": str(askpass),
            "GIT_TOKEN": self.config.github_token,
            "GIT_TERMINAL_PROMPT": "0",
        }
