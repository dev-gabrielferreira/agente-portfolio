"""API JSON do orquestrador para o Jarvis (a sessão do Claude Code que você comanda pelo celular).

Autenticação por token próprio (JARVIS_API_TOKEN), separado da senha do painel. O token dá ao Jarvis
o papel de *operador*: criar e acompanhar jobs, responder perguntas, orientar, pausar, aprovar spec e
design quando você mandar. Produção é outra conversa: aprovar deploy e fazer rollback exigem um
código TOTP do seu app autenticador, conferido aqui — o modelo não tem como gerar esse código, então
nem um prompt injection nem um erro do Jarvis publicam nada sem você.

As respostas são enxutas de propósito: cada campo entra no contexto do modelo.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from orchestrator import safefs
from orchestrator.config import Config
from orchestrator.db import DB
from orchestrator.infra import CaddyChanges, ChangeError
from orchestrator.knowledge import KnowledgeBase
from orchestrator.totp import TotpGuard

WAITING_TEXT = {
    "answers": "responder as perguntas da descoberta",
    "spec_approval": "aprovar ou pedir ajustes na spec/plano",
    "design_approval": "aprovar ou pedir ajustes no design",
    "deploy_approval": "aprovar o deploy em produção (exige código TOTP)",
    "human": "decidir como seguir (o agente parou e explicou o motivo)",
}
STAGE_WAITING = {"spec": "spec_approval", "design": "design_approval", "deploy": "deploy_approval"}
JOB_TYPES = {
    "mudanca": "change",
    "mudança": "change",
    "change": "change",
    "incidente": "incident",
    "incident": "incident",
}


def _read(pdir: Path, rel: str, limit: int) -> str:
    """Arquivo do projeto (do agente) sem seguir links simbólicos — nada de /proc/self/environ no prompt."""
    text = safefs.read_text(pdir, rel, limit=limit + 1)
    return text if len(text) <= limit else text[:limit] + "\n\n[... cortado]"


def _one_line(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_router(
    c: Config, db: DB, pipeline: Any, monitor: Any, kb: KnowledgeBase, caddy: CaddyChanges | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    guard = TotpGuard(c.admin_totp_secret, db)
    # deploy e rollback SEMPRE exigem o código: a variável só acrescenta etapas (falha fechada)
    totp_for = {"deploy", "rollback"} | {s.strip() for s in c.jarvis_totp_for.split(",") if s.strip()}

    async def authorize(request: Request) -> None:
        # o Jarvis fala direto com o container (rede interna do Docker); pedido que passou pelo
        # proxy público (Caddy acrescenta X-Forwarded-For) não chega na API, a menos que liberado
        if not c.jarvis_api_public and request.headers.get("x-forwarded-for"):
            raise HTTPException(404, "Not Found")
        if not c.jarvis_api_token:
            raise HTTPException(503, "API do Jarvis desligada: defina JARVIS_API_TOKEN no .env")
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if not token or not secrets.compare_digest(token.encode(), c.jarvis_api_token.encode()):
            await asyncio.sleep(1)
            raise HTTPException(401, "token inválido")

    def project_or_404(slug: str) -> dict:
        project = db.project_by_slug(slug)
        if not project:
            raise HTTPException(404, f"projeto '{slug}' não existe; use GET /api/v1/projetos")
        return project

    def job_or_404(job_id: int) -> dict:
        job = db.job(job_id)
        if not job:
            raise HTTPException(404, f"job #{job_id} não existe")
        return job

    def panel(path: str) -> str:
        return c.panel_url.rstrip("/") + path

    def project_row(p: dict) -> dict:
        return {
            "slug": p["slug"],
            "nome": p["name"],
            "status": p["status"],
            "descricao": _one_line(p.get("description") or "", 200),
            "stack": p.get("stack") or "",
            "producao": c.url(p["slug"], "production") if p.get("prod_tag") else None,
            "versao_producao": p.get("prod_tag") or None,
            "saude": monitor.last.get(p["slug"]) if monitor else None,
            "repo": p.get("repo_url") or None,
        }

    def job_row(j: dict, projects: dict[int, dict] | None = None) -> dict:
        p = (projects or {}).get(j["project_id"]) or db.project(j["project_id"]) or {}
        row = {
            "id": j["id"],
            "projeto": p.get("slug"),
            "tipo": j["type"],
            "status": j["status"],
            "fase": j["phase"],
            "pedido": _one_line(j["request"], 140),
        }
        if j["status"] == "waiting":
            row["esperando"] = j["waiting_for"]
            row["o_que_fazer"] = WAITING_TEXT.get(j["waiting_for"], j["waiting_for"])
            if j.get("message"):
                row["mensagem"] = _one_line(j["message"], 300)
        return row

    def audit(job_id: int, message: str, data: dict | None = None) -> None:
        db.event(job_id, "human", f"{message} (via Jarvis)", data)

    def need_totp(action: str, body: dict) -> None:
        if action not in totp_for:
            return
        verdict = guard.verify(str(body.get("codigo") or ""), action)
        if not verdict.ok:
            raise HTTPException(
                403, f"segundo fator recusado: {verdict.reason}. Peça ao Gabriel o código do app autenticador."
            )

    async def call(fn, *args) -> None:
        try:
            result = fn(*args)
            if asyncio.iscoroutine(result):
                await result
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    # ------------------------------------------------------------------ leitura
    @router.get("/resumo")
    async def resumo(request: Request) -> dict:
        await authorize(request)
        projects = db.projects()
        by_id = {p["id"]: p for p in projects}
        jobs = db.jobs(limit=60)
        current = pipeline.current_job_id
        running = next((j for j in jobs if j["id"] == current), None)
        return {
            "projetos": [project_row(p) for p in projects],
            "esperando_voce": [job_row(j, by_id) for j in jobs if j["status"] == "waiting"],
            "trabalhando_agora": job_row(running, by_id) if running else None,
            "na_fila": sum(1 for j in jobs if j["status"] == "queued"),
            "ultimo_evento": db.last_event_id(),
            "painel": c.panel_url,
            "segundo_fator": "configurado" if guard.enabled else "NÃO configurado (deploy pela API fica bloqueado)",
        }

    @router.get("/projetos")
    async def projetos(request: Request) -> list[dict]:
        await authorize(request)
        return [project_row(p) for p in db.projects()]

    @router.get("/projetos/{slug}")
    async def projeto(request: Request, slug: str) -> dict:
        await authorize(request)
        p = project_or_404(slug)
        return {
            **project_row(p),
            "staging": c.url(slug, "staging") if p.get("staging_tag") else None,
            "versao_anterior": p.get("prev_prod_tag") or None,
            "tags": p.get("tags") or [],
            "jobs": [job_row(j, {p["id"]: p}) for j in db.jobs(p["id"], limit=8)],
            "nota": f"GET /api/v1/projetos/{slug}/nota (base de conhecimento)",
            "painel": panel(f"/projects/{p['id']}"),
        }

    @router.get("/projetos/{slug}/nota")
    async def nota(request: Request, slug: str) -> JSONResponse:
        await authorize(request)
        p = project_or_404(slug)
        text = kb.note(slug)
        if not text:
            kb.refresh_project(p["id"], index=False)
            text = kb.note(slug)
        return JSONResponse({"slug": slug, "nota": text})

    @router.get("/jobs")
    async def jobs(request: Request, status: str = "", projeto: str = "", limite: int = 15) -> list[dict]:
        await authorize(request)
        project_id = project_or_404(projeto)["id"] if projeto else None
        rows = db.jobs(project_id, limit=max(1, min(limite, 50)))
        if status:
            rows = [j for j in rows if j["status"] == status]
        return [job_row(j) for j in rows]

    @router.get("/jobs/{job_id}")
    async def job(request: Request, job_id: int) -> dict:
        await authorize(request)
        j = job_or_404(job_id)
        p = db.project(j["project_id"]) or {}
        pdir = c.projects_dir / p.get("slug", "_")
        out: dict[str, Any] = {
            **job_row(j, {p.get("id"): p}),
            "pedido": j["request"][:4000],
            "rodada_de_correcao": j["fix_round"],
            "criado_em": j["created_at"],
            "painel": panel(f"/jobs/{job_id}"),
        }
        questions = j.get("questions") or {}
        if isinstance(questions, dict) and questions.get("items"):
            out["entendimento"] = questions.get("understanding", "")
            out["perguntas"] = [
                {
                    "id": q.get("id"),
                    "pergunta": q.get("question"),
                    "por_que": q.get("why", ""),
                    "opcoes": q.get("options") or [],
                    "recomendado": q.get("default", ""),
                }
                for q in questions["items"]
            ]
            if questions.get("round"):
                out["rodada_de_perguntas"] = questions["round"]
        if j.get("spec_summary"):
            out["resumo_da_spec"] = j["spec_summary"]
        if j["waiting_for"] == "spec_approval":
            out["spec_md"] = _read(pdir, "SPEC.md", 12000)
            out["plano_md"] = _read(pdir, "docs/PLAN.md", 8000)
        if j["waiting_for"] == "design_approval":
            out["design_md"] = _read(pdir, "design/DESIGN.md", 8000)
            out["telas"] = f"veja os mockups em {panel(f'/jobs/{job_id}')}"
        findings = _read(pdir, ".harness/NEXT_FINDINGS.md", 4000)
        if findings:
            out["achados_abertos"] = findings
        if j["waiting_for"] == "deploy_approval" and pdir.exists():
            out["mudancas"] = (await pipeline.ws.changes_since(pdir, j["base_commit"]))[:4000]
            out["staging"] = c.url(p["slug"], "staging")
            for key in ("eval_report", "security_report", "review_report"):
                report = j.get(key) or {}
                if report:
                    out[key] = {k: report[k] for k in ("verdict", "summary", "score") if k in report}
        out["ultimos_eventos"] = [
            f"{e['ts']} [{e['kind']}] {_one_line(e['message'], 200)}" for e in db.events(job_id)[-15:]
        ]
        return out

    @router.get("/jobs/{job_id}/log")
    async def job_log(request: Request, job_id: int, tamanho: int = 6000) -> dict:
        await authorize(request)
        job_or_404(job_id)
        path = c.logs_dir / f"job-{job_id}.log"
        if not path.exists():
            return {"log": ""}
        size = min(max(tamanho, 500), 20000)
        with path.open("rb") as f:
            end = f.seek(0, 2)
            f.seek(max(0, end - size))
            return {"log": f.read().decode("utf-8", "replace")}

    @router.get("/eventos")
    async def eventos(request: Request, depois: int = 0, limite: int = 50) -> dict:
        await authorize(request)
        rows = db.events_since(depois, max(1, min(limite, 200)))
        return {
            "eventos": [
                {
                    "id": e["id"],
                    "ts": e["ts"],
                    "tipo": e["kind"],
                    "mensagem": _one_line(e["message"], 300),
                    "job": e["job_id"],
                    "projeto": e["project_slug"],
                    "status_job": e["job_status"],
                    "esperando": e["waiting_for"] or None,
                }
                for e in rows
            ],
            "ultimo": rows[-1]["id"] if rows else depois,
        }

    # ------------------------------------------------------------------ ações
    @router.post("/projetos")
    async def novo_projeto(request: Request, body: dict = Body(...)) -> dict:
        await authorize(request)
        pedido = str(body.get("pedido") or "").strip()
        if len(pedido) < 20:
            raise HTTPException(422, "descreva o projeto com mais detalhe (campo 'pedido')")
        j = pipeline.new_project(pedido, str(body.get("nome") or ""))
        audit(j["id"], "projeto pedido")
        return {
            "job": j["id"],
            "projeto": (db.project(j["project_id"]) or {}).get("slug"),
            "painel": panel(f"/jobs/{j['id']}"),
        }

    @router.post("/projetos/adotar")
    async def adotar_projeto(request: Request, body: dict = Body(...)) -> dict:
        """Traz para o agente um projeto que já existe no GitHub (inclusive um que já está no ar)."""
        await authorize(request)
        nome = str(body.get("nome") or "").strip()
        if len(nome) < 2:
            raise HTTPException(422, "informe o nome do projeto (campo 'nome')")
        try:
            j = pipeline.adopt_project(nome, str(body.get("repositorio") or ""), str(body.get("instrucoes") or ""))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        audit(j["id"], "adoção de projeto pedida")
        return {
            "job": j["id"],
            "projeto": (db.project(j["project_id"]) or {}).get("slug"),
            "painel": panel(f"/jobs/{j['id']}"),
        }

    @router.post("/projetos/{slug}/jobs")
    async def novo_job(request: Request, slug: str, body: dict = Body(...)) -> dict:
        await authorize(request)
        p = project_or_404(slug)
        tipo = JOB_TYPES.get(str(body.get("tipo") or "mudanca").lower())
        pedido = str(body.get("pedido") or "").strip()
        if not tipo:
            raise HTTPException(422, "tipo deve ser 'mudanca' ou 'incidente'")
        if len(pedido) < 10:
            raise HTTPException(422, "descreva a mudança (campo 'pedido')")
        try:
            j = pipeline.new_job(p["id"], tipo, pedido)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        audit(j["id"], f"job de {tipo} pedido")
        return {"job": j["id"], "painel": panel(f"/jobs/{j['id']}")}

    @router.post("/jobs/{job_id}/respostas")
    async def respostas(request: Request, job_id: int, body: dict = Body(...)) -> dict:
        await authorize(request)
        job_or_404(job_id)
        raw = body.get("respostas") or {}
        if not isinstance(raw, dict) or not raw:
            raise HTTPException(422, "envie 'respostas' como objeto {id_da_pergunta: resposta}")
        answers = {str(k): str(v).strip() for k, v in raw.items()}
        await call(
            pipeline.submit_answers, job_id, answers, str(body.get("nome") or ""), str(body.get("endereco") or "")
        )
        audit(job_id, "respostas enviadas")
        return {"ok": True}

    @router.post("/jobs/{job_id}/aprovar")
    async def aprovar(request: Request, job_id: int, body: dict = Body(...)) -> dict:
        await authorize(request)
        j = job_or_404(job_id)
        etapa = str(body.get("etapa") or "")
        if etapa not in STAGE_WAITING:
            raise HTTPException(422, "etapa deve ser 'spec', 'design' ou 'deploy'")
        if j["status"] != "waiting" or j["waiting_for"] != STAGE_WAITING[etapa]:
            raise HTTPException(
                409, f"o job não está esperando aprovação de {etapa} (esperando: {j['waiting_for'] or 'nada'})"
            )
        need_totp(etapa, body)
        fn = {"spec": pipeline.approve_spec, "design": pipeline.approve_design, "deploy": pipeline.approve_deploy}[
            etapa
        ]
        await call(fn, job_id)
        audit(job_id, f"{etapa} aprovado", {"segundo_fator": etapa in totp_for})
        return {"ok": True}

    @router.post("/jobs/{job_id}/ajustes")
    async def ajustes(request: Request, job_id: int, body: dict = Body(...)) -> dict:
        await authorize(request)
        j = job_or_404(job_id)
        texto = str(body.get("texto") or "").strip()
        if len(texto) < 5:
            raise HTTPException(422, "descreva os ajustes (campo 'texto')")
        waiting = j["waiting_for"]
        if waiting == "spec_approval":
            await call(pipeline.revise_spec, job_id, texto)
        elif waiting == "design_approval":
            await call(pipeline.revise_design, job_id, texto)
        elif waiting in {"deploy_approval", "human"}:
            await call(pipeline.request_changes, job_id, texto)
        else:
            raise HTTPException(409, "o job não está esperando revisão; use /orientar para mandar recado ao agente")
        audit(job_id, "ajustes pedidos", {"texto": texto[:2000]})
        return {"ok": True}

    @router.post("/jobs/{job_id}/orientar")
    async def orientar(request: Request, job_id: int, body: dict = Body(...)) -> dict:
        await authorize(request)
        job_or_404(job_id)
        texto = str(body.get("texto") or "").strip()
        if not texto:
            raise HTTPException(422, "campo 'texto' vazio")
        await call(pipeline.steer, job_id, texto)
        audit(job_id, "orientação enviada")
        return {"ok": True, "obs": "o agente lê a mensagem antes da próxima ação"}

    @router.post("/jobs/{job_id}/pausar")
    async def pausar(request: Request, job_id: int) -> dict:
        await authorize(request)
        job_or_404(job_id)
        await call(pipeline.pause, job_id)
        audit(job_id, "pausa pedida")
        return {"ok": True}

    @router.post("/jobs/{job_id}/retomar")
    async def retomar(request: Request, job_id: int, body: dict = Body(default={})) -> dict:
        await authorize(request)
        j = job_or_404(job_id)
        if j["phase"] == "production":  # retomar aqui = subir de novo em produção
            need_totp("deploy", body)
        await call(pipeline.retry, job_id)
        audit(job_id, "retomada pedida")
        return {"ok": True}

    @router.post("/jobs/{job_id}/cancelar")
    async def cancelar(request: Request, job_id: int, body: dict = Body(default={})) -> dict:
        await authorize(request)
        job_or_404(job_id)
        need_totp("cancel", body)
        await call(pipeline.cancel, job_id)
        audit(job_id, "cancelado")
        return {"ok": True}

    @router.post("/projetos/{slug}/rollback")
    async def rollback(request: Request, slug: str, body: dict = Body(default={})) -> dict:
        await authorize(request)
        p = project_or_404(slug)
        need_totp("rollback", body)
        try:
            tag = await pipeline.manual_rollback(p["id"])
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        except Exception as e:  # deploy falhou: devolve o motivo para o Jarvis explicar
            raise HTTPException(502, f"rollback falhou: {e}") from None
        last = db.jobs(p["id"], limit=1)
        if last:
            audit(last[0]["id"], f"rollback de produção para {tag}")
        kb.refresh_project(p["id"])
        return {"ok": True, "versao_producao": tag}

    # ------------------------------------------------------------ Caddyfile principal (propostas)
    def caddy_or_503() -> CaddyChanges:
        if caddy is None or not c.caddy_proposals:
            raise HTTPException(503, "propostas no Caddyfile desligadas (CADDY_PROPOSALS=false no .env)")
        return caddy

    async def caddy_call(coro_or_value: Any) -> Any:
        try:
            if asyncio.iscoroutine(coro_or_value):
                return await coro_or_value
            return coro_or_value
        except ChangeError as e:
            raise HTTPException(409, str(e)) from None

    def need_caddy_totp(body: dict) -> None:
        # aplicar ou desfazer mudança no Caddyfile principal SEMPRE pede o código (não depende de JARVIS_TOTP_FOR)
        verdict = guard.verify(str(body.get("codigo") or ""), "caddy")
        if not verdict.ok:
            raise HTTPException(
                403, f"segundo fator recusado: {verdict.reason}. Peça ao Gabriel o código do app autenticador."
            )

    @router.get("/infra/caddy")
    async def caddy_estado(request: Request) -> dict:
        await authorize(request)
        return await caddy_call(caddy_or_503().current())

    @router.get("/infra/caddy/propostas")
    async def caddy_propostas(request: Request, status: str = "", limite: int = 10) -> dict:
        await authorize(request)
        ch = caddy_or_503()
        rows = db.infra_changes(status=status or None, limit=max(1, min(limite, 30)))
        return {"propostas": [ch.public(r) for r in rows]}

    @router.get("/infra/caddy/propostas/{change_id}")
    async def caddy_proposta(request: Request, change_id: int) -> dict:
        await authorize(request)
        ch = caddy_or_503()
        row = db.infra_change(change_id)
        if not row:
            raise HTTPException(404, f"proposta #{change_id} não existe")
        return ch.public(row, full=True)

    @router.post("/infra/caddy/propostas")
    async def caddy_propor(request: Request, body: dict = Body(...)) -> dict:
        await authorize(request)
        ch = caddy_or_503()
        edicoes = body.get("edicoes")
        if edicoes is not None and not isinstance(edicoes, list):
            raise HTTPException(422, "'edicoes' deve ser uma lista de {antes, depois} ou {acrescentar}")
        conteudo = body.get("conteudo")
        row = await caddy_call(
            ch.propose(
                str(body.get("motivo") or ""),
                str(body.get("base") or ""),
                conteudo=str(conteudo) if conteudo is not None else None,
                edicoes=edicoes,
            )
        )
        await pipeline.notifier.send(
            f"Caddyfile: proposta #{row['id']}", ch.summary(db.infra_change(row["id"]) or {}), "/infra", priority="high"
        )
        return {
            **row,
            "proximo_passo": (
                "mostre o diff e os alertas ao Gabriel. Para aplicar ele precisa te mandar DOIS códigos: o de 6 "
                "dígitos do autenticador e a confirmação desta proposta, que chega no celular dele (e está no "
                "painel, em Infra). Você não recebe a confirmação."
            ),
        }

    def ready(ch: CaddyChanges, change_id: int, body: dict, status: str) -> None:
        # proposta existe, está no estado certo e a confirmação dela confere ANTES de gastar o código TOTP
        row = db.infra_change(change_id)
        if not row:
            raise HTTPException(404, f"proposta #{change_id} não existe")
        if row["status"] != status:
            raise HTTPException(409, f"a proposta #{change_id} está '{row['status']}'")
        try:
            ch.check_confirmation(change_id, str(body.get("confirmacao") or ""))
        except ChangeError as e:
            raise HTTPException(403, str(e)) from None

    @router.post("/infra/caddy/propostas/{change_id}/aprovar")
    async def caddy_aprovar(request: Request, change_id: int, body: dict = Body(default={})) -> dict:
        await authorize(request)
        ch = caddy_or_503()
        ready(ch, change_id, body, "proposta")
        need_caddy_totp(body)
        return await caddy_call(ch.approve(change_id))

    @router.post("/infra/caddy/propostas/{change_id}/rejeitar")
    async def caddy_rejeitar(request: Request, change_id: int, body: dict = Body(default={})) -> dict:
        await authorize(request)
        return await caddy_call(caddy_or_503().reject(change_id, str(body.get("motivo") or "")))

    @router.post("/infra/caddy/propostas/{change_id}/desfazer")
    async def caddy_desfazer(request: Request, change_id: int, body: dict = Body(default={})) -> dict:
        await authorize(request)
        ch = caddy_or_503()
        ready(ch, change_id, body, "aplicada")
        need_caddy_totp(body)
        return await caddy_call(ch.undo(change_id))

    return router
