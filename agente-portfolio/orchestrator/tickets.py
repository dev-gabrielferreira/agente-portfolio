"""Validação computacional dos tickets que o planner escreve para o builder.

Cada ticket vira uma sessão do builder com contexto limpo: ele só sabe o que está no ticket, no plano
e no código. Ticket vago vira adivinhação (e retrabalho), então o orquestrador confere a forma antes
de aprovar o plano — seções obrigatórias com conteúdo, critérios de aceite observáveis em número
suficiente, arquivos citados, fronteira do escopo e nada de "a definir". O conteúdo (se a decisão é
boa) continua sendo julgado pelo Gabriel na aprovação do plano; aqui só se garante que está escrito.

Formato completo e exemplo: skill `tickets-verticais`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MIN_CHARS = 1200
MIN_CRITERIA = 3
MIN_CRITERION_CHARS = 25

# seção (prefixo normalizado do título "## …") → (nome exibido, mínimo de caracteres de conteúdo)
REQUIRED = {
    "objetivo": ("Objetivo", 80),
    "contexto": ("Contexto", 60),
    "o que construir": ("O que construir", 250),
    "arquivos": ("Arquivos e módulos", 20),
    "criterios": ("Critérios de aceite", 0),  # conferido pelos checkboxes
    "costuras": ("Costuras de teste", 40),
    "fora do escopo": ("Fora do escopo", 10),
}

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$", re.M)
BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S", re.M)
PATH_RE = re.compile(r"`[^`\s]*[/.][^`\s]*`")
PLACEHOLDER_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\[[ xX]\]\s+)?(?:\.\.\.|…|-)\s*$", re.M)
FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
PLACEHOLDER_WORD_RE = re.compile(r"\bTBD\b|\ba definir\b|\ba preencher\b|<preencher>|\[preencher\]", re.I)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def sections(text: str) -> dict[str, str]:
    """Conteúdo de cada seção `## …`, chaveado pela seção obrigatória que ela satisfaz."""
    out: dict[str, str] = {}
    # títulos dentro de blocos de código (ex.: um markdown de exemplo) não abrem seção
    masked = FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    matches = list(HEADING_RE.finditer(masked))
    for i, m in enumerate(matches):
        title = _norm(m.group(1))
        body = text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
        for key in REQUIRED:
            if title.startswith(key) and key not in out:
                out[key] = body
                break
    return out


def criteria(text: str) -> list[str]:
    body = sections(text).get("criterios", "")
    return [c for c in CHECKBOX_RE.findall(body)]


def check(ticket: dict[str, Any], text: str) -> list[str]:
    """Problemas de um ticket (lista vazia = detalhado o bastante para uma sessão de contexto limpo)."""
    tid = str(ticket.get("id") or "?")
    problems: list[str] = []
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not (first.startswith("# ") and tid in first):
        problems.append(f"começar com o título '# {tid} — …'")
    if len(text.strip()) < MIN_CHARS:
        problems.append(f"raso ({len(text.strip())} caracteres; um ticket detalhado tem {MIN_CHARS}+)")
    found = sections(text)
    for key, (label, minimum) in REQUIRED.items():
        body = found.get(key)
        if body is None:
            problems.append(f"falta a seção '## {label}'")
        elif minimum and len(body) < minimum:
            problems.append(f"seção '{label}' curta demais ({len(body)} caracteres)")
    if "arquivos" in found and not PATH_RE.search(found["arquivos"]):
        problems.append("'Arquivos e módulos' não cita nenhum caminho entre crases (ex.: `app/domain/leituras.py`)")
    if "costuras" in found and not BULLET_RE.search(found["costuras"]):
        problems.append("'Costuras de teste' precisa listar onde cada teste toca (um item por costura)")
    if "fora do escopo" in found and not BULLET_RE.search(found["fora do escopo"]):
        problems.append("'Fora do escopo' precisa listar ao menos um item")
    if "criterios" in found:
        items = criteria(text)
        if len(items) < MIN_CRITERIA:
            problems.append(f"{len(items)} critério(s) de aceite em checkbox; o mínimo é {MIN_CRITERIA}")
        short = [c for c in items if len(c) < MIN_CRITERION_CHARS]
        if short:
            problems.append(f"critério vago demais: '{short[0][:60]}' (descreva entrada, ação e resultado observável)")
    for fid in ticket.get("features") or []:
        if str(fid) not in text:
            problems.append(f"não cita a feature {fid} que diz cobrir")
    prose = FENCE_RE.sub("", text)  # `...` dentro de exemplo de código não é pendência
    if PLACEHOLDER_LINE_RE.search(prose) or PLACEHOLDER_WORD_RE.search(prose):
        problems.append("tem marcador em aberto (…, TBD, 'a definir'): decida ou mova para 'Fora do escopo'")
    return [f"ticket {tid}: {p}" for p in problems]


def coverage(tickets: list[tuple[dict[str, Any], str]], features: list[dict[str, Any]]) -> list[str]:
    """Cada feature pendente precisa de, no mínimo, tantos critérios nos tickets que a cobrem quantos
    critérios de aceite ela tem na spec (senão algum aceite ficou sem fatia que o construa)."""
    problems = []
    for feat in features:
        if feat.get("passes"):
            continue
        fid = feat.get("id")
        covering = [text for t, text in tickets if fid in (t.get("features") or [])]
        if not covering:
            continue  # "features sem ticket" já é apontado pelo plano
        need = len(feat.get("acceptance") or [])
        have = sum(len(criteria(text)) for text in covering)
        if have < need:
            problems.append(
                f"feature {fid}: {need} critério(s) de aceite na spec, mas os tickets que a cobrem somam {have}"
            )
    return problems
