"""Mudanças no Caddyfile PRINCIPAL do VPS: o Jarvis propõe, você aprova com o código de 6 dígitos.

Os sites dos projetos do agente continuam sendo arquivos próprios em `caddy-sites/` (o deploy cuida
deles). Isto aqui é para o resto do seu Caddyfile — um redirect, um site seu, tirar o bloco antigo
de um projeto adotado. O caminho de uma mudança:

  proposta   o Jarvis manda edições (trecho antes → depois) ou o arquivo inteiro, calculadas sobre
             uma versão (hash) do arquivo. O orquestrador aplica em memória, confere as REGRAS
             (bloqueios e alertas), valida no próprio Caddy (`caddy validate`) e guarda o diff.
  aprovação  só com o código TOTP. Pelo Jarvis, também com a CONFIRMAÇÃO desta proposta — um código
             curto que o orquestrador manda no push (ntfy) e mostra no painel, junto com o resumo que
             ELE calculou (sites que entram e saem, alertas). O Jarvis nunca recebe a confirmação: se
             um Jarvis manipulado mostrasse uma proposta e tentasse aplicar outra, o código que você
             digitou não bateria. No painel você vê o diff real. Na hora: confere que o arquivo não mudou
             desde a proposta, valida de novo, mede os sites (antes), grava com backup, `caddy reload`,
             mede de novo. Reload recusado ou site que funcionava e parou → volta o arquivo anterior
             sozinho ("revertida").
  desfazer   uma mudança aplicada volta ao arquivo anterior (também com o código).

O Jarvis nunca vê segredos do arquivo: hashes de senha, tokens e chaves viram marcadores «segredo-N»
na leitura, e os marcadores voltam aos valores reais quando ele propõe.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from orchestrator.config import Config
from orchestrator.db import DB

# deploy (sites dos projetos) e mudanças no Caddyfile nunca recarregam o Caddy ao mesmo tempo
CADDY_LOCK = asyncio.Lock()
MAX_BYTES = 200_000
MAX_PROBES = 30
PENDING = "proposta"

IMPORT_RE = re.compile(r"^\s*import\s+\S*sites-agente\S*", re.M)
API_GUARD_RE = re.compile(r"^\s*respond\s+@api\s+404\b", re.M)
ADMIN_RE = re.compile(r"^\s*admin\s+(\S+)", re.M)
SITE_RE = re.compile(r"^(?![\s#(&{])([^{\n]+?)\s*\{\s*$", re.M)
SAFE_ADMIN = ("off", "localhost", "127.0.0.1", "[::1]", "unix/")
SENSITIVE_ROOT_RE = re.compile(r"^\s*root\s+(?:\S+\s+)?(/|/data|/config|/etc|/root|/home|/srv)/?\s*$", re.M)
# (padrão, grupo com o valor secreto). Na dúvida, esconde: esconder demais só obriga o Jarvis a usar o
# marcador; esconder de menos põe um segredo no contexto do modelo.
KEY_NAMES = (
    r"[\w-]*(?:token|secret|password|passwd|pass|api[-_]?key|apikey|access[-_]?key|private[-_]?key|"
    r"client[-_]?secret|credentials?|authorization|x-api-key)"
)
SECRET_PATTERNS = [
    (re.compile(r"\$2[abxy]?\$\d\d\$[./A-Za-z0-9]{53}"), 0),  # hash bcrypt (basic_auth)
    (re.compile(r"\$(?:scrypt|argon2\w*)\$\S+"), 0),
    (re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})"), 1),
    (re.compile(r"(?i)\bbasic\s+([A-Za-z0-9+/=]{8,})"), 1),
    (re.compile(rf"(?i)\b{KEY_NAMES}\b[\"']?\s+(\"?(?!\{{)(?!(?:bearer|basic|digest)\b)[^\s\"{{}}]{{6,}}\"?)"), 1),
    (re.compile(r"(?i)\b(?:acme_)?dns\s+[\w.-]+\s+((?!\{)[^\s\"{}]{12,})"), 1),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), 0),  # JWT
    # sequência longa sem cara de caminho ou domínio (chave, hash, token de API)
    (re.compile(r"(?<![\w/.:-])(?!/)[A-Za-z0-9+_-][A-Za-z0-9+/_-]{31,}={0,2}(?![\w/.-])"), 0),
]
MARK_RE = re.compile(r"«segredo-(\d+)»")
# diretivas que mostram o valor para quem acessa o site (um segredo nunca pode parar numa delas)
EXPOSING_RE = re.compile(r"^\s*(respond|header(?!_up\b|_down\b)|redir|error|abort|templates|log|rewrite|uri|root)\b")
UPSTREAM_RE = re.compile(r"^\s*reverse_proxy\s+(.+?)\s*\{?\s*$", re.M)


class ChangeError(ValueError):
    """Pedido recusado (conflito, validação, regra). A mensagem vai para o Jarvis/painel."""


class CaddyHost(Protocol):
    async def read(self) -> str: ...
    async def writable(self) -> bool: ...
    async def validate(self, content: str) -> tuple[bool, str]: ...
    async def write(self, content: str) -> None: ...
    async def reload(self) -> tuple[bool, str]: ...


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ leitura do Caddyfile
def _strip_comments(content: str) -> str:
    return "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))


def addresses(content: str) -> list[str]:
    """Endereços dos blocos de site do nível de cima (`a.com, www.a.com {`)."""
    out: list[str] = []
    for m in SITE_RE.finditer(_strip_comments(content)):
        for addr in re.split(r"[,\s]+", m.group(1).strip()):
            if addr and addr not in out:
                out.append(addr)
    return out


def hostname(addr: str) -> str | None:
    """Domínio concreto de um endereço do Caddy (sem curinga, porta local ou placeholder)."""
    addr = addr.strip().lower()
    for scheme in ("https://", "http://"):
        addr = addr.removeprefix(scheme)
    if not addr or addr[0] in ":*{" or "*" in addr or "{" in addr:
        return None
    host = addr.split("/", 1)[0].split(":", 1)[0]
    if host in {"localhost"} or "." not in host or re.fullmatch(r"[\d.]+", host):
        return None
    return host


def hosts(content: str) -> list[str]:
    out: list[str] = []
    for addr in addresses(content):
        h = hostname(addr)
        if h and h not in out:
            out.append(h)
    return out


# ------------------------------------------------------------------ segredos fora do contexto do modelo
def redact(content: str, seed: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Troca segredos por «segredo-N» (mesmo valor, mesmo número). `seed` reaproveita a numeração de
    outra versão do arquivo, para o diff mostrar o mesmo marcador dos dois lados."""
    mapping: dict[str, str] = dict(seed or {})
    by_value = {v: k for k, v in mapping.items()}
    spans: list[tuple[int, int]] = []
    for pattern, group in SECRET_PATTERNS:
        for m in pattern.finditer(content):
            start, end = m.span(group)
            if MARK_RE.fullmatch(content[start:end].strip('"')):
                continue
            if not any(a < end and start < b for a, b in spans):
                spans.append((start, end))
    spans.sort()
    out, pos = [], 0
    for start, end in spans:
        value = content[start:end]
        mark = by_value.get(value)
        if mark is None:
            mark = f"«segredo-{len(mapping) + 1}»"
            by_value[value] = mark
            mapping[mark] = value
        out += [content[pos:start], mark]
        pos = end
    out.append(content[pos:])
    return "".join(out), mapping


