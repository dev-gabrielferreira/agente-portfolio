import json
import shutil
from pathlib import Path

import pytest

from orchestrator.config import REPO_DIR, Config
from orchestrator.db import DB
from orchestrator.deployer import FakeDeployer
from orchestrator.notify import Notifier
from orchestrator.pipeline import Pipeline
from orchestrator.publisher import NullPublisher
from orchestrator.runner import RunResult, RunSpec
from orchestrator.workspace import Workspace

STUB_CHECK = """#!/usr/bin/env bash
# gate falso para testes: falha se existir .fail-check na raiz do projeto
cd "$(dirname "$0")/.."
if [[ -f .fail-check ]]; then echo "FAILED tests/acceptance/test_f01.py::test_algo - AssertionError"; exit 1; fi
echo "tudo verde"
"""

STUB_MUTATION = """#!/usr/bin/env bash
# mutation falso: score vem de .mutation-score (padrão 90)
cd "$(dirname "$0")/.."
mkdir -p .harness/evidence
score=$(cat .mutation-score 2>/dev/null || echo 90)
echo "{\\"score\\": $score, \\"killed\\": 9, \\"survived\\": 1, \\"total\\": 10, \\"survivors\\": [\\"app.services.x__mutmut_1: survived\\"]}" > .harness/evidence/mutation.json
"""

STRONG_ACCEPTANCE = """import pytest


@pytest.mark.feature("{fid}")
def test_{low}_criterio_de_aceite(client):
    r = client.get("/health")
    assert r.json() == {{"status": "ok"}}
"""


def detailed_ticket(tid: str, title: str, features: list[str]) -> str:
    """Ticket no formato da skill tickets-verticais (passa na validação do orquestrador)."""
    feats = ", ".join(features) or "—"
    return f"""# {tid} — {title}

**Features:** {feats} · **Bloqueado por:** — · **Tamanho:** M

## Objetivo

O usuário consegue ver a curva de carga do SIN do último mês num gráfico diário, com a média
destacada. É o núcleo do painel e prova que a ingestão dos dados da ONS funciona de ponta a ponta.

## Contexto

A fundação já existe (FastAPI com /health, SQLite em /data). O PLAN define a ingestão na seção
"Dados" e o ADR 0001 escolhe FastAPI; os termos "carga" e "subsistema" estão no CONTEXT.md.

## O que construir

- **Dados:** tabela `load_points` (subsystem, day, mwavg) com índice por (subsystem, day).
- **Regra de negócio:** `monthly_curve(points, month)` em `app/services/curve.py`, função pura que
  agrega por dia e calcula a média do mês, ignorando dias sem dado.
- **API:** `GET /api/curve?month=2026-08` devolve os pontos e a média; 422 para mês inválido.
- **Interface:** página inicial com o gráfico, estado vazio "sem dados para o mês" e erro legível.

## Arquivos e módulos

- criar `app/services/curve.py` — `monthly_curve`
- alterar `app/main.py` — registrar a rota `/api/curve`
- criar `app/templates/curve.html` — gráfico e estados

## Contratos

`GET /api/curve?month=2026-08` → 200 `{{"points": [{{"day": "2026-08-01", "mwavg": 71234.5}}], "mean": 70110.2}}`;
422 quando o mês não segue AAAA-MM.

## Critérios de aceite

- [ ] Dado agosto com 31 dias de dados, quando abro o painel, então vejo 31 pontos e a média destacada.
- [ ] Dado um mês sem dados, quando abro o painel, então vejo "sem dados para o mês" e nenhum erro.
- [ ] Dado o parâmetro month=2026-13, quando chamo a API, então recebo 422 com a mensagem do formato.

## Costuras de teste

- `monthly_curve` (função pura): mês completo, dias faltando, mês vazio.
- API pelo TestClient: 200 com corpo, 422 no mês inválido.

## Fora do escopo

- Comparação entre subsistemas (outro ticket).
- Exportar CSV.

## Riscos e armadilhas

- Fuso: os dados da ONS vêm em horário de Brasília; agregue por dia local.
"""


