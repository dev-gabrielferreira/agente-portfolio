"""O pipeline: do pedido à produção, com guias antes e sensores depois de cada passo.

    create:   init → discovery ⇄ [respostas, em rodadas] → spec → plan → [aprovação da spec + plano]
              → design* → [aprovação do design] → build (um ticket por sessão) ⇄ gates ⇄ tests → staging
              → evaluate → review → [aprovação do deploy] → production → publish → retro
    change:   init → discovery ⇄ [respostas, se houver] → spec → plan → design* → build → … (igual daqui em diante)
    incident: init → build → gates ⇄ tests → staging → evaluate → review → [aprovação] → production → …
    adopt:    init (clone) → build (onboarding) → gates ⇄ tests → … → production → …

    * design só quando o projeto tem interface (tag `ui`) e a spec pede (`needs_design`).

A descoberta é uma sabatina em rodadas (até DISCOVERY_ROUNDS); a spec diz o quê, o plano diz como
(arquitetura livre com ADRs, manifesto .harness/stack.json e tickets em fatias verticais). O build
executa um ticket por sessão, com contexto limpo; os sensores rodam depois do último ticket.

Papéis (cada um com suas skills, MCPs e plugins — ver harness/catalog.toml):
planner · designer · builder · test-engineer · evaluator · security-reviewer · code-reviewer · retro

Roteamento dos sensores:
  - falha no gate (lint, tipos, testes, cobertura, contrato, segredos) → builder corrige o código;
  - testes do test-engineer ausentes, fracos, sem rastreio, mutation score baixo ou disputa aberta
    → test-engineer (o builder não pode editar esses testes);
  - staging, avaliador, revisão (segurança + código) ou você reprovando → builder.
Passou de MAX_FIX_ROUNDS (builder) ou MAX_TEST_ROUNDS (testes), o job para e espera você.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orchestrator import integrity, safefs, schemas
from orchestrator import stack as stack_manifest
from orchestrator import tickets as ticket_rules
from orchestrator.config import Config
from orchestrator.db import DB
from orchestrator.deployer import Deployer, DeployError
from orchestrator.knowledge import KnowledgeBase
from orchestrator.notify import Notifier
from orchestrator.prompts import render
from orchestrator.publisher import Publisher, PublishError
from orchestrator.runner import Runner, RunResult, RunSpec
from orchestrator.skills import Library
from orchestrator.workspace import ShellError, Workspace, slugify, valid_slug

log = logging.getLogger(__name__)

WRITE_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit"]
TICKET_MAX_CHARS = 24000  # ticket detalhado cabe inteiro no TASK do builder

SECRET_PATTERNS = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})"
)

FIRST_PHASE_AFTER_INIT = {
    "create": "discovery",
    "change": "discovery",
    "incident": "build",
    "adopt": "build",
}


class Pipeline:
    def __init__(
        self,
        config: Config,
        db: DB,
        runner: Runner,
        deployer: Deployer,
        publisher: Publisher,
        workspace: Workspace,
        notifier: Notifier,
        library: Library | None = None,
        knowledge: KnowledgeBase | None = None,
    ):
        self.c = config
        self.db = db
        self.runner = runner
        self.deployer = deployer
        self.publisher = publisher
        self.ws = workspace
        self.notifier = notifier
        self.lib = library or Library(
            config.harness_dir,
            config.vendor_dir,
            {**os.environ, "FIGMA_ENABLED": "true" if config.figma_enabled else ""},
        )
        self.kb = knowledge or KnowledgeBase(config, db)
        self.current_job_id: int | None = None
        self._pause_requested: set[int] = set()
        self._integrity_notes: dict[int, list[str]] = {}
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # =====================================================================================
    # Worker
    # =====================================================================================
    async def worker(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.db.reset_running_jobs()
        while True:
            job = self.db.next_runnable_job()
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5)
                except TimeoutError:
                    pass
                continue
            await self.run_job(job["id"])

    def wake(self) -> None:
        """Pode ser chamado de threads do FastAPI (rotas síncronas): agenda no loop do worker."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake.set)

    async def run_job(self, job_id: int) -> None:
        """Executa fases em sequência até o job terminar ou precisar esperar."""
        self.current_job_id = job_id
        self.db.update_job(job_id, status="running", not_before="")
        try:
            while True:
                job = self.db.job(job_id)
                if job is None or job["status"] != "running":
                    return
                if job_id in self._pause_requested:
                    self._pause_requested.discard(job_id)
                    await self._wait_human(job_id, "Pausado pelo operador. Clique em 'Tentar de novo' para continuar.")
                    return
                phase = "review" if job["phase"] == "security" else job["phase"]  # jobs antigos
                handler = getattr(self, f"phase_{phase}", None)
                if handler is None:
                    raise RuntimeError(f"fase desconhecida: {phase}")
                self.db.event(job_id, "phase", f"▶ {phase}")
                next_phase = await handler(job)
                job = self.db.job(job_id)
                if job is None or job["status"] != "running":
                    return
                if next_phase is None:
                    return
                self.db.update_job(job_id, phase=next_phase)
        except Exception as e:
            log.exception("erro no job %s", job_id)
            self.db.event(job_id, "error", f"erro interno: {e}", {"trace": traceback.format_exc()[-4000:]})
            await self._wait_human(job_id, f"Erro interno na fase atual: {e}. Corrija e clique em 'Tentar de novo'.")
        finally:
            self.current_job_id = None
            self._refresh_knowledge(job_id)

    def _refresh_knowledge(self, job_id: int) -> None:
        """Job terminou ou parou: a nota do projeto na base de conhecimento reflete o estado novo."""
        job = self.db.job(job_id)
        if not job:
            return
        try:
            self.kb.refresh_project(job["project_id"])
        except Exception:
            log.exception("falha ao atualizar a base de conhecimento (job %s)", job_id)

    # =====================================================================================
    # Ações vindas do painel
    # =====================================================================================
    def _reserved_slugs(self) -> set[str]:
        """Endereços que já são seus no Caddyfile principal (ex.: energia.gabrielfdev.com → 'energia'):
        um projeto novo com esse slug disputaria o domínio e o deploy falharia no reload do Caddy."""
        try:
            known = json.loads(self.db.kv_get("infra:caddy-hosts", "[]"))
        except ValueError:
            return set()
        suffix = "." + self.c.domain
        labels = {h[: -len(suffix)] for h in known if isinstance(h, str) and h.endswith(suffix)}
        return labels | {x.removesuffix("-staging") for x in labels if x.endswith("-staging")}

    def _unique_slug(self, base: str, avoid_reserved: bool = True) -> str:
        base = slugify(base) or "projeto"
        if len(base) < 3:
            base = f"projeto-{base}"
        reserved = self._reserved_slugs() if avoid_reserved else set()
        slug, n = base, 2
        while self.db.project_by_slug(slug) or not valid_slug(slug) or slug in reserved:
            slug = f"{base[:36]}-{n}"
            n += 1
        return slug

    def new_project(self, request: str, name: str = "") -> dict[str, Any]:
        name = name.strip() or "Novo projeto"
        project = self.db.create_project(self._unique_slug(name), name)
        job = self.db.create_job(project["id"], "create", request.strip(), "init")
        self.db.update_project(project["id"], status="building")
        self.wake()
        return job

    def adopt_project(self, name: str, repo_url: str, request: str) -> dict[str, Any]:
        if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+?(\.git)?$", repo_url.strip()):
            raise ValueError("informe a URL https do repositório no GitHub")
        repo_url = repo_url.strip().removesuffix(".git")
        project = self.db.create_project(
            self._unique_slug(name, avoid_reserved=False), name.strip(), kind="adopted", repo_url=repo_url
        )
        self.db.update_project(project["id"], min_coverage=0, min_mutation=0, status="building")
        job = self.db.create_job(project["id"], "adopt", request.strip(), "init")
        self.wake()
        return job

    def new_job(self, project_id: int, type_: str, request: str) -> dict[str, Any]:
        if type_ not in {"change", "incident"}:
            raise ValueError("tipo de job inválido")
        if self.db.active_jobs(project_id):
            raise ValueError("este projeto já tem um job em andamento")
        job = self.db.create_job(project_id, type_, request.strip(), "init")
        self.wake()
        return job

    async def submit_answers(self, job_id: int, answers: dict[str, str], name: str = "", slug: str = "") -> None:
        job = self._require(job_id, "answers")
        project = self.db.project(job["project_id"])
        assert project
        if name.strip() and name.strip() != project["name"]:
            self.db.update_project(project["id"], name=name.strip())
        if slug and slug != project["slug"] and job["type"] == "create" and not project["prod_tag"]:
            await self._rename_project(project, slug)
        state = dict(job["questions"]) if isinstance(job["questions"], dict) else {}
        round_ = int(state.get("round") or 1)
        history = [
            *(state.get("history") or []),
            {"round": round_, "items": state.get("items", []), "answers": answers},
        ]
        state.update(history=history, items=[])
        merged = {**(job["answers"] or {}), **answers}  # guarda também os feedbacks (_spec_feedback…)
        # outra rodada só se ainda cabe: o planner decide se as respostas abriram decisões novas
        nxt = "discovery" if round_ < max(1, self.c.discovery_rounds) else "spec"
        self.db.update_job(job_id, questions=state, answers=merged, status="queued", waiting_for="", phase=nxt)
        self.db.event(job_id, "human", f"respostas da rodada {round_} enviadas")
        self.wake()

    async def _rename_project(self, project: dict, slug: str) -> None:
        if not valid_slug(slug) or self.db.project_by_slug(slug):
            raise ValueError(f"endereço inválido ou já usado: {slug}")
        old = self.ws.project_dir(project["slug"])
        if old.exists():
            shutil.move(str(old), str(self.ws.project_dir(slug)))
        self.db.update_project(project["id"], slug=slug)

    def approve_spec(self, job_id: int) -> None:
        job = self._require(job_id, "spec_approval")
        nxt = "design" if self._needs_design(job) else "build"
        self.db.update_job(job_id, status="queued", waiting_for="", phase=nxt, fix_round=0)
        self.db.event(job_id, "human", "spec e plano aprovados")
        self.wake()

    def revise_spec(self, job_id: int, feedback: str) -> None:
        job = self._require(job_id, "spec_approval")
        answers = job["answers"] or {}
        answers["_spec_feedback"] = feedback.strip()
        self.db.update_job(job_id, answers=answers, status="queued", waiting_for="", phase="spec")
        self.db.event(job_id, "human", "ajustes na spec pedidos", {"feedback": feedback})
        self.wake()

    def approve_design(self, job_id: int) -> None:
        self._require(job_id, "design_approval")
        self.db.update_job(job_id, status="queued", waiting_for="", phase="build", fix_round=0)
        self.db.event(job_id, "human", "design aprovado")
        self.wake()

    def revise_design(self, job_id: int, feedback: str) -> None:
        job = self._require(job_id, "design_approval")
        answers = job["answers"] or {}
        answers["_design_feedback"] = feedback.strip()
        self.db.update_job(job_id, answers=answers, status="queued", waiting_for="", phase="design")
        self.db.event(job_id, "human", "ajustes no design pedidos", {"feedback": feedback})
        self.wake()

    def approve_deploy(self, job_id: int) -> None:
        self._require(job_id, "deploy_approval")
        self.db.update_job(job_id, status="queued", waiting_for="", phase="production")
        self.db.event(job_id, "human", "deploy em produção aprovado")
        self.wake()

    async def request_changes(self, job_id: int, feedback: str) -> None:
        job = self._require(job_id, "deploy_approval", "human")
        project = self.db.project(job["project_id"])
        assert project
        if job["waiting_for"] == "human" and job["phase"] in {"discovery", "spec", "plan", "design"}:
            # parou antes do código (ex.: plano reprovado na validação): o ajuste volta para quem planeja
            answers = dict(job["answers"] or {})
            key = "_design_feedback" if job["phase"] == "design" else "_spec_feedback"
            answers[key] = feedback.strip()
            self.db.update_job(
                job_id, answers=answers, status="queued", waiting_for="", message="", fix_round=0, tests_round=0
            )
            self.db.event(job_id, "human", f"ajustes pedidos na fase {job['phase']}", {"feedback": feedback})
            self.wake()
            return
        text = f"# Ajustes pedidos pelo Gabriel (revisão humana)\n\n{feedback.strip()}\n"
        await self.ws.write(self.ws.project_dir(project["slug"]), ".harness/NEXT_FINDINGS.md", text)
        self.db.update_job(job_id, status="queued", waiting_for="", phase="build", fix_round=0)
        self.db.event(job_id, "human", "ajustes pedidos", {"feedback": feedback})
        self.wake()

    async def retry(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job or job["status"] not in {"waiting", "failed"}:
            raise ValueError("só dá para tentar de novo um job parado")
        project = self.db.project(job["project_id"])
        assert project
        await self.ws.set_stop(self.ws.project_dir(project["slug"]), False)
        extra = {"fix_round": 0, "tests_round": 0} if job["waiting_for"] == "human" else {}
        self.db.update_job(job_id, status="queued", waiting_for="", message="", **extra)
        self.db.event(job_id, "human", f"retomado na fase {job['phase']}")
        self.wake()

    async def pause(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job:
            return
        project = self.db.project(job["project_id"])
        assert project
        await self.ws.set_stop(self.ws.project_dir(project["slug"]), True)
        if self.current_job_id == job_id:
            self._pause_requested.add(job_id)
            self.runner.cancel()
        elif job["status"] in {"queued", "waiting"}:
            await self._wait_human(job_id, "Pausado pelo operador.")
        self.db.event(job_id, "human", "pausa solicitada")

    async def cancel(self, job_id: int) -> None:
        await self.pause(job_id)
        await asyncio.sleep(0)
        self.db.update_job(job_id, status="cancelled", waiting_for="", finished_at=_ts())
        self.db.event(job_id, "human", "job cancelado")

    async def steer(self, job_id: int, message: str) -> None:
        job = self.db.job(job_id)
        project = self.db.project(job["project_id"]) if job else None
        if not project:
            return
        await self.ws.steer(self.ws.project_dir(project["slug"]), message)
        self.db.event(job_id, "human", "mensagem enviada ao agente", {"message": message})

    async def manual_rollback(self, project_id: int) -> str:
        project = self.db.project(project_id)
        if not project or not project["prev_prod_tag"]:
            raise ValueError("não há versão anterior para voltar")
        tag, prev = project["prev_prod_tag"], project["prod_tag"]
        await self.deployer.up(project["slug"], "production", tag)
        await self.deployer.health(project["slug"], "production")
        self.db.update_project(project_id, prod_tag=tag, prev_prod_tag=prev, status="live")
        return tag

    def accept_lesson(self, lesson_id: int, text: str | None = None) -> None:
        lesson = self.db.lesson(lesson_id)
        if not lesson or lesson["status"] != "proposed":
            raise ValueError("lição não encontrada")
        final = (text or lesson["text"]).strip()
        with self.c.lessons_file.open("a", encoding="utf-8") as f:
            f.write(f"- {final}\n")
        self.db.set_lesson_status(lesson_id, "accepted", final)

    def _require(self, job_id: int, *waiting: str) -> dict[str, Any]:
        job = self.db.job(job_id)
        if not job or job["status"] != "waiting" or job["waiting_for"] not in waiting:
            raise ValueError("o job não está esperando esta ação")
        return job

    # =====================================================================================
    # Contexto por papel: skills, MCPs e plugins do catálogo
    # =====================================================================================
    def _ctx(self, job: dict) -> tuple[dict, Path]:
        project = self.db.project(job["project_id"])
        assert project
        return project, self.ws.project_dir(project["slug"])

    def _tags(self, project: dict) -> set[str]:
        return self.lib.project_tags(project.get("tags") or [])

    def _chromium(self) -> str:
        if self.c.chromium_path:
            return self.c.chromium_path
        found = sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome"))
        return found[-1] if found else ""

    async def _run_role(
        self,
        job: dict,
        role: str,
        template: str,
        *,
        agent: str | None = None,
        schema: dict | None = None,
        read_only: bool = False,
        max_turns: int | None = None,
        effort: str | None = None,
        extra_env: dict[str, str] | None = None,
        **values: Any,
    ) -> RunResult:
        project, pdir = self._ctx(job)
        tags = self._tags(project)
        spec = RunSpec(
            role=role,
            project_dir=pdir,
            agent=agent,
            task=render(template, skills=self.lib.brief(role, tags), **values),
            json_schema=schema,
            mcp_config=self.lib.mcp_config(role, self._chromium(), tags),
            plugins=self.lib.plugins_for(role),
            max_turns=max_turns or self.c.max_turns_review,
            effort=effort,
            log_path=self._log(job),
            disallowed_tools=(WRITE_TOOLS if read_only else []) + self.lib.mcp_disallowed(role, pdir, tags),
            extra_env=extra_env or {},
        )
        pre, baseline = await integrity.snapshot(self.ws, pdir)
        result = await self._session(job, spec)
        await self._enforce_ownership(job, pdir, role, pre, baseline)
        return result

    async def _enforce_ownership(
        self, job: dict, pdir: Path, role: str, pre: str, baseline: dict[str, bytes | None]
    ) -> None:
        """Sensor computacional: desfaz o que a sessão escreveu fora da área do papel, por qualquer caminho."""
        violations = await integrity.enforce(self.ws, pdir, role, pre, baseline)
        if not violations:
            return
        await self.ws.give_to_agent(pdir)
        listed = ", ".join(str(v) for v in violations[:15])
        self.db.event(
            job["id"],
            "gate",
            f"integridade: {role} alterou {len(violations)} arquivo(s) fora da sua área — restaurados: {listed}",
            {"violations": [v.path for v in violations]},
        )
        if role == "builder":
            self._integrity_notes.setdefault(job["id"], []).append(
                "## Alterações desfeitas pelo orquestrador\n\nVocê alterou arquivos que não são seus (pelo shell "
                f"ou por outro caminho): {listed}. Eles foram restaurados. Faça o código passar nos testes do "
                "test-engineer e nos gates como estão; discordância vai para `.harness/TEST_DISPUTES.md`."
            )

    async def _sync(self, project: dict, pdir: Path, commit: bool = True) -> bool:
        tags = self._tags(project)
        return await self.ws.sync_harness(pdir, commit=commit, install_skills=lambda d: self.lib.install(d, tags))

    # =====================================================================================
    # Fases
    # =====================================================================================
    async def phase_init(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        if not pdir.exists():
            if project["kind"] == "adopted":
                await self.ws.adopt(project["slug"], project["repo_url"])
            else:
                await self.ws.create_from_template(project["slug"], project["name"])
            self.db.event(job["id"], "info", f"pasta do projeto criada em {pdir}")
        if project["kind"] == "adopted" and not (pdir / stack_manifest.MANIFEST).exists():
            detected = stack_manifest.detect(pdir)
            await self.ws.write(
                pdir, stack_manifest.MANIFEST, json.dumps(detected, ensure_ascii=False, indent=2) + "\n"
            )
            self.db.event(job["id"], "info", "manifesto da arquitetura detectado (o builder confere no onboarding)")
        if await self._sync(project, pdir):
            self.db.event(job["id"], "info", "harness sincronizado (regras, hooks, agentes, skills e gates)")
        await self.ws.set_stop(pdir, False)
        for rel in (".harness/NEXT_FINDINGS.md", ".harness/HARNESS_FEEDBACK.md", ".harness/TEST_FINDINGS.md"):
            self.ws.remove(pdir, rel)
        self.db.update_job(job["id"], base_commit=await self.ws.head(pdir))
        return FIRST_PHASE_AFTER_INIT[job["type"]]

    @staticmethod
    def _discovery_history(job: dict) -> list[dict]:
        state = job["questions"] if isinstance(job["questions"], dict) else {}
        history = list(state.get("history") or [])
        if not history and state.get("items"):  # jobs de antes das rodadas
            history = [{"round": 1, "items": state["items"], "answers": job["answers"] or {}}]
        return history

    @staticmethod
    def _qa_text(history: list[dict]) -> str:
        blocks = []
        for rnd in history:
            answers = rnd.get("answers") or {}
            lines = [f"### Rodada {rnd.get('round', '?')}"]
            for q in rnd.get("items") or []:
                given = (answers.get(q["id"]) or "").strip()
                if given:
                    answer = given
                elif q.get("default"):
                    answer = f"{q['default']} (recomendação aceita em silêncio)"
                else:
                    answer = "(sem resposta: decida você e registre como premissa)"
                lines.append(f"**{q['question']}**\n→ {answer}")
            blocks.append("\n\n".join(lines))
        return "\n\n".join(blocks) or "(sem perguntas)"

    async def phase_discovery(self, job: dict) -> str | None:
        project, _ = self._ctx(job)
        state = dict(job["questions"]) if isinstance(job["questions"], dict) else {}
        history = self._discovery_history(job)
        round_ = len(history) + 1
        max_rounds = max(1, self.c.discovery_rounds)
        context = "Projeto novo: ainda não há código, só o andaime do harness. A arquitetura será decidida no plano."
        if job["type"] != "create":
            context = (
                f"Projeto existente '{project['name']}' em produção em "
                f"{self.c.url(project['slug'], 'production')}. "
                "Leia SPEC.md, docs/PLAN.md, .harness/stack.json e o código."
            )
        last_rule = (
            "Esta é a ÚLTIMA rodada: pergunte só o que for impossível decidir bem sozinho; o resto vira premissa "
            "explícita na spec."
            if round_ >= max_rounds
            else "Se as respostas desta rodada abrirem decisões novas, haverá outra rodada — não antecipe."
        )
        result = await self._run_role(
            job,
            "planner",
            "discovery",
            agent="planner",
            schema=schemas.QUESTIONS,
            read_only=True,
            job_type=job["type"],
            request=job["request"],
            context=context,
            round=round_,
            max_rounds=max_rounds,
            history=(self._qa_text(history) if history else "(primeira rodada)")
            + (
                f"\n\n### Orientação do Gabriel\n\n{job['answers']['_spec_feedback']}"
                if (job["answers"] or {}).get("_spec_feedback")
                else ""
            ),
            last_round_rule=last_rule,
        )
        if not result.ok or not isinstance(result.structured, dict):
            return await self._session_failed(job, result, "descoberta")
        out = result.structured
        items = [q for q in (out.get("questions") or []) if q.get("id") and q.get("question")]
        seen: set[str] = {q["id"] for rnd in history for q in rnd.get("items") or []}
        for q in items:  # ids únicos entre rodadas (as respostas se acumulam num dicionário só)
            qid = str(q["id"]) if round_ == 1 else f"r{round_}_{q['id']}"
            while qid in seen:
                qid += "_"
            seen.add(qid)
            q["id"] = qid
        state.update(
            understanding=out.get("understanding", "") or state.get("understanding", ""),
            project_name=out.get("project_name") or state.get("project_name", ""),
            one_liner=out.get("one_liner") or state.get("one_liner", ""),
            items=items,
            round=round_,
            history=history,
        )
        self.db.update_job(job["id"], questions=state)
        if job["type"] == "create" and out.get("one_liner"):
            self.db.update_project(project["id"], description=out["one_liner"])
        if not items:
            done = f"{len(history)} rodada(s) de perguntas" if history else "sem perguntas: o pedido está claro"
            self.db.event(job["id"], "info", f"descoberta concluída ({done})")
            return "spec"
        label = f"Rodada {round_}: " if round_ > 1 else ""
        await self._wait(job["id"], "answers", f"{label}{len(items)} pergunta(s) para você responder.")
        await self.notifier.send(
            f"Perguntas: {project['name']}",
            f"O agente tem {len(items)} pergunta(s) (rodada {round_}) antes de começar.",
            f"/jobs/{job['id']}",
        )
        return None

    async def phase_spec(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        answers = dict(job["answers"] or {})
        feedback = answers.get("_spec_feedback") or "(nenhum)"
        result = await self._run_role(
            job,
            "planner",
            "spec",
            agent="planner",
            schema=schemas.SPEC,
            job_type=job["type"],
            request=job["request"],
            qa=self._qa_text(self._discovery_history(job)),
            feedback=feedback,
        )
        if not result.ok or not isinstance(result.structured, dict):
            return await self._session_failed(job, result, "spec")
        problems = self._check_spec_files(pdir)
        if problems:
            self.db.event(job["id"], "gate", "spec inválida: " + "; ".join(problems))
            return await self._wait_human(job["id"], "A spec gerada não passou na validação: " + "; ".join(problems))
        await self.ws.commit_all(pdir, "docs: especificação do projeto")
        summary = result.structured
        self.db.update_job(job["id"], spec_summary=summary)
        tags = sorted(set(summary.get("stack_tags") or []) | set(project.get("tags") or []))
        self.db.update_project(project["id"], tags=tags)
        # as tags (capacidades do produto) decidem skills/MCPs: o plano já sai com as skills certas
        await self._sync(self.db.project(project["id"]) or project, pdir)
        return "plan"

    async def phase_plan(self, job: dict) -> str | None:
        """Plano técnico: arquitetura livre com ADRs, manifesto da stack e tickets em fatias verticais."""
        project, pdir = self._ctx(job)
        answers = dict(job["answers"] or {})
        known = stack_manifest.starters(self.c.harness_dir)
        starters_text = (
            "\n".join(
                f"- `{name}` — {json.loads((path / 'stack.json').read_text(encoding='utf-8')).get('summary', '')}"
                for name, path in known.items()
            )
            or "(nenhum)"
        )
        problems: list[str] = []
        # a validação computacional reprova plano raso (tickets vagos, manifesto incompleto); o planner
        # recebe os problemas e corrige antes de o Gabriel ser chamado (PLAN_FIX_ATTEMPTS vezes)
        for attempt in range(1 + max(0, self.c.plan_fix_attempts)):
            if problems:
                self.db.event(
                    job["id"],
                    "gate",
                    f"plano reprovado na validação ({len(problems)} problema(s)); o planner está corrigindo "
                    f"(tentativa {attempt + 1})",
                )
            result = await self._run_role(
                job,
                "planner",
                "plan",
                agent="planner",
                schema=schemas.PLAN,
                job_type=job["type"],
                request=job["request"],
                feedback=answers.get("_spec_feedback") or "(nenhum)",
                starters=starters_text,
                validation=self._validation_text(problems),
            )
            if not result.ok or not isinstance(result.structured, dict):
                return await self._session_failed(job, result, "plano técnico")
            problems = self._check_plan_files(pdir, set(known), job["type"])
            if not problems:
                break
        if problems:
            self.db.event(job["id"], "gate", "plano inválido: " + "; ".join(problems))
            return await self._wait_human(
                job["id"], "O plano técnico não passou na validação: " + "; ".join(problems[:8])
            )
        # a parte do manifesto que liga sensores fica travada a partir daqui (integrity.config_problems)
        self.db.kv_set(f"stack-lock:{project['id']}", json.dumps(stack_manifest.gate_keys(stack_manifest.load(pdir))))
        # tickets novos pertencem a este job: outro job (incidente, mudança futura) nunca os herda
        tickets = self._tickets(pdir)
        for t in tickets:
            t.setdefault("job", job["id"])
        self._save_tickets(pdir, tickets)
        await self.ws.give_to_agent(pdir)
        await self.ws.commit_all(pdir, "docs: plano técnico (arquitetura, ADRs, manifesto e tickets)")
        out = result.structured
        manifest = stack_manifest.load(pdir)
        tags = sorted(set(project.get("tags") or []) | set(out.get("stack_tags") or []))
        stack_text = (out.get("stack") or manifest.get("summary") or project["stack"] or "")[:120]
        self.db.update_project(project["id"], stack=stack_text, tags=tags)
        summary = dict(self.db.job(job["id"])["spec_summary"] or {})  # type: ignore[index]
        summary["plan"] = {
            k: out.get(k) for k in ("summary", "architecture", "stack", "starter", "adrs", "tickets", "risks")
        }
        self.db.update_job(job["id"], spec_summary=summary)
        mine = [t for t in self._tickets(pdir) if t.get("job") == job["id"]]
        self.db.event(
            job["id"],
            "info",
            f"plano: {stack_text} · starter {manifest.get('starter') or 'nenhum'} · {len(mine)} ticket(s) novo(s)",
        )
        await self._sync(self.db.project(project["id"]) or project, pdir)
        if job["type"] == "create":
            await self._wait(job["id"], "spec_approval", "Spec e plano técnico prontos para sua revisão.")
            await self.notifier.send(
                f"Spec e plano prontos: {project['name']}",
                (summary.get("summary", "") + "\n\nArquitetura: " + stack_text)[:400],
                f"/jobs/{job['id']}",
            )
            return None
        return "design" if self._needs_design(self.db.job(job["id"]) or job) else "build"

    @staticmethod
    def _tickets(pdir: Path) -> list[dict]:
        try:
            data = json.loads(safefs.read_text(pdir, ".harness/tickets.json", default="{}"))
        except ValueError:
            return []
        items = data.get("tickets") if isinstance(data, dict) else None
        return [t for t in items or [] if isinstance(t, dict) and t.get("id")]

    @staticmethod
    def _save_tickets(pdir: Path, tickets: list[dict]) -> None:
        text = json.dumps({"tickets": tickets}, ensure_ascii=False, indent=2) + "\n"
        safefs.write_text(pdir, ".harness/tickets.json", text)

    @staticmethod
    def _ticket_file_ok(pdir: Path, rel: str) -> bool:
        """O arquivo do ticket precisa estar em .harness/tickets/, sem link simbólico no caminho."""
        return bool(rel) and rel.startswith(".harness/tickets/") and safefs.is_safe_file(pdir, rel)

    def _next_ticket(self, pdir: Path, job: dict) -> dict | None:
        """Próximo ticket DESTE job (só create/change planejam tickets)."""
        if job["type"] not in {"create", "change"}:
            return None
        tickets = self._tickets(pdir)
        closed = {t["id"] for t in tickets if t.get("status") in {"done", "blocked"}}
        pending = [t for t in tickets if t.get("status", "todo") == "todo" and t.get("job") == job["id"]]
        for t in pending:
            if all(dep in closed for dep in t.get("blocked_by") or []):
                return t
        return pending[0] if pending else None  # dependência circular ou inválida: segue a ordem

    def _check_plan_files(self, pdir: Path, known_starters: set[str], job_type: str) -> list[str]:
        problems = []
        if len(safefs.read_text(pdir, "docs/PLAN.md", limit=600)) < 300:
            problems.append("docs/PLAN.md ausente ou raso")
        problems += [f"stack.json: {p}" for p in stack_manifest.validate(stack_manifest.load(pdir), known_starters)]
        tickets = self._tickets(pdir)
        if not tickets:
            problems.append(".harness/tickets.json sem tickets")
        ids = [t["id"] for t in tickets]
        if len(ids) != len(set(ids)):
            problems.append("ids de ticket repetidos")
        for t in tickets:
            if t.get("status", "todo") not in {"todo", "done", "blocked"}:
                problems.append(f"ticket {t['id']}: status inválido")
            f = str(t.get("file") or "")
            if not self._ticket_file_ok(pdir, f):
                problems.append(f"ticket {t['id']}: arquivo '{f}' não existe em .harness/tickets/")
            for dep in t.get("blocked_by") or []:
                if dep not in ids:
                    problems.append(f"ticket {t['id']}: bloqueado por '{dep}', que não existe")
        new = [t for t in tickets if t.get("status", "todo") == "todo" and not t.get("job")]
        if not new:
            problems.append("nenhum ticket novo pendente (status 'todo') para construir")
        # só os tickets deste plano: os de jobs anteriores já foram construídos (ou seguem o formato antigo)
        written = []
        for t in new:
            rel = str(t.get("file") or "")
            if self._ticket_file_ok(pdir, rel):
                text = safefs.read_text(pdir, rel, limit=TICKET_MAX_CHARS)
                problems += ticket_rules.check(t, text)
                written.append((t, text))
        try:
            feats = json.loads(safefs.read_text(pdir, ".harness/features.json"))["features"]
            covered = {fid for t in tickets for fid in t.get("features") or []}
            missing = [f["id"] for f in feats if not f.get("passes") and f["id"] not in covered]
            if missing:
                problems.append(f"features sem ticket: {', '.join(missing)}")
            problems += ticket_rules.coverage(written, feats)
        except (OSError, ValueError, KeyError, TypeError):
            problems.append("features.json ilegível")
        return problems

    @staticmethod
    def _validation_text(problems: list[str]) -> str:
        if not problems:
            return "(nenhum: primeira versão)"
        items = "\n".join(f"- {p}" for p in problems[:40])
        more = f"\n- … e mais {len(problems) - 40}" if len(problems) > 40 else ""
        return (
            "A versão anterior do plano foi **reprovada** pela validação automática do orquestrador. Os arquivos "
            "continuam no projeto: corrija-os (não recomece do zero) até resolver cada item abaixo. Formato dos "
            "tickets: skill `tickets-verticais`.\n\n" + items + more
        )

    def _needs_design(self, job: dict) -> bool:
        project = self.db.project(job["project_id"]) or {}
        summary = job.get("spec_summary") or {}
        return "ui" in (project.get("tags") or []) and bool(summary.get("needs_design", job["type"] == "create"))

    @staticmethod
    def _check_spec_files(pdir: Path) -> list[str]:
        problems = []
        if len(safefs.read_text(pdir, "SPEC.md", limit=400)) < 200:
            problems.append("SPEC.md ausente ou vazio")
        try:
            data = json.loads(safefs.read_text(pdir, ".harness/features.json"))
            feats = data["features"]
            if not feats:
                problems.append("features.json sem features")
            for f in feats:
                if not {"id", "title", "acceptance", "passes"} <= f.keys() or not f["acceptance"]:
                    problems.append(f"feature {f.get('id')} incompleta")
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"features.json inválido ({e})")
        return problems

    async def phase_design(self, job: dict) -> str | None:
        """Designer: identidade, tokens e mockups (e Figma, se habilitado) antes do código de interface."""
        project, pdir = self._ctx(job)
        answers = dict(job["answers"] or {})
        feedback = answers.get("_design_feedback") or "(primeira versão)"
        rnd = int(answers.get("_design_round", 0)) + 1
        answers["_design_round"] = rnd
        answers.pop("_design_feedback", None)
        self.db.update_job(job["id"], answers=answers)
        if self.c.figma_enabled:
            figma = (
                "**Habilitado.** Leve tokens, componentes e telas-chave para o Figma (skill `figma-no-pipeline`).\n"
                f"- figma_file_key existente: {project.get('figma_file_key') or '(nenhum — crie o arquivo)'}\n"
                f"- FIGMA_PLAN_KEY: {self.c.figma_plan_key or '(use whoami)'}"
            )
        else:
            figma = "Desabilitado. Entregue só os artefatos locais em design/."
        result = await self._run_role(
            job,
            "designer",
            "design",
            agent="designer",
            schema=schemas.DESIGN,
            max_turns=self.c.max_turns_build,
            name=project["name"],
            round=rnd,
            request=job["request"],
            tags=", ".join(sorted(self._tags(project))),
            figma=figma,
            feedback=feedback,
        )
        if result.cancelled or result.rate_limited or not isinstance(result.structured, dict):
            return await self._session_failed(job, result, "design")
        missing = [p for p in ("design/DESIGN.md", "design/tokens.css") if not (pdir / p).is_file()]
        if not list((pdir / "design" / "mockups").glob("*.html")):
            missing.append("design/mockups/*.html")
        if missing:
            self.db.event(job["id"], "gate", f"design incompleto: {', '.join(missing)}")
            return await self._wait_human(job["id"], f"O design não entregou: {', '.join(missing)}.")
        await self.ws.commit_all(pdir, "design: identidade, tokens e mockups")
        report = result.structured
        self.db.update_job(job["id"], design_report=report)
        if report.get("figma_url") or report.get("figma_file_key"):
            self.db.update_project(
                project["id"], figma_url=report.get("figma_url", ""), figma_file_key=report.get("figma_file_key", "")
            )
        if report.get("figma_error"):
            self.db.event(job["id"], "info", f"Figma: {report['figma_error'][:300]}")
        self.db.event(
            job["id"],
            "design",
            f"design pronto: {len(report.get('screens', []))} tela(s) — {report.get('concept', '')[:160]}",
        )
        if not self.c.design_approval:
            return "build"
        await self._wait(job["id"], "design_approval", "Design pronto para sua revisão.")
        await self.notifier.send(
            f"Design pronto: {project['name']}", report.get("concept", "")[:300], f"/jobs/{job['id']}"
        )
        return None

    async def _apply_starter(self, job: dict, pdir: Path) -> None:
        """Copia o starter escolhido no plano (uma vez, só arquivos que faltam) antes do primeiro ticket."""
        copied = stack_manifest.apply_starter(pdir, self.c.harness_dir)
        if not copied:
            return
        await self.ws.give_to_agent(pdir)
        name = stack_manifest.load(pdir).get("starter")
        await self.ws.commit_all(pdir, f"chore: starter {name} (ponto de partida escolhido no plano)")
        self.db.event(job["id"], "info", f"starter {name} aplicado: {len(copied)} arquivo(s)")

    async def phase_build(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        await self._apply_starter(job, pdir)
        has_findings = (pdir / ".harness" / "NEXT_FINDINGS.md").exists()
        # correção de achados é uma sessão só; construção nova anda um ticket por sessão (contexto limpo)
        ticket = None if has_findings else self._next_ticket(pdir, job)
        if ticket:
            title, body = self._ticket_brief(job, project, pdir, ticket)
        else:
            title, body = self._build_brief(job, project, has_findings)
        result = await self._run_role(
            job,
            "builder",
            "build",
            agent="builder",
            max_turns=self.c.max_turns_build,
            extra_env={"MIN_COVERAGE": str(self._min_cov(project))},
            title=title,
            body=body,
            name=project["name"],
            slug=project["slug"],
            round=job["fix_round"],
            prod_url=self.c.url(project["slug"], "production"),
            references=self._references(job, project, pdir),
        )
        if result.cancelled or result.rate_limited or (not result.ok and result.turns == 0):
            return await self._session_failed(job, result, "build")
        if not result.ok:
            # sessão longa que estourou turnos/tempo ainda pode ter deixado trabalho bom; os gates decidem.
            self.db.event(job["id"], "info", f"sessão do builder terminou com aviso: {result.error[:300]}")
        if ticket:
            return await self._close_ticket(job, pdir, ticket, result.ok)
        self.ws.remove(pdir, ".harness/NEXT_FINDINGS.md")
        await self.ws.commit_all(pdir, "chore: checkpoint do orquestrador após build")
        # correção no meio dos tickets (ex.: você pediu ajuste depois de um ticket falhar): volta aos tickets
        return "build" if self._next_ticket(pdir, job) else "gates"

    async def _close_ticket(self, job: dict, pdir: Path, ticket: dict, ok: bool) -> str:
        tickets = self._tickets(pdir)
        for t in tickets:
            if t["id"] != ticket["id"]:
                continue
            t["attempts"] = int(t.get("attempts") or 0) + 1
            if ok:
                t["status"] = "done"
                self.db.event(job["id"], "info", f"ticket {t['id']} concluído: {t.get('title', '')}")
            elif t["attempts"] >= self.c.ticket_attempts:
                t["status"] = "blocked"
                self.db.event(
                    job["id"],
                    "gate",
                    f"ticket {t['id']} não fechou em {t['attempts']} sessões; seguindo (os gates dirão o que falta)",
                )
        self._save_tickets(pdir, tickets)
        await self.ws.give_to_agent(pdir)
        await self.ws.commit_all(pdir, f"chore: checkpoint do orquestrador após {ticket['id']}")
        return "build" if self._next_ticket(pdir, job) else "gates"

    def _ticket_brief(self, job: dict, project: dict, pdir: Path, ticket: dict) -> tuple[str, str]:
        tickets = self._tickets(pdir)
        done = [t["id"] for t in tickets if t.get("status") == "done"]
        rel = str(ticket.get("file") or "")
        content = safefs.read_text(pdir, rel, limit=TICKET_MAX_CHARS) if self._ticket_file_ok(pdir, rel) else ""
        if not content:
            content = f"(arquivo do ticket indisponível; use o título: {ticket.get('title')})"
        manifest = stack_manifest.load(pdir)
        foundation = ""
        if not done:
            starter = manifest.get("starter") or "nenhum"
            foundation = (
                "\n\n**Primeiro ticket: fundação.** "
                + (
                    f"O starter `{starter}` já foi copiado (só arquivos que faltavam): "
                    "ajuste-o ao plano, não o contrário."
                    if starter != "nenhum"
                    else "Não há starter: monte a estrutura exatamente como o plano e o manifesto descrevem."
                )
                + " Garanta `pyproject.toml` com o extra `dev` do harness, Dockerfile do contrato, `/health`, "
                "`.env.example` e o comando `start` do manifesto funcionando; gates verdes antes de encerrar."
            )
        body = (
            f"Pedido original do Gabriel:\n\n> {job['request'][:1500]}\n\n"
            f"Implemente **somente o ticket {ticket['id']}** — os demais vêm em sessões novas, com contexto limpo. "
            "O desenho está em `docs/PLAN.md`, `docs/adr/`, `CONTEXT.md` e `.harness/stack.json`; o contrato de aceite "
            "em `.harness/features.json`. Siga as skills `tickets-verticais` e `tdd` (teste nas costuras do ticket, "
            "vermelho antes de verde, uma fatia por vez)."
            f"{foundation}\n\n## Ticket (`{ticket.get('file', '')}`)\n\n{content}\n\n"
            "## Definição de pronto (vale para todo ticket)\n\n"
            "- Cada item de **Critérios de aceite** tem um teste seu que falhou antes do código e passa agora.\n"
            "- **Fora do escopo** foi respeitado; o que você descobriu que falta vira nota em "
            "`.harness/PROGRESS.md` (e em `.harness/HARNESS_FEEDBACK.md` se o ticket estava errado).\n"
            "- `./scripts/check.sh` verde, commits pequenos `feat(" + str(ticket["id"]) + "): …`.\n"
            "- `.harness/PROGRESS.md` com uma linha por critério → teste que o comprova, decisões e pendências.\n\n"
            f"Tickets já concluídos: {', '.join(done) or 'nenhum'}. "
            "Não mexa no status dos tickets: o orquestrador cuida."
        )
        return f"implementar o ticket {ticket['id']} — {ticket.get('title', '')}", body

    def _references(self, job: dict, project: dict, pdir: Path) -> str:
        lines = [
            "- Testes de aceitação, e2e, propriedades e contrato são do **test-engineer** e você não pode "
            "editá-los; os seus ficam em `tests/unit/`. Discordância → `.harness/TEST_DISPUTES.md`.",
        ]
        if (pdir / "docs" / "PLAN.md").exists():
            lines.append(
                "- Plano técnico em `docs/PLAN.md`, decisões em `docs/adr/`, glossário em `CONTEXT.md` e manifesto da "
                "arquitetura em `.harness/stack.json` (gates e testes leem dele; mantenha-o coerente com o código)."
            )
        if (pdir / "design" / "DESIGN.md").exists():
            lines.append(
                "- Design aprovado em `design/` (DESIGN.md, tokens.css, mockups). Use os tokens como "
                "fonte única de cores, tipografia e espaçamento."
            )
        if project.get("figma_file_key"):
            lines.append(
                f"- Figma do projeto: {project.get('figma_url') or ''} (file_key `{project['figma_file_key']}`) "
                "— leia com get_design_context/get_screenshot (skill `figma-design-to-code`)."
            )
        resolved = (job.get("test_report") or {}).get("disputes") or []
        if resolved:
            lines.append(
                "- Disputas julgadas pelo test-engineer: "
                + "; ".join(
                    f"{d['test']} → {'mantido' if d['verdict'] == 'kept' else 'corrigido'} ({d['reason'][:160]})"
                    for d in resolved
                )
            )
        return "\n".join(lines)

    def _build_brief(self, job: dict, project: dict, has_findings: bool) -> tuple[str, str]:
        if has_findings:
            return (
                "corrigir os problemas encontrados",
                (
                    "Os sensores (gates, test-engineer, staging, avaliador, revisores ou o Gabriel) "
                    "reprovaram a versão anterior. Os problemas estão em `.harness/NEXT_FINDINGS.md`.\n\n"
                    "1. Leia todos os achados antes de mudar código.\n"
                    "2. Para cada bug, primeiro reproduza com um teste (seu, em tests/unit) ou com o teste do "
                    "test-engineer que já falha; depois corrija a causa.\n"
                    "3. Não enfraqueça testes nem critérios. Testes do test-engineer não são seus.\n"
                    "4. Se discordar de um achado, explique em PROGRESS.md com evidência — não ignore.\n"
                    "5. Registre em `.harness/HARNESS_FEEDBACK.md` se algum achado indicar uma regra ou "
                    "verificação que teria evitado o problema."
                ),
            )
        t = job["type"]
        if t == "create":
            return (
                "implementar o projeto",
                (
                    f"Pedido original do Gabriel:\n\n> {job['request']}\n\n"
                    "A spec aprovada está em `SPEC.md`, o plano em `docs/PLAN.md` e o contrato de aceite em "
                    "`.harness/features.json`. Implemente as features pendentes, uma por vez, em ordem de "
                    "prioridade, seguindo o plano, o CLAUDE.md e as skills. Regra de negócio na pasta declarada "
                    "em `backend.domain` do `.harness/stack.json` (é onde o mutation testing mede os testes). "
                    "Escreva o README como vitrine do projeto."
                ),
            )
        if t == "change":
            return (
                "implementar a mudança pedida",
                (
                    f"Pedido do Gabriel:\n\n> {job['request']}\n\n"
                    "A mudança foi especificada no fim de `SPEC.md` e as features novas estão em "
                    "`.harness/features.json` com `passes: false`. Siga a skill `manutencao`: diff "
                    "mínimo, compatibilidade de dados e de API, toda a suíte verde."
                ),
            )
        if t == "incident":
            return (
                "resolver um incidente em produção",
                (
                    f"Produção ({self.c.url(project['slug'], 'production')}) apresentou problema.\n\n"
                    f"{job['request']}\n\n"
                    "Siga as skills `systematic-debugging` e `manutencao`: reproduza com um teste, encontre a "
                    "causa raiz, corrija com diff mínimo e registre sintoma → causa → correção em PROGRESS.md. "
                    "Se a causa estiver fora do código (ex.: API externa fora do ar), torne o app resiliente a ela."
                ),
            )
        return (
            "adotar um projeto existente",
            (
                f"Este projeto já existia fora do agente (repositório {project['repo_url']}). "
                f"Instruções do Gabriel:\n\n> {job['request'] or '(nenhuma)'}\n\n"
                "Siga a seção 'Projeto adotado' da skill `manutencao`:\n"
                "1. Gere `.harness/PROJECT.md` (mapa do projeto).\n"
                "2. Adapte ao contrato de deploy do CLAUDE.md **sem mudar comportamento**: Dockerfile "
                "com HEALTHCHECK, porta 8000, `/health`, dados em `/data`, `.env.example`, e um "
                "`pyproject.toml` com o extra `dev` do harness (pytest, pytest-cov, "
                "pytest-playwright, hypothesis, schemathesis, axe-playwright-python, mutmut, pyright, respx, "
                "ruff, pip-audit) para os gates funcionarem. Confira e corrija o manifesto detectado em "
                "`.harness/stack.json` (skill `arquitetura-livre`).\n"
                "3. Crie testes de unidade que protejam os fluxos principais (os de aceitação virão do "
                "test-engineer).\n"
                "4. Em `.harness/features.json`, registre os fluxos principais existentes como features "
                "(critérios = comportamento atual) e marque `passes: true` só com evidência.\n"
                "5. Documente em `docs/adr/` o que mudou na infraestrutura."
            ),
        )

    def _min_cov(self, project: dict) -> int:
        return project["min_coverage"] if project["min_coverage"] is not None else self.c.min_coverage

    def _min_mutation(self, project: dict) -> int:
        value = project.get("min_mutation")
        return value if value is not None else self.c.min_mutation_score

    async def phase_gates(self, job: dict) -> str | None:
        """Sensores computacionais. Decide quem age em seguida: builder, test-engineer ou staging."""
        project, pdir = self._ctx(job)
        builder_problems: list[str] = self._integrity_notes.pop(job["id"], [])
        tampered = integrity.restore_gates(self.c.harness_dir / "project-template", pdir)
        if tampered:
            await self.ws.give_to_agent(pdir)
            self.db.event(job["id"], "gate", f"integridade: gates alterados foram restaurados: {', '.join(tampered)}")
            builder_problems.append(
                "## Gates alterados\n\nOs scripts de gate "
                + ", ".join(f"`{t}`" for t in tampered)
                + " estavam diferentes dos do harness e foram restaurados. Não altere gates."
            )
        lock = self.db.kv_get(f"stack-lock:{project['id']}")
        config = integrity.config_problems(pdir, json.loads(lock) if lock else None)
        if config:
            builder_problems.append(
                "## Configuração de testes que desativaria testes\n\n" + "\n".join(f"- {c}" for c in config)
            )
        disputes = self._open_disputes(pdir)
        code, out = await self.ws.sh(
            "./scripts/check.sh",
            cwd=pdir,
            agent=True,
            check=False,
            timeout=2400,
            env={"MIN_COVERAGE": str(self._min_cov(project))},
        )
        if code != 0:
            if disputes and job["tests_round"] < self.c.max_test_rounds and not builder_problems:
                self.db.event(job["id"], "gate", "gates falharam com disputa de teste aberta: test-engineer julga")
                return "tests"
            builder_problems.append("## ./scripts/check.sh falhou\n\n```\n" + out[-7000:] + "\n```")
        else:
            skipped = integrity.unexecuted_tester_tests(pdir)
            if skipped:
                builder_problems.append(
                    "## Testes do test-engineer que não rodaram\n\nEstes testes existem mas não aparecem como "
                    "executados no junit (foram desmarcados, ignorados ou pulados por fora):\n\n"
                    + "\n".join(f"- `{t}`" for t in skipped[:30])
                    + "\n\nRemova o que impede a coleta/execução deles. Os testes do test-engineer sempre rodam."
                )
        for rel in ("Dockerfile", ".env.example", "README.md"):
            if not (pdir / rel).is_file():
                builder_problems.append(f"## Contrato de deploy\n\nArquivo obrigatório ausente: `{rel}`.")
        pending = self._pending_features(pdir)
        if pending:
            builder_problems.append(
                "## Features ainda com `passes: false`\n\n"
                + "\n".join(f"- {f['id']} — {f['title']}" for f in pending)
                + "\n\nImplemente-as (com evidência) ou, se estiverem bloqueadas, explique o bloqueio em PROGRESS.md."
            )
        leaks = await self._scan_secrets(pdir)
        if leaks:
            builder_problems.append(
                "## Possíveis segredos versionados\n\n"
                + "\n".join(f"- {x}" for x in leaks)
                + "\n\nRemova do código, use variável de ambiente e documente em .env.example."
            )
        if builder_problems:
            self.db.event(
                job["id"],
                "gate",
                f"gates falharam ({len(builder_problems)} problema(s))",
                {"problems": [p[:500] for p in builder_problems]},
            )
            return await self._needs_fix(job, "gates computacionais", "\n\n".join(builder_problems))

        # --- sensores sobre a suíte do test-engineer: rastreio, testes fracos, disputas
        quality = await self._test_quality(pdir)
        if quality is not None:
            reasons: list[str] = []
            if job["tests_round"] == 0:
                reasons.append("primeira rodada de testes independentes deste trabalho")
            if quality["untraced"]:
                reasons.append("features sem teste de aceitação: " + ", ".join(quality["untraced"]))
            if quality["problems"]:
                reasons.append(f"{len(quality['problems'])} teste(s) fraco(s) na suíte do test-engineer")
            if disputes:
                reasons.append("disputa aberta pelo builder")
            if reasons:
                return await self._route_to_tests(job, pdir, reasons, quality)

            # --- mutation testing: a suíte pega bugs de verdade?
            mutation = await self._mutation(project, pdir)
            if mutation and not mutation.get("skipped") and not mutation.get("blocked"):
                self.db.update_job(job["id"], mutation_score=mutation.get("score"))
                if mutation.get("score", 0) < self._min_mutation(project):
                    return await self._route_to_tests(
                        job,
                        pdir,
                        [f"mutation score {mutation.get('score')}% abaixo de {self._min_mutation(project)}%"],
                        quality,
                        mutation,
                    )
        score = (self.db.job(job["id"]) or job).get("mutation_score")
        self.db.event(
            job["id"],
            "gate",
            "gates passaram (lint, tipos, testes, e2e, fuzz, cobertura, contrato"
            + (f", mutation {score}%" if score is not None else "")
            + ")",
        )
        return "staging"

    async def _route_to_tests(
        self, job: dict, pdir: Path, reasons: list[str], quality: dict, mutation: dict | None = None
    ) -> str | None:
        if job["tests_round"] >= self.c.max_test_rounds:
            msg = (
                f"Limite de {self.c.max_test_rounds} rodadas de teste atingido. Pendências: "
                + "; ".join(reasons)
                + ". Oriente o agente e clique em 'Tentar de novo'."
            )
            self.db.update_job(job["id"], phase="tests")
            return await self._wait_human(job["id"], msg)
        lines = ["# Pendências nos testes do test-engineer", "", *[f"- {r}" for r in reasons], ""]
        for p in quality.get("problems", [])[:40]:
            lines.append(f"- `{p['path']}:{p['line']}` [{p['rule']}] {p['message']}")
        if mutation and mutation.get("survivors"):
            lines += ["", "## Mutantes sobreviventes (veja com `.venv/bin/mutmut show <nome>`)", ""]
            lines += [f"- {s}" for s in mutation["survivors"][:40]]
        await self.ws.write(pdir, ".harness/TEST_FINDINGS.md", "\n".join(lines) + "\n")
        self.db.event(job["id"], "gate", "testes do test-engineer necessários: " + "; ".join(reasons))
        return "tests"

    def _open_disputes(self, pdir: Path) -> str:
        return safefs.read_text(pdir, ".harness/TEST_DISPUTES.md", limit=20000).strip()

    async def _test_quality(self, pdir: Path) -> dict | None:
        if not (pdir / "scripts" / "test_quality.py").is_file():
            return None
        py = ".venv/bin/python" if (pdir / ".venv" / "bin" / "python").exists() else "python3"
        await self.ws.sh(
            py,
            "scripts/test_quality.py",
            "--owner",
            "tester",
            "--trace",
            "--json",
            ".harness/evidence/test-quality.json",
            cwd=pdir,
            agent=True,
            check=False,
            timeout=300,
        )
        try:
            return json.loads(safefs.read_text(pdir, ".harness/evidence/test-quality.json"))
        except ValueError:
            return {"problems": [], "untraced": []}

    async def _mutation(self, project: dict, pdir: Path) -> dict | None:
        if not (pdir / "scripts" / "mutation.sh").is_file():
            return None
        await self.ws.sh(
            "./scripts/mutation.sh",
            cwd=pdir,
            agent=True,
            check=False,
            timeout=self.c.mutation_timeout_s + 120,
            env={
                "MIN_MUTATION_SCORE": str(self._min_mutation(project)),
                "MUTATION_TIMEOUT_S": str(self.c.mutation_timeout_s),
            },
        )
        try:
            return json.loads(safefs.read_text(pdir, ".harness/evidence/mutation.json"))
        except ValueError:
            return None

    async def phase_tests(self, job: dict) -> str | None:
        """Test-engineer independente: aceitação, e2e, propriedades, a11y, mutation e disputas."""
        project, pdir = self._ctx(job)
        rnd = job["tests_round"] + 1
        self.db.update_job(job["id"], tests_round=rnd)
        quality = await self._test_quality(pdir) or {"problems": [], "untraced": []}
        test_findings = safefs.read_text(pdir, ".harness/TEST_FINDINGS.md", limit=20000, default="(nenhum)")
        disputes = self._open_disputes(pdir)
        mutation = safefs.read_text(pdir, ".harness/evidence/mutation.json", limit=1500, default="(ainda não rodou)")
        focus = {
            "create": "todas as features da spec",
            "change": "as features novas e a regressão das antigas afetadas pela mudança",
            "incident": f"um teste de regressão que reproduz o incidente e a área afetada: {job['request'][:400]}",
            "adopt": "os fluxos principais existentes (testes de caracterização do comportamento atual)",
        }[job["type"]]
        result = await self._run_role(
            job,
            "tester",
            "tests",
            agent="test-engineer",
            schema=schemas.TESTS,
            max_turns=self.c.max_turns_build,
            extra_env={"MIN_COVERAGE": str(self._min_cov(project))},
            name=project["name"],
            round=rnd,
            job_type=job["type"],
            focus=focus,
            untraced=", ".join(quality["untraced"]) or "nenhuma",
            changes=await self.ws.changes_since(pdir, job["base_commit"]) or "(projeto novo)",
            test_findings=test_findings,
            disputes=disputes or "(nenhuma)",
            min_mutation=self._min_mutation(project),
            mutation=mutation,
        )
        if result.cancelled or result.rate_limited or (not result.ok and result.turns == 0):
            return await self._session_failed(job, result, "testes")
        report = (
            result.structured
            if isinstance(result.structured, dict)
            else {"summary": f"sessão terminou sem relatório: {result.error[:200]}", "bugs": []}
        )
        self.db.update_job(job["id"], test_report=report)
        self.ws.remove(pdir, ".harness/TEST_FINDINGS.md")
        if disputes:
            await self._archive_disputes(pdir, disputes, report.get("disputes") or [])
        await self.ws.commit_all(pdir, f"test: testes de aceitação (rodada {rnd})")
        bugs = report.get("bugs") or []
        score = (report.get("mutation") or {}).get("score")
        self.db.event(
            job["id"],
            "tests",
            f"test-engineer: {report.get('tests_written', '?')} teste(s), {len(bugs)} bug(s)"
            + (f", mutation {score}%" if score is not None else ""),
            {"bugs": bugs[:20], "gaps": report.get("gaps", [])[:10]},
        )
        if bugs:
            md = (
                "## Bugs encontrados pelo test-engineer\n\nOs testes abaixo falham por causa do código. Corrija o "
                "código sem alterar os testes; se tiver certeza de que um teste contradiz a SPEC, registre em "
                "`.harness/TEST_DISPUTES.md`.\n\n"
                + "\n\n".join(
                    f"### [{b.get('severity', '?').upper()}] {b.get('feature', '')} — `{b['test']}`\n"
                    f"- Observado: {b['observed']}\n- Esperado: {b['expected']}"
                    for b in bugs
                )
            )
            return await self._needs_fix(job, "test-engineer", md)
        return "gates"

    async def _archive_disputes(self, pdir: Path, disputes: str, verdicts: list[dict]) -> None:
        previous = safefs.read_text(pdir, ".harness/TEST_DISPUTES_RESOLVED.md", default="# Disputas julgadas\n")
        verdict_md = (
            "\n".join(f"- `{d['test']}` → **{d['verdict']}**: {d['reason']}" for d in verdicts) or "- (sem veredito)"
        )
        await self.ws.write(
            pdir,
            ".harness/TEST_DISPUTES_RESOLVED.md",
            f"{previous}\n## {_ts()}\n\n### Disputa\n\n{disputes}\n\n### Veredito\n\n{verdict_md}\n",
        )
        self.ws.remove(pdir, ".harness/TEST_DISPUTES.md")

    @staticmethod
    def _pending_features(pdir: Path) -> list[dict]:
        try:
            data = json.loads(safefs.read_text(pdir, ".harness/features.json"))
            return [f for f in data.get("features", []) if not f.get("passes")]
        except (ValueError, AttributeError):
            return [{"id": "?", "title": ".harness/features.json ilegível"}]

    async def _scan_secrets(self, pdir: Path) -> list[str]:
        _, files = await self.ws.sh("git", "-c", "safe.directory=*", "ls-files", cwd=pdir, check=False)
        hits = []
        for rel in files.splitlines():
            if rel.startswith(".claude/") or not safefs.is_safe_file(pdir, rel):
                continue
            if (pdir / rel).stat().st_size > 1_000_000:
                continue
            text = safefs.read_text(pdir, rel)
            for i, line in enumerate(text.splitlines(), 1):
                if SECRET_PATTERNS.search(line):
                    hits.append(f"{rel}:{i}")
        return hits[:20]

    async def phase_staging(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        slug = project["slug"]
        tag = f"{await self.ws.short_head(pdir)}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        try:
            await self.deployer.build(slug, pdir, tag)
        except DeployError as e:
            self.db.event(job["id"], "deploy", f"build da imagem falhou: {e}")
            if e.infra:
                return await self._wait_human(job["id"], f"Falha de infraestrutura no build: {e}")
            return await self._needs_fix(
                job, "build da imagem Docker", f"## `docker build` falhou\n\n```\n{e.logs[-6000:]}\n```"
            )
        try:
            await self.deployer.up(slug, "staging", tag)
            await self.deployer.health(slug, "staging")
        except DeployError as e:
            logs = e.logs or await self.deployer.logs(slug, "staging")
            self.db.event(job["id"], "deploy", f"staging não subiu: {e}")
            if e.infra:
                return await self._wait_human(job["id"], f"Falha de infraestrutura no staging: {e}")
            return await self._needs_fix(
                job,
                "deploy em staging",
                (
                    f"## O container não ficou saudável em staging\n\n{e}\n\n"
                    f"Últimas linhas do log do container:\n\n```\n{logs[-6000:]}\n```\n\n"
                    "Reproduza localmente com `uvicorn` usando `APP_ENV=staging` e `DATA_DIR` vazio."
                ),
            )
        self.db.update_project(project["id"], staging_tag=tag)
        self.db.event(job["id"], "deploy", f"staging no ar: {self.c.url(slug, 'staging')} ({tag})")
        return "evaluate"

    async def phase_evaluate(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        slug = project["slug"]
        url = self.c.url(slug, "staging")
        if self.c.staging_auth_password:
            url = url.replace("https://", f"https://{self.c.staging_auth_user}:{self.c.staging_auth_password}@")
        focus = {
            "create": "todas as features da spec, de ponta a ponta",
            "change": "as features novas desta mudança e regressões nas antigas",
            "incident": f"confirmar que o incidente foi resolvido e que nada regrediu: {job['request'][:500]}",
            "adopt": "os fluxos principais continuam funcionando depois da adaptação ao contrato",
        }[job["type"]]
        previous = job["eval_report"] or {}
        prev_text = (
            "\n".join(f"- [{f.get('severity')}] {f.get('what')}" for f in previous.get("findings", []))
            or "(primeira avaliação)"
        )
        design = "Sem design formal: avalie pela direção visual da SPEC."
        if (pdir / "design" / "DESIGN.md").exists():
            design = (
                "Compare com `design/DESIGN.md`, `design/tokens.css`, `design/mockups/*.html` e os "
                "screenshots em `.harness/evidence/design/`."
            )
            if project.get("figma_url"):
                design += f" Figma: {project['figma_url']}"
        result = await self._run_role(
            job,
            "evaluator",
            "evaluate",
            agent="evaluator",
            schema=schemas.EVALUATION,
            read_only=True,
            effort=self.c.eval_effort,
            staging_url=url,
            round=job["fix_round"],
            focus=focus,
            design=design,
            changes=await self.ws.changes_since(pdir, job["base_commit"]),
            previous=prev_text,
        )
        if not result.ok or not isinstance(result.structured, dict):
            return await self._session_failed(job, result, "avaliação")
        report = result.structured
        self.db.update_job(job["id"], eval_report=report)
        passed, reasons = schemas.evaluation_passes(report)
        scores = report.get("scores", {})
        lh = report.get("lighthouse") or {}
        self.db.event(
            job["id"],
            "eval",
            f"avaliador: {'PASS' if passed else 'NEEDS_WORK'} · "
            + ", ".join(f"{k}={v}" for k, v in scores.items())
            + (f" · lighthouse perf={lh.get('performance')} a11y={lh.get('accessibility')}" if lh else ""),
            {"reasons": reasons},
        )
        if passed:
            return "review"
        return await self._needs_fix(job, "avaliador (QA em staging)", self._findings_md(report, reasons))

    @staticmethod
    def _findings_md(report: dict, reasons: list[str]) -> str:
        lines = [
            "## Avaliação em staging: NEEDS_WORK",
            "",
            report.get("summary", ""),
            "",
            "Motivos: " + "; ".join(reasons),
            "",
            "Notas: " + json.dumps(report.get("scores", {})),
            "",
        ]
        if report.get("lighthouse"):
            lines += ["Lighthouse: " + json.dumps(report["lighthouse"]), ""]
        order = {"blocker": 0, "major": 1, "minor": 2}
        for f in sorted(report.get("findings", []), key=lambda f: order.get(f.get("severity"), 3)):
            lines += [
                f"### [{f.get('severity', '?').upper()}] {f.get('feature', '')} {f.get('what', '')}",
                f"- Esperado: {f.get('expected', '')}",
                f"- Onde: {f.get('where', '-')}",
                "",
            ]
        return "\n".join(lines)

    async def phase_review(self, job: dict) -> str | None:
        """Sensores inferenciais finais: segurança (security-reviewer) e manutenção (code-reviewer)."""
        project, pdir = self._ctx(job)
        base = job["base_commit"] if job["type"] != "create" else ""
        changes = await self.ws.changes_since(pdir, job["base_commit"])
        sec = await self._run_role(
            job,
            "security",
            "security",
            agent="security-reviewer",
            schema=schemas.SECURITY,
            read_only=True,
            base=base,
            changes=changes,
        )
        if not sec.ok or not isinstance(sec.structured, dict):
            return await self._session_failed(job, sec, "revisão de segurança")
        tests = job.get("test_report") or {}
        evaluation = job.get("eval_report") or {}
        code = await self._run_role(
            job,
            "reviewer",
            "review",
            agent="code-reviewer",
            schema=schemas.CODE_REVIEW,
            read_only=True,
            base=base,
            changes=changes,
            tests_summary=tests.get("summary", "(sem relatório)")[:600],
            eval_summary=evaluation.get("summary", "(sem relatório)")[:600],
        )
        if not code.ok or not isinstance(code.structured, dict):
            return await self._session_failed(job, code, "revisão de código")
        self.db.update_job(job["id"], security_report=sec.structured, review_report=code.structured)
        sec_blockers = [f for f in sec.structured.get("findings", []) if f.get("severity") == "blocker"]
        code_blockers = [f for f in code.structured.get("findings", []) if f.get("severity") == "blocker"]
        self.db.event(
            job["id"],
            "gate",
            f"revisão: segurança {len(sec_blockers)} bloqueante(s), código {len(code_blockers)} bloqueante(s)",
        )
        if sec_blockers or code_blockers:
            parts = []
            if sec_blockers:
                parts.append(
                    "## Revisão de segurança: bloqueantes\n\n"
                    + "\n\n".join(
                        f"### {f['title']}\n- Onde: {f.get('where', '-')}\n- Cenário: {f.get('scenario', '-')}\n"
                        f"- Correção sugerida: {f['fix']}"
                        for f in sec_blockers
                    )
                )
            if code_blockers:
                parts.append(
                    "## Revisão de código: bloqueantes\n\n"
                    + "\n\n".join(
                        f"### {f['title']}\n- Onde: {f.get('where', '-')}\n- Impacto: {f.get('impact', '-')}\n"
                        f"- Correção sugerida: {f['fix']}"
                        for f in code_blockers
                    )
                )
            return await self._needs_fix(job, "revisão (segurança + código)", "\n\n".join(parts))
        job = self.db.job(job["id"]) or job
        score = job.get("mutation_score")
        await self._wait(job["id"], "deploy_approval", "Pronto para produção: revise o staging e aprove.")
        await self.notifier.send(
            f"Aprovar deploy: {project['name']}",
            "Passou em gates, testes independentes"
            + (f" (mutation {score}%)" if score is not None else "")
            + f", avaliador e revisões. Staging: {self.c.url(project['slug'], 'staging')}",
            f"/jobs/{job['id']}",
            priority="high",
        )
        return None

    phase_security = phase_review  # compatibilidade com jobs criados antes da fase review

    async def phase_production(self, job: dict) -> str | None:
        project, _ = self._ctx(job)
        slug, tag, prev = project["slug"], project["staging_tag"], project["prod_tag"]
        if not tag:
            return await self._wait_human(job["id"], "Não há imagem validada em staging para promover.")
        try:
            await self.deployer.up(slug, "production", tag)
            await self.deployer.health(slug, "production")
        except DeployError as e:
            self.db.event(job["id"], "deploy", f"produção falhou: {e}")
            message = f"O deploy em produção falhou ({e})."
            if prev:
                try:
                    await self.deployer.up(slug, "production", prev)
                    await self.deployer.health(slug, "production")
                    self.db.event(job["id"], "deploy", f"rollback automático para {prev} concluído")
                    message += f" Rollback automático para {prev} feito; produção segue na versão anterior."
                except DeployError as e2:
                    self.db.event(job["id"], "deploy", f"rollback para {prev} também falhou: {e2}")
                    self.db.update_project(project["id"], status="down")
                    message += f" O rollback também falhou ({e2}) — produção pode estar fora do ar!"
            else:
                await self.deployer.stop(slug, "production")
                message += " Era o primeiro deploy; o container foi parado."
            await self.notifier.send(
                f"Falha no deploy: {project['name']}", message, f"/jobs/{job['id']}", priority="urgent"
            )
            return await self._wait_human(job["id"], message)
        self.db.update_project(project["id"], prod_tag=tag, prev_prod_tag=prev or "", status="live")
        self.db.event(job["id"], "deploy", f"produção no ar: {self.c.url(slug, 'production')} ({tag})")
        try:
            await self.deployer.cleanup(slug, {tag, prev, project["staging_tag"]} - {""})
        except DeployError as e:
            self.db.event(job["id"], "info", f"limpeza de imagens falhou: {e}")
        return "publish"

    async def phase_publish(self, job: dict) -> str | None:
        project, pdir = self._ctx(job)
        try:
            repo = await self.publisher.publish(project, pdir, project["prod_tag"])
            self.db.update_project(project["id"], repo_url=repo)
            self.db.event(job["id"], "info", f"código publicado em {repo}")
        except (PublishError, ShellError) as e:
            self.db.event(job["id"], "error", f"publicação no GitHub falhou: {e}")
        await self.notifier.send(
            f"No ar: {project['name']}", f"{self.c.url(project['slug'], 'production')}", f"/jobs/{job['id']}"
        )
        return "retro"

    async def phase_retro(self, job: dict) -> str | None:
        """Steering loop: transforma o que deu errado neste job em propostas de melhoria."""
        project, pdir = self._ctx(job)
        report = self._run_report(job)
        feedback = safefs.read_text(pdir, ".harness/HARNESS_FEEDBACK.md", limit=6000, default="(nenhuma)")
        existing = self.c.lessons_file.read_text(encoding="utf-8")[-6000:]
        result = await self._run_role(
            job,
            "retro",
            "retro",
            schema=schemas.RETRO,
            read_only=True,
            max_turns=30,
            effort="medium",
            report=report,
            feedback=feedback,
            lessons=existing,
        )
        if result.ok and isinstance(result.structured, dict):
            lessons = result.structured.get("lessons", [])
            for lesson in lessons:
                self.db.add_lesson(job["id"], lesson["text"], lesson.get("kind", "rule"), lesson.get("evidence", ""))
            for s in result.structured.get("simplifications", []):
                self.db.event(job["id"], "info", f"sugestão de simplificação: {s}")
            if lessons:
                await self.notifier.send(
                    "Novas lições propostas",
                    f"{len(lessons)} lição(ões) do job #{job['id']} para revisar.",
                    "/lessons",
                    priority="low",
                )
        else:
            self.db.event(job["id"], "info", f"retrospectiva não concluída: {result.error[:200]}")
        self.db.update_job(job["id"], status="done", finished_at=_ts(), waiting_for="")
        self.db.update_project(project["id"], status="live")
        self.db.event(job["id"], "info", "job concluído")
        return None

    def _run_report(self, job: dict) -> str:
        job = self.db.job(job["id"]) or job
        lines = [
            f"Job #{job['id']} · tipo {job['type']} · rodadas de correção: {job['fix_round']} · "
            f"rodadas de teste: {job['tests_round']} · mutation: {job.get('mutation_score')}",
            f"Pedido: {job['request'][:800]}",
            "",
            "Linha do tempo:",
        ]
        for ev in self.db.events(job["id"]):
            lines.append(f"- {ev['ts']} [{ev['kind']}] {ev['message']}")
            data = ev.get("data") or {}
            for p in data.get("problems", [])[:3]:
                lines.append("    " + p.replace("\n", " ")[:300])
            for b in data.get("bugs", [])[:5]:
                lines.append(f"    bug: {b.get('observed', '')[:200]}")
        lines.append("\nSessões:")
        for s in self.db.sessions(job["id"]):
            lines.append(
                f"- {s['role']}: ok={bool(s['ok'])} turnos={s['turns']} {s['duration_s']:.0f}s {s['error'][:120]}"
            )
        ev = job.get("eval_report") or {}
        if ev.get("findings"):
            lines.append("\nÚltimos achados do avaliador:")
            lines += [f"- [{f.get('severity')}] {f.get('what')}" for f in ev["findings"][:10]]
        review = job.get("review_report") or {}
        if review.get("findings"):
            lines.append("\nAchados da revisão de código:")
            lines += [f"- [{f.get('severity')}] {f.get('title')}" for f in review["findings"][:10]]
        return "\n".join(lines)[-14000:]

    # =====================================================================================
    # Auxiliares
    # =====================================================================================
    def _log(self, job: dict) -> Path:
        return self.c.logs_dir / f"job-{job['id']}.log"

    async def _session(self, job: dict, spec: RunSpec) -> RunResult:
        self.db.event(job["id"], "info", f"sessão {spec.role}{' (' + spec.agent + ')' if spec.agent else ''} iniciada")
        result = await self.runner.run(spec)
        self.db.add_session(
            job["id"],
            spec.role,
            session_id=result.session_id,
            ok=int(result.ok),
            cost_usd=result.cost_usd,
            turns=result.turns,
            duration_s=result.duration_s,
            log_path=result.log_path,
            error=result.error[:1000],
        )
        self.db.add_cost(job["id"], result.cost_usd)
        return result

    async def _session_failed(self, job: dict, result: RunResult, what: str) -> str | None:
        if result.rate_limited:
            when = result.retry_at or (datetime.now(UTC) + timedelta(seconds=self.c.rate_limit_backoff_s))
            until = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.db.update_job(
                job["id"],
                status="queued",
                not_before=until,
                message=f"Limite de uso atingido; retomo automaticamente às {until} (UTC).",
            )
            self.db.event(job["id"], "info", f"limite de uso na fase {what}; nova tentativa após {until}")
            return None
        if result.cancelled:
            self._pause_requested.discard(job["id"])
            return await self._wait_human(job["id"], "Pausado pelo operador. Clique em 'Retomar' para continuar.")
        self.db.event(job["id"], "error", f"sessão de {what} falhou: {result.error[:500]}")
        return await self._wait_human(job["id"], f"A sessão de {what} falhou: {result.error[:500]}")

    async def _needs_fix(self, job: dict, source: str, findings: str) -> str | None:
        _, pdir = self._ctx(job)
        job = self.db.job(job["id"]) or job
        rnd = job["fix_round"] + 1
        header = f"# Problemas encontrados — fonte: {source} (rodada {rnd})\n\n"
        await self.ws.write(pdir, ".harness/NEXT_FINDINGS.md", header + findings[:40000])
        self.db.update_job(job["id"], fix_round=rnd)
        if rnd > self.c.max_fix_rounds:
            project = self.db.project(job["project_id"])
            msg = (
                f"Limite de {self.c.max_fix_rounds} rodadas de correção atingido (última falha: {source}). "
                "Leia os achados, mande uma orientação pelo painel e clique em 'Tentar de novo'."
            )
            await self.notifier.send(
                f"Preciso de ajuda: {project['name'] if project else ''}", msg, f"/jobs/{job['id']}", priority="high"
            )
            self.db.update_job(job["id"], phase="build")
            return await self._wait_human(job["id"], msg)
        return "build"

    async def _wait(self, job_id: int, waiting_for: str, message: str) -> None:
        current = self.db.job(job_id)
        if current and current["status"] in {"cancelled", "done"}:
            return
        self.db.update_job(job_id, status="waiting", waiting_for=waiting_for, message=message)
        self.db.event(job_id, "human", f"aguardando você: {message}")

    async def _wait_human(self, job_id: int, message: str) -> None:
        await self._wait(job_id, "human", message)
        return None


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