def unredact(text: str, mapping: dict[str, str]) -> str:
    def put(m: re.Match) -> str:
        if m.group(0) not in mapping:
            raise ChangeError(f"marcador {m.group(0)} não existe nesta versão do Caddyfile")
        return mapping[m.group(0)]

    return MARK_RE.sub(put, text)


def unified_diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), "Caddyfile (atual)", "Caddyfile (proposto)"
    )
    return "".join(lines)


def apply_edits(content: str, edits: list[dict[str, Any]]) -> str:
    """Edições no estilo "trecho exato antes → depois" (cada trecho precisa aparecer uma vez só), ou
    `acrescentar` para um bloco novo no fim do arquivo."""
    if not edits:
        raise ChangeError("nenhuma edição")
    for i, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            raise ChangeError(f"edição {i}: formato inválido")
        if "acrescentar" in e:
            block = str(e["acrescentar"]).strip("\n")
            if not block.strip():
                raise ChangeError(f"edição {i}: 'acrescentar' vazio")
            content = content.rstrip("\n") + "\n\n" + block + "\n"
            continue
        old, new = str(e.get("antes") or ""), str(e.get("depois") or "")
        if not old:
            raise ChangeError(f"edição {i}: informe 'antes' (trecho exato do arquivo) ou use 'acrescentar'")
        count = content.count(old)
        if count != 1:
            raise ChangeError(f"edição {i}: o trecho 'antes' aparece {count} vez(es); precisa aparecer exatamente 1")
        content = content.replace(old, new, 1)
    return content