class FakeRunner:
    """Simula cada papel do Claude Code escrevendo arquivos como o agente faria."""

    def __init__(self):
        self.calls: list[RunSpec] = []
        self.eval_queue: list[dict] = []
        self.tester_bugs: list[list[dict]] = []
        self.tester_writes_weak = False
        self.security_blockers = False
        self.review_blockers = False
        self.rate_limit_next = False
        self.builder_fixes_gate = True
        self.builder_tampers = False
        self.stack_tags = ["ui"]
        self.discovery_rounds: list[list[dict]] = []  # perguntas por rodada (vazio = padrão: 1 rodada)
        self.plan_tickets = 1
        self.plan_starter = "python-fastapi"
        self.plan_breaks: str = ""  # "manifest" | "tickets" | "vago": plano inválido para testar a validação
        self.plan_fixes_on_retry = False  # corrige o plano quando o orquestrador devolve os problemas

    def cancel(self) -> None:
        pass

    def _plan(self, pdir: Path, task: str = "") -> RunResult:
        breaks = self.plan_breaks
        if self.plan_fixes_on_retry and "foi **reprovada** pela validação" in task:
            breaks = ""
        (pdir / "docs").mkdir(exist_ok=True)
        (pdir / "docs" / "PLAN.md").write_text("# Plano\n\n" + "Arquitetura e decisões. " * 20)
        (pdir / "docs" / "adr" / "0001-framework.md").write_text("# 0001 — FastAPI\n\nDecisão: FastAPI.")
        manifest = {
            "version": 1,
            "starter": self.plan_starter,
            "summary": "FastAPI + HTMX + SQLite",
            "start": "python -m uvicorn app.main:app --host 127.0.0.1 --port {port}",
            "health": "/health",
            "backend": {
                "language": "python",
                "package": "app",
                "asgi": "app.main:app",
                "openapi": True,
                "domain": "app/services",
            },
            "frontend": None,
            "ui_paths": ["app/templates"],
        }
        if breaks == "manifest":
            manifest.pop("start")
        (pdir / ".harness" / "stack.json").write_text(json.dumps(manifest))
        feats = json.loads((pdir / ".harness" / "features.json").read_text())["features"]
        tickets = []
        (pdir / ".harness" / "tickets").mkdir(exist_ok=True)
        for i in range(1, self.plan_tickets + 1):
            tid = f"T{i:02d}"
            covers = [f["id"] for f in feats] if i == self.plan_tickets and breaks != "tickets" else []
            text = f"# {tid}\n\nFatia {i}." if breaks == "vago" else detailed_ticket(tid, f"Fatia {i}", covers)
            (pdir / ".harness" / "tickets" / f"{tid}.md").write_text(text)
            tickets.append(
                {
                    "id": tid,
                    "title": f"Fatia {i}",
                    "file": f".harness/tickets/{tid}.md",
                    "features": covers,
                    "blocked_by": [f"T{i - 1:02d}"] if i > 1 else [],
                    "status": "todo",
                }
            )
        (pdir / ".harness" / "tickets.json").write_text(json.dumps({"tickets": tickets}))
        return RunResult(
            ok=True,
            structured={
                "summary": "Plano: monólito FastAPI.",
                "architecture": "um container",
                "stack": "FastAPI + DuckDB",
                "starter": self.plan_starter,
                "stack_tags": self.stack_tags,
                "adrs": [{"title": "Framework", "decision": "FastAPI"}],
                "tickets": [{"id": t["id"], "title": t["title"]} for t in tickets],
                "risks": ["dados da ONS instáveis"],
            },
        )

    async def run(self, spec: RunSpec) -> RunResult:
        self.calls.append(spec)
        (spec.project_dir / ".harness").mkdir(exist_ok=True)
        (spec.project_dir / ".harness" / "TASK.md").write_text(spec.task)
        if self.rate_limit_next:
            self.rate_limit_next = False
            return RunResult(ok=False, error="Claude usage limit reached", rate_limited=True)
        role, pdir = spec.role, spec.project_dir
        if role == "planner" and spec.json_schema and "questions" in spec.json_schema["properties"]:
            first = "rodada 1 de" in spec.task
            questions = list(self.discovery_rounds.pop(0)) if self.discovery_rounds else None
            if questions is None:
                questions = (
                    [{"id": "q1", "question": "Qual período?", "why": "volume", "default": "2 anos"}] if first else []
                )
            return RunResult(
                ok=True,
                structured={
                    "project_name": "Painel de Energia",
                    "one_liner": "Painel da carga do SIN",
                    "understanding": "Um painel de energia.",
                    "questions": questions,
                },
            )
        if role == "planner" and spec.json_schema and "tickets" in spec.json_schema["properties"]:
            return self._plan(pdir, spec.task)
        if role == "planner":
            (pdir / "SPEC.md").write_text("# Spec\n\n" + "Visão do produto. " * 30)
            feats = {
                "features": [
                    {
                        "id": "F01",
                        "title": "Gráfico de carga",
                        "priority": 1,
                        "acceptance": ["mostra a curva"],
                        "passes": False,
                    }
                ]
            }
            (pdir / ".harness" / "features.json").write_text(json.dumps(feats))
            return RunResult(
                ok=True,
                structured={
                    "summary": "Painel",
                    "features": [{"id": "F01", "title": "Gráfico"}],
                    "stack": "FastAPI + DuckDB",
                    "stack_tags": self.stack_tags,
                    "needs_design": True,
                    "env_vars": [],
                    "risks": [],
                },
            )
        if role == "designer":
            (pdir / "design" / "mockups").mkdir(parents=True, exist_ok=True)
            (pdir / "design" / "DESIGN.md").write_text("# Design\nSala de controle.")
            (pdir / "design" / "tokens.css").write_text(":root { --accent: #4fd1a5; }")
            (pdir / "design" / "mockups" / "home.html").write_text("<h1>Home</h1><script>alert(1)</script>")
            shots = pdir / ".harness" / "evidence" / "design"
            shots.mkdir(parents=True, exist_ok=True)
            (shots / "home-desktop.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return RunResult(
                ok=True,
                structured={
                    "concept": "sala de controle",
                    "screens": [{"name": "Home", "mockup": "design/mockups/home.html"}],
                },
            )
        if role == "builder":
            data = json.loads((pdir / ".harness" / "features.json").read_text())
            for f in data["features"]:
                f["passes"] = True
            (pdir / ".harness" / "features.json").write_text(json.dumps(data))
            (pdir / "app" / "services").mkdir(parents=True, exist_ok=True)
            (pdir / "app" / "services" / "chart.py").write_text("VALUE = 1\n")
            if self.builder_fixes_gate:
                (pdir / ".fail-check").unlink(missing_ok=True)
            if self.builder_tampers:  # simula escrita pelo shell (sed/cat >), que os hooks não veem
                self.builder_tampers = False
                for t in (pdir / "tests" / "acceptance").glob("test_*.py"):
                    t.write_text("def test_enfraquecido():\n    assert True\n")
                with (pdir / "scripts" / "check.sh").open("a") as f:
                    f.write("\nexit 0\n")
                (pdir / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests/acceptance\n")
            return RunResult(ok=True, cost_usd=1.5, turns=40)
        if role == "tester":
            data = json.loads((pdir / ".harness" / "features.json").read_text())
            for f in data["features"]:
                body = STRONG_ACCEPTANCE.format(fid=f["id"], low=f["id"].lower())
                if self.tester_writes_weak:
                    body = body.replace('assert r.json() == {"status": "ok"}', "assert r.status_code == 200")
                (pdir / "tests" / "acceptance" / f"test_{f['id'].lower()}.py").write_text(body)
            bugs = self.tester_bugs.pop(0) if self.tester_bugs else []
            return RunResult(
                ok=True,
                turns=30,
                structured={
                    "summary": "suíte de aceitação",
                    "tests_written": 3,
                    "bugs": bugs,
                    "mutation": {"score": 90},
                    "disputes": [
                        {"test": "tests/acceptance/test_f01.py::x", "verdict": "kept", "reason": "a SPEC exige isso"}
                    ]
                    if (pdir / ".harness" / "TEST_DISPUTES.md").exists()
                    else [],
                },
            )
        if role == "evaluator":
            report = self.eval_queue.pop(0) if self.eval_queue else passing_eval()
            return RunResult(ok=True, structured=report)
        if role == "security":
            findings = (
                [{"severity": "blocker", "title": "SQL injection", "fix": "use parâmetros"}]
                if self.security_blockers
                else []
            )
            self.security_blockers = False
            return RunResult(ok=True, structured={"summary": "ok", "findings": findings})
        if role == "reviewer":
            findings = (
                [
                    {
                        "severity": "blocker",
                        "title": "fraude de teste",
                        "fix": "remova o if de ambiente",
                        "source": "code-reviewer",
                    }
                ]
                if self.review_blockers
                else []
            )
            self.review_blockers = False
            return RunResult(ok=True, structured={"summary": "código ok", "findings": findings})
        if role == "retro":
            return RunResult(
                ok=True,
                structured={
                    "lessons": [
                        {"text": "Rode o gate antes de encerrar", "kind": "check", "evidence": "gates falharam"}
                    ]
                },
            )
        raise AssertionError(f"papel inesperado {role}")


def passing_eval() -> dict:
    return {
        "verdict": "PASS",
        "summary": "bom",
        "scores": {"functionality": 9, "product_depth": 8, "design": 8, "code_quality": 8, "accessibility": 9},
        "findings": [],
    }


def failing_eval() -> dict:
    return {
        "verdict": "NEEDS_WORK",
        "summary": "gráfico não carrega",
        "scores": {"functionality": 4, "product_depth": 7, "design": 7, "code_quality": 7, "accessibility": 9},
        "findings": [{"severity": "blocker", "what": "gráfico vazio", "expected": "curva", "where": "/"}],
    }


@pytest.fixture
def config(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    shutil.copytree(REPO_DIR / "harness", harness)
    scripts = harness / "project-template" / "scripts"
    (scripts / "check.sh").write_text(STUB_CHECK)
    (scripts / "mutation.sh").write_text(STUB_MUTATION)
    for var in ("GITHUB_TOKEN", "NOTIFY_URL", "STAGING_AUTH_PASSWORD", "FIGMA_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    c = Config()
    c.base_dir = tmp_path / "srv"
    c.harness_dir = harness
    c.run_as = ""
    c.admin_password = "senha-teste"
    c.session_secret = "x" * 32
    c.panel_https_only = False
    c.git_email = "agente@test"
    c.figma_enabled = False
    c.ensure_dirs()
    return c


@pytest.fixture
def env(config):
    db = DB(config.db_path)
    runner, deployer, publisher = FakeRunner(), FakeDeployer(), NullPublisher()
    notifier = Notifier("")
    pipeline = Pipeline(config, db, runner, deployer, publisher, Workspace(config), notifier)
    return pipeline, db, runner, deployer, publisher, notifier


def project_dir(pipeline, job) -> Path:
    project = pipeline.db.project(job["project_id"])
    return pipeline.ws.project_dir(project["slug"])
