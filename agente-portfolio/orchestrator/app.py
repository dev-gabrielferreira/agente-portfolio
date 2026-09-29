"""Painel web do agente (FastAPI + Jinja2). Um único usuário: você."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from orchestrator import safefs
from orchestrator.api import build_router
from orchestrator.config import Config
from orchestrator.db import DB
from orchestrator.deployer import Deployer, DockerDeployer, FakeDeployer
from orchestrator.infra import CaddyChanges, CaddyHost, ChangeError, DockerCaddyHost, Prober
from orchestrator.knowledge import KnowledgeBase
from orchestrator.monitor import Monitor
from orchestrator.notify import Notifier
from orchestrator.pipeline import Pipeline
from orchestrator.publisher import GitHubPublisher, NullPublisher, Publisher
from orchestrator.runner import ClaudeRunner, Runner
from orchestrator.skills import ROLES, Library
from orchestrator.totp import TotpGuard
from orchestrator.workspace import Workspace

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]{0,63})\s*=(.*)$")
PUBLIC = ("/login", "/static", "/public", "/healthz")
API_PREFIX = "/api/"  # autenticada por token próprio (orchestrator/api.py), não pela sessão do painel

STATUS_LABEL = {
    "queued": "na fila",
    "running": "trabalhando",
    "waiting": "esperando você",
    "done": "concluído",
    "failed": "falhou",
    "cancelled": "cancelado",
    "draft": "rascunho",
    "building": "em construção",
    "live": "no ar",
    "down": "fora do ar",
    "archived": "arquivado",
}
PHASE_LABEL = {
    "init": "preparando",
    "discovery": "perguntas",
    "spec": "especificação",
    "plan": "plano técnico",
    "build": "construção",
    "design": "design",
    "gates": "gates (lint, tipos, testes)",
    "tests": "testes independentes",
    "staging": "deploy em staging",
    "evaluate": "avaliação (QA)",
    "review": "revisão (segurança + código)",
    "security": "revisão (segurança + código)",
    "production": "deploy em produção",
    "publish": "publicação",
    "retro": "retrospectiva",
}
TYPE_LABEL = {"create": "novo projeto", "change": "mudança", "incident": "incidente", "adopt": "adoção"}


def create_app(
    config: Config | None = None,
    runner: Runner | None = None,
    deployer: Deployer | None = None,
    publisher: Publisher | None = None,
    start_background: bool = True,
    library: Library | None = None,
    caddy_host: CaddyHost | None = None,
    caddy_prober: Prober | None = None,
) -> FastAPI:
    c = config or Config()
    c.ensure_dirs()
    if not c.admin_password:
        raise RuntimeError("defina ADMIN_PASSWORD no .env")
    db = DB(c.db_path)
    ws = Workspace(c)
    deployer = deployer or (FakeDeployer() if c.deploy_dry_run else DockerDeployer(c, ws))
    publisher = publisher or (GitHubPublisher(c, ws) if c.github_token else NullPublisher())
    notifier = Notifier(c.notify_url, c.panel_url)
    lib = library or Library(
        c.harness_dir, c.vendor_dir, {**os.environ, "FIGMA_ENABLED": "true" if c.figma_enabled else ""}
    )
    kb = KnowledgeBase(c, db)
    pipeline = Pipeline(c, db, runner or ClaudeRunner(c), deployer, publisher, ws, notifier, lib, kb)
    monitor = Monitor(c, db, deployer, pipeline, notifier)
    caddy = CaddyChanges(c, db, caddy_host or DockerCaddyHost(c), caddy_prober)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks = []
        try:
            kb.refresh_all()  # base de conhecimento do Jarvis em dia desde o boot
        except Exception:
            log.exception("não consegui gerar a base de conhecimento")
        if start_background:
            tasks = [asyncio.create_task(pipeline.worker()), asyncio.create_task(monitor.loop())]
            try:  # domínios do seu Caddyfile: projeto novo não pega um endereço que já é seu
                await caddy.refresh_hosts()
            except Exception:
                log.warning("não consegui ler o Caddyfile principal agora (os slugs reservados ficam como estavam)")
        yield
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    app = FastAPI(title="Agente de Portfólio", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config, app.state.db, app.state.pipeline, app.state.monitor = c, db, pipeline, monitor
    app.state.knowledge = kb
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(STATUS=STATUS_LABEL, PHASE=PHASE_LABEL, TYPE=TYPE_LABEL, cfg=c)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not path.startswith(PUBLIC) and not path.startswith(API_PREFIX) and not request.session.get("auth"):
            if request.method == "GET":
                return RedirectResponse("/login", status_code=303)
            return Response("não autenticado", status_code=401)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=c.session_secret or secrets.token_hex(32),
        same_site="strict",
        https_only=c.panel_https_only,
        max_age=60 * 60 * 24 * 14,
    )
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        flash = request.session.pop("flash", None)
        return templates.TemplateResponse(request, name, {"flash": flash, **ctx})

    def back(request: Request, url: str, message: str = "") -> RedirectResponse:
        if message:
            request.session["flash"] = message
        return RedirectResponse(url, status_code=303)

    def get_job(job_id: int) -> dict:
        job = db.job(job_id)
        if not job:
            raise HTTPException(404, "job não encontrado")
        return job

    def get_project(project_id: int) -> dict:
        project = db.project(project_id)
        if not project:
            raise HTTPException(404, "projeto não encontrado")
        return project

    # ---------------------------------------------------------------- autenticação
    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "worker_job": pipeline.current_job_id}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html")

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if secrets.compare_digest(password.encode(), c.admin_password.encode()):
            request.session["auth"] = True
            return back(request, "/")
        await asyncio.sleep(2)  # freia tentativa e erro
        return back(request, "/login", "Senha incorreta.")

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return back(request, "/login")

    # ---------------------------------------------------------------- painel
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        projects = db.projects()
        jobs = db.jobs(limit=30)
        by_id = {p["id"]: p for p in projects}
        waiting = [j for j in jobs if j["status"] == "waiting"]
        return page(
            request,
            "index.html",
            projects=projects,
            jobs=jobs,
            by_id=by_id,
            waiting=waiting,
            current=pipeline.current_job_id,
            metrics=db.metrics(),
            health=monitor.last,
            proposed=len(db.lessons("proposed")),
        )

    @app.get("/new", response_class=HTMLResponse)
    def new_form(request: Request):
        return page(request, "new.html")

    @app.post("/new")
    def new_project(request: Request, request_text: str = Form(..., alias="request"), name: str = Form("")):
        if len(request_text.strip()) < 20:
            return back(request, "/new", "Descreva o projeto com um pouco mais de detalhe.")
        job = pipeline.new_project(request_text, name)
        return back(request, f"/jobs/{job['id']}", "Pedido recebido. O agente vai preparar as perguntas.")

    @app.post("/adopt")
    def adopt(
        request: Request,
        name: str = Form(...),
        repo_url: str = Form(...),
        request_text: str = Form("", alias="request"),
    ):
        try:
            job = pipeline.adopt_project(name, repo_url, request_text)
        except ValueError as e:
            return back(request, "/new", str(e))
        return back(request, f"/jobs/{job['id']}", "Projeto em adoção.")

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_page(request: Request, project_id: int):
        project = get_project(project_id)
        pdir = pipeline.ws.project_dir(project["slug"])
        env_example = safefs.read_text(pdir, ".env.example", limit=20000)
        declared = sorted({m.group(1) for line in env_example.splitlines() if (m := ENV_LINE.match(line))})
        keys = {
            env: sorted(_read_env(c.secrets_dir / f"{project['slug']}.{env}.env")) for env in ("staging", "production")
        }
        return page(
            request,
            "project.html",
            project=project,
            jobs=db.jobs(project_id),
            declared=declared,
            keys=keys,
            health=monitor.last.get(project["slug"]),
        )

    @app.post("/projects/{project_id}/jobs")
    def project_job(
        request: Request,
        project_id: int,
        type_: str = Form(..., alias="type"),
        request_text: str = Form(..., alias="request"),
    ):
        get_project(project_id)
        try:
            job = pipeline.new_job(project_id, type_, request_text)
        except ValueError as e:
            return back(request, f"/projects/{project_id}", str(e))
        return back(request, f"/jobs/{job['id']}")

    @app.post("/projects/{project_id}/secrets/{env}")
    def save_secrets(request: Request, project_id: int, env: str, content: str = Form("")):
        project = get_project(project_id)
        if env not in ("staging", "production"):
            raise HTTPException(400)
        path = c.secrets_dir / f"{project['slug']}.{env}.env"
        values = _read_env(path)
        for line in content.splitlines():
            m = ENV_LINE.match(line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if value:
                values[key] = value
            else:
                values.pop(key, None)
        path.write_text("".join(f"{k}={v}\n" for k, v in sorted(values.items())), encoding="utf-8")
        path.chmod(0o600)
        return back(
            request, f"/projects/{project_id}", f"Variáveis de {env} salvas. Valem no próximo deploy desse ambiente."
        )

    @app.post("/projects/{project_id}/rollback")
    async def rollback(request: Request, project_id: int):
        try:
            tag = await pipeline.manual_rollback(project_id)
        except Exception as e:
            return back(request, f"/projects/{project_id}", f"Rollback falhou: {e}")
        return back(request, f"/projects/{project_id}", f"Produção voltou para {tag}.")

    @app.post("/projects/{project_id}/visibility")
    def visibility(request: Request, project_id: int):
        project = get_project(project_id)
        db.update_project(project_id, public=0 if project["public"] else 1)
        return back(request, f"/projects/{project_id}")

    # ---------------------------------------------------------------- jobs
    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_page(request: Request, job_id: int):
        job = get_job(job_id)
        project = get_project(job["project_id"])
        pdir = pipeline.ws.project_dir(project["slug"])
        spec = plan = ""
        if (
            job["waiting_for"] in {"spec_approval", "design_approval", "deploy_approval"}
            and (pdir / "SPEC.md").exists()
        ):
            spec = safefs.read_text(pdir, "SPEC.md", limit=60000)
        if job["waiting_for"] == "spec_approval":
            plan = safefs.read_text(pdir, "docs/PLAN.md", limit=40000)
        findings = safefs.read_text(pdir, ".harness/NEXT_FINDINGS.md", limit=40000)
        changes = ""
        if job["waiting_for"] == "deploy_approval" and pdir.exists():
            changes = await pipeline.ws.changes_since(pdir, job["base_commit"])
        return page(
            request,
            "job.html",
            job=job,
            project=project,
            events=db.events(job_id),
            sessions=db.sessions(job_id),
            spec=spec,
            plan=plan,
            findings=findings,
            changes=changes,
            running=pipeline.current_job_id == job_id,
            design_shots=_design_shots(pdir),
            design_doc=safefs.read_text(pdir, "design/DESIGN.md", limit=20000),
        )

    @app.get("/jobs/{job_id}/file")
    def job_file(job_id: int, path: str):
        """Serve evidências e mockups do projeto (imagens e HTML estático) com CSP de sandbox:
        conteúdo gerado pelo agente nunca executa script na origem do painel."""
        job = get_job(job_id)
        project = get_project(job["project_id"])
        pdir = pipeline.ws.project_dir(project["slug"]).resolve()
        target = (pdir / path).resolve()
        allowed = (pdir / "design", pdir / ".harness" / "evidence")
        if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
            raise HTTPException(404)
        media = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".html": "text/html; charset=utf-8",
            ".css": "text/css",
            ".md": "text/plain; charset=utf-8",
            ".json": "application/json",
            ".svg": "image/svg+xml",
        }.get(target.suffix.lower())
        if not media:
            raise HTTPException(404)
        return Response(
            target.read_bytes(),
            media_type=media,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self' data:",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/jobs/{job_id}/log")
    def job_log(job_id: int, offset: int = 0):
        get_job(job_id)
        path = c.logs_dir / f"job-{job_id}.log"
        if not path.exists():
            return JSONResponse({"text": "", "offset": 0})
        with path.open("rb") as f:
            size = f.seek(0, 2)
            if offset > size or offset < 0:
                offset = 0
            if offset == 0 and size > 200_000:
                offset = size - 200_000
            f.seek(offset)
            data = f.read(500_000)
        return JSONResponse({"text": data.decode("utf-8", "replace"), "offset": offset + len(data)})

    @app.get("/jobs/{job_id}/status")
    def job_status(job_id: int):
        job = get_job(job_id)
        return {
            "status": job["status"],
            "phase": job["phase"],
            "waiting_for": job["waiting_for"],
            "fix_round": job["fix_round"],
            "events": len(db.events(job_id)),
        }

    @app.post("/jobs/{job_id}/answers")
    async def answers(request: Request, job_id: int):
        form = await request.form()
        data = {k.removeprefix("q_"): str(v).strip() for k, v in form.items() if k.startswith("q_")}
        try:
            await pipeline.submit_answers(job_id, data, str(form.get("name", "")), str(form.get("slug", "")).strip())
        except ValueError as e:
            return back(request, f"/jobs/{job_id}", str(e))
        return back(request, f"/jobs/{job_id}", "Respostas enviadas. O planner está escrevendo a spec.")

    def action(fn, ok: str):
        async def handler(request: Request, job_id: int):
            form = await request.form()
            text = str(form.get("text", "")).strip()
            try:
                takes_text = len(inspect.signature(fn).parameters) > 1
                result = fn(job_id, text) if takes_text else fn(job_id)
                if asyncio.iscoroutine(result):
                    await result
            except ValueError as e:
                return back(request, f"/jobs/{job_id}", str(e))
            return back(request, f"/jobs/{job_id}", ok)

        return handler

    routes = {
        "approve-spec": (pipeline.approve_spec, "Spec aprovada. Construção iniciada."),
        "revise-spec": (pipeline.revise_spec, "Ajustes enviados ao planner."),
        "approve-design": (pipeline.approve_design, "Design aprovado. Construção iniciada."),
        "revise-design": (pipeline.revise_design, "Ajustes enviados ao designer."),
        "approve-deploy": (pipeline.approve_deploy, "Deploy em produção aprovado."),
        "request-changes": (pipeline.request_changes, "Ajustes enviados ao agente."),
        "retry": (pipeline.retry, "Job retomado."),
        "pause": (pipeline.pause, "Pausa solicitada."),
        "cancel": (pipeline.cancel, "Job cancelado."),
        "steer": (pipeline.steer, "Mensagem entregue na próxima ação do agente."),
    }
    for name, (fn, ok) in routes.items():
        app.add_api_route(f"/jobs/{{job_id}}/{name}", action(fn, ok), methods=["POST"])

    app.include_router(build_router(c, db, pipeline, monitor, kb, caddy))
    app.state.caddy = caddy
    totp_guard = TotpGuard(c.admin_totp_secret, db)

    # ---------------------------------------------------------------- infra (Caddyfile principal)
    @app.get("/infra", response_class=HTMLResponse)
    async def infra_page(request: Request):
        writable, error = None, ""
        try:
            writable = await caddy.host.writable()
        except ChangeError as e:
            error = str(e)
        pending = db.infra_changes(status="proposta")
        for ch in pending:
            ch["confirmacao"] = caddy.confirmation(ch)
        history = [x for x in db.infra_changes(limit=30) if x["status"] != "proposta"]
        for ch in history:
            if ch["status"] == "aplicada":
                ch["confirmacao"] = caddy.confirmation(ch)
        return page(
            request,
            "infra.html",
            pending=pending,
            history=history,
            writable=writable,
            error=error,
            enabled=c.caddy_proposals,
            totp=bool(c.admin_totp_secret),
        )

    def _caddy_code(code: str) -> str:
        verdict = totp_guard.verify(code.strip(), "caddy")
        return "" if verdict.ok else f"Código recusado: {verdict.reason}."

    def _status_is(change_id: int, status: str) -> str:
        row = db.infra_change(change_id)
        if not row:
            return f"Proposta #{change_id} não existe."
        return "" if row["status"] == status else f"A proposta #{change_id} está '{row['status']}'."

    @app.post("/infra/{change_id}/aprovar")
    async def infra_approve(request: Request, change_id: int, codigo: str = Form("")):
        problem = _status_is(change_id, "proposta") or _caddy_code(codigo)
        if problem:
            return back(request, "/infra", problem)
        try:
            row = await caddy.approve(change_id)
        except ChangeError as e:
            return back(request, "/infra", str(e))
        return back(request, "/infra", f"Proposta #{change_id}: {row['status']}. {row.get('resultado', '')}")

    @app.post("/infra/{change_id}/rejeitar")
    def infra_reject(request: Request, change_id: int):
        try:
            caddy.reject(change_id, "rejeitada no painel")
        except ChangeError as e:
            return back(request, "/infra", str(e))
        return back(request, "/infra", f"Proposta #{change_id} rejeitada.")

    @app.post("/infra/{change_id}/desfazer")
    async def infra_undo(request: Request, change_id: int, codigo: str = Form("")):
        problem = _status_is(change_id, "aplicada") or _caddy_code(codigo)
        if problem:
            return back(request, "/infra", problem)
        try:
            await caddy.undo(change_id)
        except ChangeError as e:
            return back(request, "/infra", str(e))
        return back(request, "/infra", f"Mudança #{change_id} desfeita: o Caddyfile voltou à versão anterior.")

    # ---------------------------------------------------------------- harness (catálogo)
    @app.get("/harness", response_class=HTMLResponse)
    def harness_page(request: Request):
        rows = lib.status()
        mcps = [
            {"name": m.name, "scope": m.scope, "roles": m.roles, "enabled": lib.mcp_enabled(m), "deny": m.deny}
            for m in lib.mcps
        ]
        sources = [{"name": k, **v} for k, v in lib.sources.items() if k != "local"]
        return page(request, "harness.html", rows=rows, mcps=mcps, sources=sources, roles=ROLES, figma=c.figma_enabled)

    # ---------------------------------------------------------------- lições (steering loop)
    @app.get("/lessons", response_class=HTMLResponse)
    def lessons(request: Request):
        return page(
            request,
            "lessons.html",
            proposed=db.lessons("proposed"),
            decided=[x for x in db.lessons() if x["status"] != "proposed"][:50],
            current=c.lessons_file.read_text(encoding="utf-8"),
            metrics=db.metrics(),
        )

    @app.post("/lessons/{lesson_id}/accept")
    def accept_lesson(request: Request, lesson_id: int, text: str = Form("")):
        try:
            pipeline.accept_lesson(lesson_id, text or None)
        except ValueError as e:
            return back(request, "/lessons", str(e))
        return back(request, "/lessons", "Lição incorporada ao harness. Vale a partir do próximo job.")

    @app.post("/lessons/{lesson_id}/reject")
    def reject_lesson(request: Request, lesson_id: int):
        db.set_lesson_status(lesson_id, "rejected")
        return back(request, "/lessons")

    # ---------------------------------------------------------------- público
    @app.get("/public/portfolio.json")
    def portfolio() -> list[dict]:
        """Lista dos projetos no ar, para a página de portfólio consumir."""
        return [
            {
                "name": p["name"],
                "slug": p["slug"],
                "description": p["description"],
                "stack": p["stack"],
                "url": c.url(p["slug"], "production"),
                "repo": p["repo_url"],
                "updated_at": p["updated_at"],
            }
            for p in db.projects()
            if p["status"] == "live" and p["public"] and p["prod_tag"]
        ]

    return app


def _design_shots(pdir: Path) -> list[str]:
    folder = pdir / ".harness" / "evidence" / "design"
    if not folder.is_dir():
        return []
    return [p.relative_to(pdir).as_posix() for p in sorted(folder.glob("*.png"))][:24]


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := ENV_LINE.match(line):
            out[m.group(1)] = m.group(2).strip()
    return out