# ------------------------------------------------------------------ regras
def review(before: str, after: str, panel_url: str, managed: set[str]) -> tuple[list[str], list[str]]:
    """(bloqueios, alertas). Bloqueio: a proposta não é aceita. Alerta: vai junto do diff para você decidir."""
    blocks: list[str] = []
    alerts: list[str] = []
    if not after.strip():
        blocks.append("o Caddyfile proposto está vazio")
    if len(after.encode("utf-8")) > MAX_BYTES:
        blocks.append(f"o Caddyfile proposto passa de {MAX_BYTES // 1000} KB")
    if IMPORT_RE.search(before) and not IMPORT_RE.search(after):
        blocks.append("remove o `import` dos sites do agente (sites-agente): os projetos sairiam do ar")
    if API_GUARD_RE.search(before) and not API_GUARD_RE.search(after):
        panel = urlparse(panel_url).hostname or "painel"
        blocks.append(f"remove a proteção da API do Jarvis no {panel} (`respond @api 404`)")
    for m in ADMIN_RE.finditer(_strip_comments(after)):
        if not m.group(1).lower().startswith(SAFE_ADMIN):
            blocks.append(f"expõe a API de administração do Caddy (`admin {m.group(1)}`)")
    for h in hosts(after):
        if h in managed:
            blocks.append(f"{h} já é servido pelo agente (sites-agente): mude esse site pelo pipeline")

    old_hosts, new_hosts = set(hosts(before)), set(hosts(after))
    for h in sorted(old_hosts - new_hosts):
        alerts.append(f"tira do ar o site {h}")
    for h in sorted(new_hosts - old_hosts):
        alerts.append(f"site novo {h}: o DNS precisa apontar para o VPS (o Caddy pede o certificado sozinho)")
    if SENSITIVE_ROOT_RE.search(after) and not SENSITIVE_ROOT_RE.search(before):
        alerts.append("serve arquivos de uma pasta de sistema (`root` em /, /data, /config, /etc…)")
    if len(re.findall(r"\bbrowse\b", after)) > len(re.findall(r"\bbrowse\b", before)):
        alerts.append("liga a listagem de pastas (`browse`)")
    if len(re.findall(r"\bbasic_?auth\b", after)) < len(re.findall(r"\bbasic_?auth\b", before)):
        alerts.append("remove uma proteção por senha (`basic_auth`)")
    if re.search(r"auto_https\s+off|\btls\s+internal\b", after) and not re.search(
        r"auto_https\s+off|\btls\s+internal\b", before
    ):
        alerts.append("desliga o HTTPS público (auto_https off / tls internal)")
    if len(re.findall(r"reverse_proxy\s+agente:8080", after)) > len(re.findall(r"reverse_proxy\s+agente:8080", before)):
        alerts.append("expõe o painel do agente em mais um endereço")
    new_imports = set(re.findall(r"^\s*import\s+(\S+)", after, re.M)) - set(
        re.findall(r"^\s*import\s+(\S+)", before, re.M)
    )
    for imp in sorted(new_imports):
        alerts.append(f"importa outro arquivo ({imp}): o conteúdo dele não aparece neste diff")

    # segredos: o Jarvis não vê os valores, mas poderia tentar movê-los para onde fiquem visíveis
    _, mapping = redact(before)
    before_lines = {ln.strip() for ln in before.splitlines()}
    for mark, value in mapping.items():
        if after.count(value) > before.count(value):
            blocks.append(f"copia um segredo ({mark}) para mais um lugar")
        for line in after.splitlines():
            if value in line and line.strip() not in before_lines:
                if EXPOSING_RE.match(line):
                    blocks.append(f"coloca um segredo ({mark}) numa diretiva que o visitante vê")
                else:
                    alerts.append(f"mexe numa linha com segredo ({mark})")
    old_up = {u for m in UPSTREAM_RE.finditer(before) for u in m.group(1).split()}
    for m in UPSTREAM_RE.finditer(after):
        for up in m.group(1).split():
            host = up.split("://", 1)[-1].split(":", 1)[0]
            if up not in old_up and "." in host and not re.fullmatch(r"[\d.]+", host):
                alerts.append(f"encaminha tráfego para um endereço fora do VPS ({up})")
    return blocks, sorted(set(alerts), key=alerts.index)


