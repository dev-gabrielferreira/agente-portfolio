"""Regressões dos achados da revisão independente: links simbólicos plantados pelo agente, tickets
entre jobs, produção sem segundo fator, ajustes na fase certa e manifesto travado."""

import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from orchestrator import integrity, safefs, totp
from orchestrator.app import create_app
from orchestrator.config import REPO_DIR
from orchestrator.deployer import FakeDeployer
from orchestrator.knowledge import KnowledgeBase
from tests.conftest import FakeRunner, project_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def advance(pipeline, job_id):
    await pipeline.run_job(job_id)
    return pipeline.db.job(job_id)


async def to_spec_approval(pipeline, runner, request="Uma API de cotação de frete com regras por região"):
    runner.stack_tags = ["api"]
    job = pipeline.new_project(request, "Frete")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval", job["message"]
    return job


def test_safefs_nao_segue_links(tmp_path):
    secret = tmp_path / "segredo"
    secret.write_text("TOKEN=abc")
    proj = tmp_path / "proj"
    (proj / ".harness").mkdir(parents=True)
    (proj / "SPEC.md").symlink_to(secret)
    (proj / "docs").symlink_to(tmp_path)
    assert safefs.read_text(proj, "SPEC.md") == ""
    assert safefs.read_text(proj, "docs/segredo") == ""
    assert safefs.read_text(proj, "../segredo") == ""
    safefs.write_text(proj, "SPEC.md", "spec nova")
    assert secret.read_text() == "TOKEN=abc" and (proj / "SPEC.md").read_text() == "spec nova"
    safefs.write_text(proj, "docs/x.md", "x")
    assert not (proj / "docs").is_symlink() and not (tmp_path / "x.md").exists()


async def test_ticket_apontando_para_fora_reprova_o_plano(env, tmp_path):
    pipeline, db, runner, *_ = env
    job = pipeline.new_project("Uma API de cotação de frete com regras por região", "Frete")
    runner.stack_tags = ["api"]
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    real = runner._plan

    def plan_evil(pdir, task=""):
        result = real(pdir, task)
        data = json.loads((pdir / ".harness" / "tickets.json").read_text())
        data["tickets"][0]["file"] = "/proc/self/environ"
        (pdir / ".harness" / "tickets.json").write_text(json.dumps(data))
        return result

    runner._plan = plan_evil
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human" and "não existe em .harness/tickets/" in job["message"]


async def test_tickets_json_como_link_nao_escreve_no_alvo(env, tmp_path):
    pipeline, db, runner, *_ = env
    job = await to_spec_approval(pipeline, runner)
    pdir = project_dir(pipeline, job)
    victim = tmp_path / "vitima.db"
    victim.write_text("dados do orquestrador")
    real = pdir / ".harness" / "tickets.json"
    content = real.read_text()
    real.unlink()
    real.symlink_to(victim)
    pipeline._save_tickets(pdir, json.loads(content)["tickets"])
    assert victim.read_text() == "dados do orquestrador"
    assert not real.is_symlink() and json.loads(real.read_text())["tickets"]


async def test_spec_como_link_nao_vaza_para_o_jarvis_nem_para_a_base(config, tmp_path):
    config.jarvis_api_token = "t" * 40
    app = create_app(config, runner=FakeRunner(), deployer=FakeDeployer(), start_background=False)
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + "t" * 40
    job_id = c.post("/api/v1/projetos", json={"pedido": "Um painel de energia com dados abertos da ONS"}).json()["job"]
    await app.state.pipeline.run_job(job_id)
    await app.state.pipeline.submit_answers(job_id, {})
    await app.state.pipeline.run_job(job_id)
    pdir = project_dir(app.state.pipeline, app.state.db.job(job_id))
    leak = tmp_path / "environ"
    leak.write_text("ADMIN_TOTP_SECRET=NAOPODEVAZAR")
    (pdir / "SPEC.md").unlink()
    (pdir / "SPEC.md").symlink_to(leak)
    detail = c.get(f"/api/v1/jobs/{job_id}").json()
    assert "NAOPODEVAZAR" not in json.dumps(detail)
    project = app.state.db.job(job_id)["project_id"]
    KnowledgeBase(config, app.state.db).refresh_project(project)
    assert "NAOPODEVAZAR" not in (config.knowledge_dir / "projetos" / f"{pdir.name}.md").read_text()


