"""Executa sessões do Claude Code em modo headless (`claude -p`) como o usuário isolado `agent`.

Cada sessão é um contexto novo. A tarefa vai num arquivo (.harness/TASK.md) e o prompt da linha de
comando só aponta para ele — comunicação por arquivos, auditável e sem limite de tamanho de argumento.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jsonschema

from orchestrator import safefs
from orchestrator.config import Config

RATE_LIMIT_RE = re.compile(
    r"(usage limit|session limit|weekly limit|hit your .{0,20}limit|rate[ _-]?limit|limit reached"
    r"|429|overloaded|quota)",
    re.IGNORECASE,
)
# ex.: "You've hit your session limit · resets 11:40pm (UTC)"
RESET_RE = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*\(?([A-Za-z_/+-]+)?\)?", re.IGNORECASE)


def parse_reset(text: str, now: datetime | None = None) -> datetime | None:
    """Converte "resets 11:40pm (UTC)" na próxima ocorrência desse horário, em UTC (+2 min de folga)."""
    m = RESET_RE.search(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    try:
        tz = ZoneInfo(m.group(4)) if m.group(4) else UTC
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    now = (now or datetime.now(UTC)).astimezone(tz)
    at = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at.astimezone(UTC) + timedelta(minutes=2)


ENV_PASSTHROUGH = (
    "CLAUDE_CODE_OAUTH_TOKEN",  # assinatura (gerado com `claude setup-token`)
    "ANTHROPIC_API_KEY",  # alternativa: pago por uso na API
    "PLAYWRIGHT_BROWSERS_PATH",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "PIP_INDEX_URL",
)


@dataclass
class RunResult:
    ok: bool
    text: str = ""
    structured: Any = None
    session_id: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    error: str = ""
    rate_limited: bool = False
    retry_at: datetime | None = None
    cancelled: bool = False
    log_path: str = ""


@dataclass
class RunSpec:
    role: str  # planner | designer | builder | tester | evaluator | security | reviewer | retro
    project_dir: Path
    task: str
    log_path: Path
    agent: str | None = None
    json_schema: dict | None = None
    mcp_config: dict | None = None  # {"mcpServers": {...}} dos servidores de sessão deste papel
    plugins: list[Path] = field(default_factory=list)
    max_turns: int = 100
    effort: str | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)


class Runner(Protocol):
    async def run(self, spec: RunSpec) -> RunResult: ...

    def cancel(self) -> None: ...


def describe_event(ev: dict[str, Any]) -> list[str]:
    """Converte um evento stream-json numa ou mais linhas legíveis para o painel."""
    t = ev.get("type")
    out: list[str] = []
    if t == "system" and ev.get("subtype") == "init":
        out.append(f"⚙️  sessão iniciada · modelo {ev.get('model', '?')}")
    elif t == "system" and ev.get("subtype") == "api_retry":
        out.append(f"⏳ API: nova tentativa {ev.get('attempt')} ({ev.get('error')})")
    elif t == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append("💬 " + block["text"].strip())
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                arg = (
                    inp.get("command")
                    or inp.get("file_path")
                    or inp.get("pattern")
                    or inp.get("url")
                    or inp.get("description")
                    or ""
                )
                out.append(f"🔧 {block.get('name')}: {str(arg)[:300]}")
    elif t == "user":
        content = ev.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(c.get("text", "") for c in text if isinstance(c, dict))
                    out.append(f"⚠️  {str(text)[:400]}")
    elif t == "result":
        cost = ev.get("total_cost_usd") or 0
        out.append(
            f"🏁 fim · {ev.get('subtype')} · {ev.get('num_turns', 0)} turnos · "
            f"~US$ {cost:.2f} (estimativa) · erro={ev.get('is_error')}"
        )
    return out


TAG_RE = re.compile(r"<(/?)([A-Za-z_][\w-]*)>")


def repair_structured(data: Any, schema: dict | None) -> Any:
    """Conserta um tropeço real de formatação: o modelo às vezes fecha um campo de texto com tag XML
    e escreve os campos seguintes dentro dele (`"understanding": "…</understanding><questions>[…]"`).
    Recupera os campos embutidos que o schema conhece (JSON para listas/objetos) sem inventar nada."""
    if not isinstance(data, dict) or not schema:
        return data
    props = schema.get("properties", {})
    fixed = dict(data)
    for key, value in data.items():
        if not isinstance(value, str) or f"</{key}>" not in value:
            continue
        head, _, tail = value.partition(f"</{key}>")
        fixed[key] = head.strip()
        pieces = TAG_RE.split(tail)  # [texto, "/"|"", nome, texto, …]
        current, buffer = None, []
        recovered: dict[str, str] = {}
        for i in range(0, len(pieces), 3):
            text = pieces[i]
            if current:
                buffer.append(text)
            if i + 2 < len(pieces):
                closing, name = pieces[i + 1], pieces[i + 2]
                if current and (closing or name != current):
                    recovered.setdefault(current, "".join(buffer))
                    current, buffer = None, []
                if not closing and name in props:
                    current, buffer = name, []
        if current:
            recovered.setdefault(current, "".join(buffer))
        for name, raw in recovered.items():
            if name in fixed and fixed[name] not in (None, "", [], {}):
                continue
            kind = props[name].get("type")
            if kind == "string":
                fixed[name] = raw.strip()
                continue
            with contextlib.suppress(json.JSONDecodeError):
                fixed[name] = json.loads(raw.strip())
    return fixed


def pick_structured(candidates: list[Any], schema: dict | None) -> Any:
    """Entre a saída final e as tentativas consertadas, a mais completa que respeita o schema.
    (Depois de errar o formato algumas vezes, o modelo às vezes entrega um objeto mínimo só para passar.)"""
    valid = []
    for c in candidates:
        if c is None:
            continue
        try:
            if schema:
                jsonschema.validate(c, schema)
            valid.append(c)
        except jsonschema.ValidationError:
            continue
    if not valid:
        return candidates[-1] if candidates else None
    return max(valid, key=lambda c: len(json.dumps(c, ensure_ascii=False)))


def extract_json(text: str) -> Any:
    """Plano B quando a saída estruturada não vem no campo próprio: pega o último objeto JSON do texto."""
    for match in reversed(list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL))):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(match.group(1))
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start != -1 and end > start:
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(text[start : end + 1])
    return None


class ClaudeRunner:
    def __init__(self, config: Config):
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._cancelled = False

    # montagem do comando -----------------------------------------------------------
    def build_command(self, spec: RunSpec) -> list[str]:
        c = self.config
        cmd = [
            shutil.which(c.claude_bin) or c.claude_bin,  # caminho absoluto: o PATH do agente é mínimo
            "-p",
            "Sua tarefa está em .harness/TASK.md. Leia o arquivo e execute-a seguindo o CLAUDE.md.",
            "--model",
            c.model,
            "--effort",
            spec.effort or c.effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            c.permission_mode,
            "--permission-prompts",
            "none",
            "--max-turns",
            str(spec.max_turns),
        ]
        if spec.agent:
            cmd += ["--agent", spec.agent]
        if spec.json_schema:
            cmd += ["--json-schema", json.dumps(spec.json_schema)]
        if spec.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(spec.disallowed_tools)]
        if spec.mcp_config and spec.mcp_config.get("mcpServers"):
            # sem --strict-mcp-config: os MCPs de escopo user (Figma, Context7) precisam carregar.
            # O isolamento vem de --disallowedTools por servidor e dos conectores do claude.ai
            # desligados (ENABLE_CLAUDEAI_MCP_SERVERS=false + disableClaudeAiConnectors).
            cmd += ["--mcp-config", str(spec.project_dir / ".harness" / "mcp.json")]
        for plugin in spec.plugins:
            cmd += ["--plugin-dir", str(plugin)]
        return cmd

    def build_env(self, spec: RunSpec) -> dict[str, str]:
        home = f"/home/{self.config.run_as}" if self._switch_user() else os.environ.get("HOME", "/tmp")
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": home,
            "LANG": "C.UTF-8",
            "TERM": "dumb",
            "HARNESS_ROLE": spec.role,
            "DISABLE_AUTOUPDATER": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "DISABLE_TELEMETRY": "1",
            "MIN_COVERAGE": str(self.config.min_coverage),
            "MIN_MUTATION_SCORE": str(self.config.min_mutation_score),
        }
        for key in ENV_PASSTHROUGH:
            if os.environ.get(key):
                env[key] = os.environ[key]
        env.update(spec.extra_env)
        return env

    def _switch_user(self) -> bool:
        return bool(self.config.run_as) and hasattr(os, "geteuid") and os.geteuid() == 0

    def _argv(self, spec: RunSpec) -> tuple[list[str], dict[str, str] | None]:
        cmd = self.build_command(spec)
        env = self.build_env(spec)
        if self._switch_user():
            pairs = [f"{k}={v}" for k, v in env.items()]
            return ["runuser", "-u", self.config.run_as, "--", "env", "-i", *pairs, *cmd], None
        return cmd, env

    # execução ------------------------------------------------------------------------
    async def run(self, spec: RunSpec) -> RunResult:
        self._cancelled = False
        # escrita segura: o projeto é do agente e pode conter links simbólicos plantados
        harness = safefs.real_dir(spec.project_dir, ".harness")
        safefs.write_text(spec.project_dir, ".harness/TASK.md", spec.task)
        if spec.mcp_config and spec.mcp_config.get("mcpServers"):
            safefs.write_text(spec.project_dir, ".harness/mcp.json", json.dumps(spec.mcp_config, indent=2))
        if self._switch_user():
            await _chown(harness, self.config.run_as)

        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path = spec.log_path.with_suffix(".jsonl")
        argv, env = self._argv(spec)
        started = time.monotonic()
        result = RunResult(ok=False, log_path=str(spec.log_path))
        final: dict[str, Any] | None = None
        attempts: list[Any] = []  # cada chamada do StructuredOutput (inclusive as recusadas pelo schema)
        stderr_tail: list[str] = []

        with spec.log_path.open("a", encoding="utf-8") as log, raw_path.open("a", encoding="utf-8") as raw:
            log.write(f"\n===== {spec.role} {'(' + spec.agent + ')' if spec.agent else ''} =====\n")
            log.flush()
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(spec.project_dir),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=16 * 1024 * 1024,
            )

            async def read_stdout() -> None:
                nonlocal final
                assert self._proc and self._proc.stdout
                async for line in self._proc.stdout:
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    raw.write(text + "\n")
                    try:
                        ev = json.loads(text)
                    except json.JSONDecodeError:
                        log.write(text + "\n")
                        continue
                    if ev.get("type") == "result":
                        final = ev
                    elif ev.get("type") == "assistant":
                        for block in ev.get("message", {}).get("content", []):
                            if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                                attempts.append(block.get("input"))
                    for human in describe_event(ev):
                        log.write(human + "\n")
                    log.flush()

            async def read_stderr() -> None:
                assert self._proc and self._proc.stderr
                async for line in self._proc.stderr:
                    stderr_tail.append(line.decode("utf-8", "replace").rstrip())
                    del stderr_tail[:-40]

            try:
                await asyncio.wait_for(
                    asyncio.gather(read_stdout(), read_stderr(), self._proc.wait()),
                    timeout=self.config.session_timeout_s,
                )
            except TimeoutError:
                result.error = f"sessão excedeu {self.config.session_timeout_s}s e foi encerrada"
                await self._terminate()
            finally:
                self._proc = None

        result.duration_s = round(time.monotonic() - started, 1)
        result.cancelled = self._cancelled
        if final:
            result.session_id = final.get("session_id", "")
            result.cost_usd = float(final.get("total_cost_usd") or 0)
            result.turns = int(final.get("num_turns") or 0)
            result.text = final.get("result") or ""
            result.structured = final.get("structured_output")
            if result.structured is None and spec.json_schema:
                result.structured = extract_json(result.text)
            if spec.json_schema and len(attempts) > 1:
                repaired = [repair_structured(a, spec.json_schema) for a in attempts]
                result.structured = pick_structured([*repaired, result.structured], spec.json_schema)
            is_error = bool(final.get("is_error")) or final.get("subtype") not in (None, "success")
            result.ok = not is_error and not result.error and not self._cancelled
            if is_error and not result.error:
                result.error = f"{final.get('subtype')}: {result.text[:500]}"
        elif not result.error:
            result.error = "sessão terminou sem resultado. stderr: " + " | ".join(stderr_tail[-8:])
        if self._cancelled:
            result.error = "cancelado pelo operador"
        blob = f"{result.error} {' '.join(stderr_tail[-8:])}"
        result.rate_limited = not result.ok and bool(RATE_LIMIT_RE.search(blob))
        if result.rate_limited:
            result.retry_at = parse_reset(blob)
        return result

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._proc.pid, signal.SIGINT)
            asyncio.get_event_loop().call_later(20, self._kill_now)

    def _kill_now(self) -> None:
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._proc.pid, signal.SIGKILL)

    async def _terminate(self) -> None:
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=20)
            except TimeoutError:
                self._kill_now()


async def _chown(path: Path, user: str) -> None:
    proc = await asyncio.create_subprocess_exec("chown", "-R", f"{user}:{user}", str(path))
    await proc.wait()