# ------------------------------------------------------------------ Caddy de verdade (docker exec)
class DockerCaddyHost:
    """Lê, valida, grava e recarrega o Caddyfile pelo container do Caddy — o mesmo caminho do deploy.
    Precisa do socket do Docker (só o orquestrador tem) e do Caddyfile montado com escrita no Caddy."""

    def __init__(self, config: Config):
        self.c = config

    async def _docker(self, *args: str, data: str | None = None, timeout: float = 60) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise ChangeError("cliente docker não encontrado no container do agente") from None
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(data.encode("utf-8") if data is not None else None), timeout
            )
        except TimeoutError:
            proc.kill()
            raise ChangeError(f"docker {args[0]} não respondeu em {timeout:.0f}s") from None
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def read(self) -> str:
        rc, out, err = await self._docker("exec", self.c.caddy_container, "cat", self.c.caddy_config_path)
        if rc:
            raise ChangeError(
                f"não consegui ler {self.c.caddy_config_path} no container {self.c.caddy_container}: {err[-400:]}"
            )
        return out

    async def writable(self) -> bool:
        script = 'test -w "$1"'
        rc, _, _ = await self._docker(
            "exec", self.c.caddy_container, "sh", "-c", script, "sh", self.c.caddy_config_path
        )
        return rc == 0

    async def validate(self, content: str) -> tuple[bool, str]:
        # a cópia fica ao lado do original (imports relativos resolvem igual); pasta só leitura → /tmp
        folder = str(PurePosixPath(self.c.caddy_config_path).parent)
        script = (
            't="$1"; (cat > "$t") 2>/dev/null || { t=/tmp/.Caddyfile.proposta; cat > "$t"; }; '
            'caddy validate --config "$t" --adapter caddyfile 2>&1; rc=$?; rm -f "$t"; exit $rc'
        )
        rc, out, err = await self._docker(
            "exec",
            "-i",
            self.c.caddy_container,
            "sh",
            "-c",
            script,
            "sh",
            f"{folder}/.Caddyfile.proposta",
            data=content,
        )
        return rc == 0, caddy_errors(out + err)

    async def write(self, content: str) -> None:
        # cat > reescreve no mesmo inode: funciona com o Caddyfile montado como arquivo no container
        rc, _, err = await self._docker(
            "exec", "-i", self.c.caddy_container, "sh", "-c", 'cat > "$1"', "sh", self.c.caddy_config_path, data=content
        )
        if rc:
            raise ChangeError(
                "não consegui gravar o Caddyfile (montado só leitura no container do Caddy? tire o ':ro'): "
                + err[-300:]
            )
        if await self.read() != content:
            raise ChangeError("gravei o Caddyfile, mas o conteúdo lido de volta é diferente")

    async def reload(self) -> tuple[bool, str]:
        rc, out, err = await self._docker(
            "exec",
            self.c.caddy_container,
            "caddy",
            "reload",
            "--config",
            self.c.caddy_config_path,
            "--adapter",
            "caddyfile",
            timeout=120,
        )
        return rc == 0, caddy_errors(out + err)


