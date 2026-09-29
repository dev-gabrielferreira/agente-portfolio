"""Sessões do Claude Code em segundo plano, criadas pelo Jarvis pela linha de comando.

Usa os comandos oficiais de background agents (`claude --bg`, `claude agents --json`, `claude logs`,
`claude stop`, `claude --resume <id> --bg`). Cada sessão é um Claude Code completo rodando pela sua
assinatura, sob o supervisor do próprio Claude Code, no laboratório (`JARVIS_LAB`, um repositório
git: cada sessão edita na sua worktree). Projetos em produção entram só para leitura (`--add-dir`):
mudança em produção passa pelo pipeline, com gates e a sua aprovação.

No container, quem roda estes comandos é o broker (`jarvis/broker.py`, root) em nome do usuário
`lab` e com ambiente limpo: as sessões do laboratório não herdam o token da API do Jarvis nem
enxergam os arquivos dele. O Jarvis só fala com o broker por um socket.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
NAME_RE = re.compile(r"[^a-z0-9-]+")
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.\-\[\]]{1,60}$")
PERMISSION_MODES = {"manual", "acceptEdits", "dontAsk", "auto", "plan"}
SUMMARY_KEYS = (
    "id",
    "shortId",
    "short_id",
    "sessionId",
    "name",
    "status",
    "state",
    "cwd",
    "summary",
    "lastActivity",
    "updatedAt",
    "needsAttention",
)


class SessionError(RuntimeError):
    pass


class Sessions:
    def __init__(
        self,
        claude_bin: str | None = None,
        lab: Path | str | None = None,
        projects_dir: Path | str | None = None,
        permission_mode: str | None = None,
        timeout: float = 60,
        prefix: list[str] | None = None,
        env: dict[str, str] | None = None,
        default_model: str | None = None,
    ):
        self.claude = claude_bin or os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
        self.lab = Path(lab or os.environ.get("JARVIS_LAB", "/srv/jarvis/lab"))
        self.projects_dir = Path(projects_dir or os.environ.get("JARVIS_PROJECTS_DIR", "/srv/projetos"))
        mode = permission_mode or os.environ.get("JARVIS_BG_PERMISSION_MODE", "dontAsk")
        if mode not in PERMISSION_MODES:
            raise SessionError(f"JARVIS_BG_PERMISSION_MODE inválido: {mode}")
        self.permission_mode = mode
        self.timeout = timeout
        self.prefix = list(prefix or [])  # ex.: runuser -u lab -- env -i … (no broker)
        self.env = env
        # Opus por padrão, o mesmo do Jarvis: sem --model a sessão usaria o padrão da conta, que pode
        # ser outro. Outro modelo só quando o Gabriel pede (argumento `modelo`).
        env_model = os.environ.get("JARVIS_SESSION_MODEL") or os.environ.get("JARVIS_MODEL")
        self.default_model = default_model or env_model or "claude-opus-5-5"
        if not MODEL_RE.match(self.default_model):
            raise SessionError(f"modelo padrão inválido: {self.default_model}")

    # ------------------------------------------------------------------ util
    def _run(self, *args: str) -> str:
        try:
            self.lab.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SessionError(f"laboratório inacessível ({self.lab}): {e}") from None
        try:
            proc = subprocess.run(
                [*self.prefix, self.claude, *args],
                cwd=self.lab,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            raise SessionError(f"Claude Code não encontrado ({self.claude})") from None
        except subprocess.TimeoutExpired:
            raise SessionError(f"`claude {args[0]}` não respondeu em {self.timeout:.0f}s") from None
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr.strip() else "")
        if proc.returncode != 0:
            raise SessionError(f"`claude {' '.join(args[:2])}` saiu com {proc.returncode}: {out.strip()[-1500:]}")
        return out.strip()

    @staticmethod
    def _check_id(session_id: str) -> str:
        session_id = (session_id or "").strip()
        if not ID_RE.match(session_id):
            raise SessionError("id de sessão inválido (use o id mostrado por 'sessoes')")
        return session_id

    @staticmethod
    def _compact(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        picked = {k: item[k] for k in SUMMARY_KEYS if k in item and item[k] not in (None, "", [])}
        if not picked:  # formato desconhecido: devolve tudo, cortando textos longos
            return {k: (v[:300] if isinstance(v, str) else v) for k, v in item.items()}
        for k, v in picked.items():
            if isinstance(v, str) and len(v) > 300:
                picked[k] = v[:299] + "…"
        return picked

    # ------------------------------------------------------------------ API
    def list(self, include_done: bool = False) -> list[Any]:
        args = ["agents", "--json"]
        if include_done:
            args.append("--all")
        raw = self._run(*args)
        start = min((i for i in (raw.find("["), raw.find("{")) if i >= 0), default=-1)
        if start < 0:
            return []
        try:
            data = json.loads(raw[start:])
        except ValueError:
            raise SessionError(f"saída inesperada de `claude agents --json`: {raw[:500]}") from None
        if isinstance(data, dict):
            data = data.get("sessions") or data.get("agents") or [data]
        return [self._compact(x) for x in data]

    def create(
        self,
        task: str,
        name: str,
        agent: str = "",
        model: str = "",
        project: str = "",
    ) -> dict[str, str]:
        task = (task or "").strip()
        if len(task) < 10:
            raise SessionError("descreva a tarefa da sessão (mínimo de 10 caracteres)")
        if len(task) > 20000:
            raise SessionError("tarefa longa demais; resuma ou salve em um arquivo do laboratório e cite o caminho")
        slug = NAME_RE.sub("-", (name or "").lower()).strip("-")[:40] or "tarefa"
        extra_dirs: list[str] = []
        args = ["--bg", "--permission-mode", self.permission_mode]
        if agent:
            if not AGENT_RE.match(agent):
                raise SessionError("nome de agente inválido")
            args += ["--agent", agent]
        model = (model or "").strip() or self.default_model
        if not MODEL_RE.match(model):
            raise SessionError("nome de modelo inválido")
        args += ["--model", model]
        args += ["--name", f"jarvis-{slug}"]
        if project:
            pdir = (self.projects_dir / project).resolve()
            if not pdir.is_relative_to(self.projects_dir.resolve()) or not pdir.is_dir():
                raise SessionError(f"projeto '{project}' não encontrado em {self.projects_dir}")
            extra_dirs = ["--add-dir", str(pdir)]
            task = (
                f"{task}\n\n(O código do projeto '{project}' está em {pdir}, somente leitura. "
                "Mudanças nesse projeto não são feitas aqui: descreva-as para o Jarvis abrir um job no pipeline.)"
            )
        self.lab.mkdir(parents=True, exist_ok=True)
        if task.startswith("-"):  # nunca deixar a tarefa ser lida como opção da CLI
            task = "Tarefa: " + task
        # --add-dir aceita vários valores: vem primeiro, e o prompt fica logo depois de uma opção de valor único
        out = self._run(*extra_dirs, *args, task)
        return {"nome": f"jarvis-{slug}", "saida": out[-2000:]}

    def logs(self, session_id: str, limit: int = 6000) -> str:
        out = self._run("logs", self._check_id(session_id))
        limit = max(500, min(limit, 30000))
        return out if len(out) <= limit else "[…]\n" + out[-limit:]

    def stop(self, session_id: str) -> str:
        return self._run("stop", self._check_id(session_id)) or "parada"

    def message(self, session_id: str, text: str) -> str:
        """Continua a conversa de uma sessão em segundo plano com uma nova mensagem (id completo)."""
        text = (text or "").strip()
        if not text:
            raise SessionError("mensagem vazia")
        if text.startswith("-"):
            text = "Mensagem: " + text
        out = self._run("--resume", self._check_id(session_id), "--bg", "--permission-mode", self.permission_mode, text)
        return out[-2000:]