async def test_incidente_nao_herda_tickets_de_outro_job(env):
    pipeline, db, runner, *_ = env
    job = await to_spec_approval(pipeline, runner)
    pdir = project_dir(pipeline, job)
    pipeline.approve_spec(job["id"])
    await advance(pipeline, job["id"])
    pipeline.approve_deploy(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["status"] == "done"
    data = json.loads((pdir / ".harness" / "tickets.json").read_text())
    data["tickets"].append(
        {"id": "T99", "title": "Exportar CSV (sobra)", "file": ".harness/tickets/T01.md", "status": "todo", "job": 999}
    )
    (pdir / ".harness" / "tickets.json").write_text(json.dumps(data))
    inc = pipeline.new_job(job["project_id"], "incident", "500 no /cotacao desde ontem")
    await advance(pipeline, inc["id"])
    builder_task = next(c.task for c in reversed(runner.calls) if c.role == "builder")
    assert "T99" not in builder_task and "incidente" in builder_task


async def test_ajuste_com_job_parado_no_plano_volta_para_o_plano(env):
    pipeline, db, runner, *_ = env
    runner.plan_breaks = "manifest"
    job = pipeline.new_project("Um diário de manutenção de chillers com gráficos", "Chillers")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert (job["waiting_for"], job["phase"]) == ("human", "plan")
    await pipeline.request_changes(job["id"], "corrija o manifesto: falta o start")
    job = db.job(job["id"])
    assert job["phase"] == "plan" and job["answers"]["_spec_feedback"].startswith("corrija o manifesto")
    runner.plan_breaks = ""
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval"
    assert "corrija o manifesto" in runner.calls[-1].task


def test_retomar_em_producao_exige_totp_e_deploy_sempre_exige(config):
    config.jarvis_api_token = "t" * 40
    config.admin_totp_secret = secret = totp.new_secret()
    config.jarvis_totp_for = ""  # mesmo "vazio", deploy e rollback continuam exigindo o código
    app = create_app(config, runner=FakeRunner(), deployer=FakeDeployer(), start_background=False)
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + "t" * 40
    db = app.state.db
    p = db.create_project("site", "Site")
    j = db.create_job(p["id"], "change", "ajuste", "production")
    db.update_job(j["id"], status="waiting", waiting_for="human", message="produção falhou")
    assert c.post(f"/api/v1/jobs/{j['id']}/retomar", json={}).status_code == 403
    code = totp.code_at(secret, totp.counter_now())
    assert c.post(f"/api/v1/jobs/{j['id']}/retomar", json={"codigo": code}).status_code == 200
    j2 = db.create_job(p["id"], "change", "outro", "review")
    db.update_job(j2["id"], status="waiting", waiting_for="deploy_approval")
    assert c.post(f"/api/v1/jobs/{j2['id']}/aprovar", json={"etapa": "deploy"}).status_code == 403


def test_manifesto_travado_no_plano_nao_pode_desligar_sensores(tmp_path):
    import shutil

    from orchestrator import stack

    proj = tmp_path / "p"
    shutil.copytree(REPO_DIR / "harness" / "project-template", proj)
    shutil.copy2(REPO_DIR / "harness" / "starters" / "fastapi-react" / "stack.json", proj / ".harness" / "stack.json")
    stack.apply_starter(proj, REPO_DIR / "harness")
    locked = stack.gate_keys(stack.load(proj))
    assert integrity.config_problems(proj, locked) == []
    data = stack.load(proj)
    data["frontend"] = None
    data["backend"]["domain"] = "app/vazio"
    data["ui_paths"] = []
    (proj / ".harness" / "stack.json").write_text(json.dumps(data))
    problems = " ".join(integrity.config_problems(proj, locked))
    assert "frontend.dir" in problems and "backend.domain" in problems and "ui_paths" in problems


def test_restaurar_gate_nao_segue_link_plantado(tmp_path):
    template = REPO_DIR / "harness" / "project-template"
    proj = tmp_path / "p"
    (proj / "scripts").mkdir(parents=True)
    victim = tmp_path / "entrypoint.sh"
    victim.write_text("#!/bin/sh\necho original\n")
    (proj / "scripts" / "check.sh").symlink_to(victim)
    changed = integrity.restore_gates(template, proj)
    assert "scripts/check.sh" in changed
    assert victim.read_text() == "#!/bin/sh\necho original\n"
    assert not (proj / "scripts" / "check.sh").is_symlink()
    assert os.access(proj / "scripts" / "check.sh", os.X_OK)


def test_tickets_da_mudanca_sao_carimbados_com_o_job(env):
    pipeline, db, runner, *_ = env
    job = asyncio.run(to_spec_approval(pipeline, runner))
    pdir = project_dir(pipeline, job)
    tickets = json.loads((pdir / ".harness" / "tickets.json").read_text())["tickets"]
    assert {t["job"] for t in tickets} == {job["id"]}