def caddy_errors(output: str) -> str:
    """Só o que interessa da saída do Caddy: as linhas de erro (os logs "info" em JSON ficam de fora)."""
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("Error") or '"level":"error"' in ln]
    return "\n".join(errors or lines[-5:])[-3000:]


async def probe_hosts(targets: list[str]) -> dict[str, int]:
    """Status HTTP de https://<host>/ (0 = não conectou). Sem seguir redirect: 3xx conta como no ar."""

    async def one(client: httpx.AsyncClient, host: str) -> tuple[str, int]:
        try:
            r = await client.get(f"https://{host}/")
            return host, r.status_code
        except httpx.HTTPError:
            return host, 0

    if not targets:
        return {}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        pairs = await asyncio.gather(*(one(client, h) for h in targets))
    return dict(pairs)


def _up(status: int) -> bool:
    return 0 < status < 500


Prober = Callable[[list[str]], Awaitable[dict[str, int]]]


# ------------------------------------------------------------------ o serviço
class CaddyChanges:
    def __init__(self, config: Config, db: DB, host: CaddyHost, prober: Prober | None = None, settle_s: float = 5):
        self.c = config
        self.db = db
        self.host = host
        self.prober = prober or probe_hosts
        self.settle_s = settle_s

    def managed_hosts(self) -> set[str]:
        out: set[str] = set()
        try:
            files = sorted(self.c.caddy_sites_dir.glob("*.caddy"))
        except OSError:
            return out
        for f in files:
            try:
                out.update(hosts(f.read_text(encoding="utf-8")))
            except OSError:
                continue
        return out

    # ---- confirmação (vai para você por fora do Jarvis)
    def _key(self) -> bytes:
        key = self.db.kv_get("infra:confirm-key")
        if not key:
            key = secrets.token_hex(32)
            self.db.kv_set("infra:confirm-key", key)
        return bytes.fromhex(key)

    def confirmation(self, ch: dict[str, Any]) -> str:
        """6 letras/dígitos ligados a ESTA proposta (id + conteúdo proposto)."""
        mac = hmac.new(self._key(), f"{ch['id']}:{digest(ch['after'])}".encode(), hashlib.sha256).digest()
        return base64.b32encode(mac[:5]).decode()[:6]

    def check_confirmation(self, change_id: int, given: str) -> dict[str, Any]:
        ch = self._get(change_id)
        clean = re.sub(r"[^A-Z2-7]", "", (given or "").upper())
        if not hmac.compare_digest(clean, self.confirmation(ch)):
            raise ChangeError(
                f"confirmação da proposta #{change_id} não confere: peça ao Gabriel o código de confirmação que "
                "chegou no celular (ou está no painel, em Infra) junto com o código do autenticador"
            )
        return ch

    def summary(self, ch: dict[str, Any]) -> str:
        """Resumo calculado pelo orquestrador (não pelo Jarvis) para o push."""
        old, new = set(hosts(ch["before"])), set(hosts(ch["after"]))
        parts = [f"Motivo (do Jarvis): {ch['motivo'][:200]}"]
        if new - old:
            parts.append("Entram: " + ", ".join(sorted(new - old)))
        if old - new:
            parts.append("Saem do ar: " + ", ".join(sorted(old - new)))
        changed = sum(1 for ln in ch["diff"].splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
        parts.append(f"Linhas alteradas: {changed} · alertas: {len(ch['alerts'] or [])}")
        parts.append(f"Confirmação: {self.confirmation(ch)} (diff completo no painel → Infra)")
        return "\n".join(parts)

    # ---- leitura
    async def current(self) -> dict[str, Any]:
        content = await self.host.read()
        if MARK_RE.search(content):
            raise ChangeError("o Caddyfile contém o texto «segredo-N» literal; tire-o para usar as propostas")
        text, _ = redact(content)
        return {
            "versao": digest(content),
            "conteudo": text,
            "gravavel": await self.host.writable(),
            "sites": hosts(content),
            "sites_do_agente": sorted(self.managed_hosts()),
            "propostas_pendentes": [self.public(x) for x in self.db.infra_changes(status=PENDING)],
        }

    def public(self, ch: dict[str, Any], full: bool = False) -> dict[str, Any]:
        """Visão para o Jarvis: diff sem segredos."""
        before, mapping = redact(ch["before"])
        after, _ = redact(ch["after"], seed=mapping)
        row = {
            "id": ch["id"],
            "status": ch["status"],
            "motivo": ch["motivo"],
            "alertas": ch["alerts"],
            "criada": ch["created_at"],
        }
        if ch.get("result"):
            row["resultado"] = ch["result"]
        if full or ch["status"] == PENDING:
            row["diff"] = unified_diff(before, after)[:12000]
        return row

    # ---- proposta
    async def propose(
        self,
        motivo: str,
        base: str,
        conteudo: str | None = None,
        edicoes: list[dict[str, Any]] | None = None,
        origem: str = "jarvis",
    ) -> dict[str, Any]:
        motivo = (motivo or "").strip()
        if len(motivo) < 10:
            raise ChangeError("explique o motivo da mudança (campo 'motivo')")
        if (conteudo is None) == (not edicoes):
            raise ChangeError("mande 'edicoes' (trechos antes → depois) OU 'conteudo' (o arquivo inteiro), não os dois")
        current = await self.host.read()
        if MARK_RE.search(current):
            raise ChangeError("o Caddyfile contém o texto «segredo-N» literal; tire-o para usar as propostas")
        if base != digest(current):
            raise ChangeError(
                f"o Caddyfile mudou (versão atual {digest(current)}, você leu {base or '—'}): "
                "leia de novo antes de propor"
            )
        _, mapping = redact(current)
        if conteudo is not None:
            after = unredact(conteudo, mapping)
        else:
            clean = [
                {k: unredact(str(v), mapping) for k, v in e.items()} if isinstance(e, dict) else e for e in edicoes
            ]
            after = apply_edits(current, clean)
        if not after.endswith("\n"):
            after += "\n"
        if after == current:
            raise ChangeError("a proposta não muda nada no arquivo")
        blocks, alerts = review(current, after, self.c.panel_url, self.managed_hosts())
        if blocks:
            raise ChangeError("proposta bloqueada pelas regras: " + "; ".join(blocks))
        ok, output = await self.host.validate(after)
        if not ok:
            shown, _ = redact(output, seed=mapping)
            raise ChangeError("o Caddy recusou a configuração proposta (nada foi gravado): " + shown[-1500:])
        row = self.db.create_infra_change(
            motivo=motivo[:2000],
            origem=origem,
            base_hash=digest(current),
            before=current,
            after=after,
            diff=unified_diff(current, after),
            alerts=alerts,
            validation=output[-2000:],
        )
        return self.public(row)

    # ---- decisão
    def _get(self, change_id: int) -> dict[str, Any]:
        ch = self.db.infra_change(change_id)
        if not ch:
            raise ChangeError(f"proposta #{change_id} não existe")
        return ch

    def reject(self, change_id: int, motivo: str = "") -> dict[str, Any]:
        ch = self._get(change_id)
        if ch["status"] != PENDING:
            raise ChangeError(f"a proposta #{change_id} está '{ch['status']}', não pendente")
        self.db.update_infra_change(change_id, status="rejeitada", result=motivo[:500] or "rejeitada", decided_at=now())
        return self.public(self._get(change_id))

    async def _restore(self, content: str) -> str:
        try:
            await self.host.write(content)
            ok, out = await self.host.reload()
            return "arquivo anterior restaurado" + ("" if ok else f" (mas o reload falhou: {out[-300:]})")
        except ChangeError as e:
            return f"FALHA AO RESTAURAR, confira o Caddy agora: {e}"

    async def approve(self, change_id: int) -> dict[str, Any]:
        async with CADDY_LOCK:
            ch = self._get(change_id)
            if ch["status"] != PENDING:
                raise ChangeError(f"a proposta #{change_id} está '{ch['status']}', não pendente")
            current = await self.host.read()
            if digest(current) != ch["base_hash"]:
                self.db.update_infra_change(
                    change_id, status="obsoleta", result="o Caddyfile mudou depois da proposta", decided_at=now()
                )
                raise ChangeError(f"o Caddyfile mudou desde a proposta #{change_id}: peça uma proposta nova")
            ok, output = await self.host.validate(ch["after"])
            if not ok:
                self.db.update_infra_change(change_id, status="falhou", result=output[-1500:], decided_at=now())
                raise ChangeError(
                    "o Caddy recusou a configuração na hora de aplicar (nada foi gravado): " + output[-800:]
                )
            removed = set(hosts(current)) - set(hosts(ch["after"]))
            targets = [h for h in hosts(current) if h not in removed][:MAX_PROBES]
            before_status = await self.prober(targets)
            backup = self.c.infra_dir / f"caddyfile-{change_id}-antes-{datetime.now(UTC):%Y%m%dT%H%M%S}"
            backup.write_text(current, encoding="utf-8")
            await self.host.write(ch["after"])
            ok, output = await self.host.reload()
            if not ok:
                restored = await self._restore(current)
                result = f"o Caddy recusou o reload; {restored}. Saída: {output[-600:]}"
                self.db.update_infra_change(change_id, status="revertida", result=result, decided_at=now())
                return self.public(self._get(change_id))
            await asyncio.sleep(self.settle_s)
            after_status = await self.prober(targets)
            broke = [h for h in targets if _up(before_status.get(h, 0)) and not _up(after_status.get(h, 0))]
            if broke:
                restored = await self._restore(current)
                detail = ", ".join(f"{h} ({before_status.get(h)}→{after_status.get(h)})" for h in broke)
                result = f"desfeita automaticamente: pararam de responder {detail}; {restored}"
                self.db.update_infra_change(change_id, status="revertida", result=result, decided_at=now())
                return self.public(self._get(change_id))
            summary = ", ".join(f"{h} {after_status.get(h)}" for h in targets) or "nenhum site para medir"
            self.db.update_infra_change(
                change_id,
                status="aplicada",
                result=f"aplicada e recarregada; sites depois: {summary}; backup em {backup}",
                decided_at=now(),
            )
            for other in self.db.infra_changes(status=PENDING):
                if other["base_hash"] == ch["base_hash"]:
                    self.db.update_infra_change(
                        other["id"], status="obsoleta", result=f"o arquivo mudou com a #{change_id}", decided_at=now()
                    )
            return self.public(self._get(change_id))

    async def undo(self, change_id: int) -> dict[str, Any]:
        async with CADDY_LOCK:
            ch = self._get(change_id)
            if ch["status"] != "aplicada":
                raise ChangeError(f"só dá para desfazer uma mudança aplicada (a #{change_id} está '{ch['status']}')")
            current = await self.host.read()
            if digest(current) != digest(ch["after"]):
                raise ChangeError("o Caddyfile mudou depois desta mudança: desfaça antes as mais recentes")
            ok, output = await self.host.validate(ch["before"])
            if not ok:
                raise ChangeError("a versão anterior não valida mais no Caddy (nada foi gravado): " + output[-800:])
            await self.host.write(ch["before"])
            ok, output = await self.host.reload()
            if not ok:
                restored = await self._restore(current)
                raise ChangeError(f"o Caddy recusou o reload da versão anterior; {restored}")
            self.db.update_infra_change(
                change_id, status="desfeita", result="voltou ao arquivo anterior", decided_at=now()
            )
            return self.public(self._get(change_id))


def as_json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False)
