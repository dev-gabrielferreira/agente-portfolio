"""API do Jarvis: token próprio, papel de operador e segundo fator (TOTP) para produção."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import totp
from orchestrator.app import create_app
from orchestrator.deployer import FakeDeployer
from tests.conftest import FakeRunner

TOKEN = "t" * 40
SECRET = totp.new_secret()


@pytest.fixture
def api(config):
    config.jarvis_api_token = TOKEN
    config.admin_totp_secret = SECRET
    app = create_app(config, runner=FakeRunner(), deployer=FakeDeployer(), start_background=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c, app


def current_code(offset: int = 0) -> str:
    return totp.code_at(SECRET, totp.counter_now() + offset)


def drive(app, job_id):
    asyncio.run(app.state.pipeline.run_job(job_id))
    return app.state.db.job(job_id)


def test_api_exige_o_token_certo_e_nao_se_mistura_com_o_painel(config):
    config.jarvis_api_token = ""
    app = create_app(config, runner=FakeRunner(), start_background=False)
    c = TestClient(app)
    assert c.get("/api/v1/resumo").status_code == 503  # desligada sem token configurado

    config.jarvis_api_token = TOKEN
    app = create_app(config, runner=FakeRunner(), start_background=False)
    c = TestClient(app)
    assert c.get("/api/v1/resumo").status_code == 401
    assert c.get("/api/v1/resumo", headers={"Authorization": "Bearer errado"}).status_code == 401
    # login do painel não abre a API…
    c.post("/login", data={"password": "senha-teste"})
    assert c.get("/api/v1/resumo").status_code == 401
    # pelo proxy público (X-Forwarded-For do Caddy) a API nem existe, mesmo com o token certo
    proxied = c.get("/api/v1/resumo", headers={"Authorization": f"Bearer {TOKEN}", "X-Forwarded-For": "203.0.113.9"})
    assert proxied.status_code == 404
    # …e o token da API não abre o painel
    other = TestClient(app)
    r = other.get("/", headers={"Authorization": f"Bearer {TOKEN}"}, follow_redirects=False)
    assert r.status_code == 303


def test_fluxo_completo_pelo_jarvis_com_totp_no_deploy(api):
    c, app = api
    r = c.post("/api/v1/projetos", json={"pedido": "Um painel de energia com dados abertos da ONS", "nome": "Energia"})
    assert r.status_code == 200
    job_id = r.json()["job"]

    drive(app, job_id)
    detail = c.get(f"/api/v1/jobs/{job_id}").json()
    assert detail["esperando"] == "answers"
    assert detail["perguntas"][0] == {
        "id": "q1",
        "pergunta": "Qual período?",
        "por_que": "volume",
        "opcoes": [],
        "recomendado": "2 anos",
    }
    assert c.post(f"/api/v1/jobs/{job_id}/aprovar", json={"etapa": "spec"}).status_code == 409  # fora de hora

    assert c.post(f"/api/v1/jobs/{job_id}/respostas", json={"respostas": {"q1": "5 anos"}}).json() == {"ok": True}
    drive(app, job_id)
    detail = c.get(f"/api/v1/jobs/{job_id}").json()
    assert detail["esperando"] == "spec_approval" and "Visão do produto" in detail["spec_md"]
    assert c.post(f"/api/v1/jobs/{job_id}/aprovar", json={"etapa": "spec"}).json() == {"ok": True}

    drive(app, job_id)
    assert c.get(f"/api/v1/jobs/{job_id}").json()["esperando"] == "design_approval"
    assert c.post(f"/api/v1/jobs/{job_id}/aprovar", json={"etapa": "design"}).status_code == 200

    job = drive(app, job_id)
    assert job["waiting_for"] == "deploy_approval", job["message"]
    detail = c.get(f"/api/v1/jobs/{job_id}").json()
    assert detail["o_que_fazer"].startswith("aprovar o deploy") and "staging" in detail

    # sem código, com código errado ou com código velho: o deploy não sai
    for body in (
        {"etapa": "deploy"},
        {"etapa": "deploy", "codigo": "000000"},
        {"etapa": "deploy", "codigo": current_code(-5)},
    ):
        r = c.post(f"/api/v1/jobs/{job_id}/aprovar", json=body)
        assert r.status_code == 403, body
        assert "segundo fator" in r.json()["detail"]
    assert app.state.db.job(job_id)["waiting_for"] == "deploy_approval"

    r = c.post(f"/api/v1/jobs/{job_id}/aprovar", json={"etapa": "deploy", "codigo": current_code()})
    assert r.status_code == 200
    job = drive(app, job_id)
    assert job["status"] == "done"
    trail = [e["message"] for e in app.state.db.events(job_id) if e["kind"] == "human"]
    assert "deploy aprovado (via Jarvis)" in trail and "respostas enviadas (via Jarvis)" in trail


def test_rollback_exige_totp_e_codigo_nao_pode_ser_reusado(api):
    c, app = api
    db = app.state.db
    p = db.create_project("site", "Site")
    db.update_project(p["id"], prod_tag="v2", prev_prod_tag="v1", status="live")
    db.create_job(p["id"], "change", "ajuste", "init")

    assert c.post("/api/v1/projetos/site/rollback", json={}).status_code == 403
    code = current_code()
    r = c.post("/api/v1/projetos/site/rollback", json={"codigo": code})
    assert r.status_code == 200 and r.json()["versao_producao"] == "v1"
    r = c.post("/api/v1/projetos/site/rollback", json={"codigo": code})
    assert r.status_code == 403 and "já usado" in r.json()["detail"]


def test_resumo_eventos_e_nota_do_projeto(api):
    c, app = api
    job_id = c.post(
        "/api/v1/projetos", json={"pedido": "Uma API de encurtar links com estatísticas", "nome": "Links"}
    ).json()["job"]
    drive(app, job_id)

    resumo = c.get("/api/v1/resumo").json()
    assert resumo["esperando_voce"][0]["id"] == job_id
    assert resumo["esperando_voce"][0]["o_que_fazer"] == "responder as perguntas da descoberta"
    assert resumo["segundo_fator"] == "configurado"

    feed = c.get("/api/v1/eventos", params={"depois": 0}).json()
    assert feed["eventos"] and feed["ultimo"] == feed["eventos"][-1]["id"]
    assert {e["projeto"] for e in feed["eventos"]} == {"links"}
    assert c.get("/api/v1/eventos", params={"depois": feed["ultimo"]}).json()["eventos"] == []

    nota = c.get("/api/v1/projetos/links/nota").json()["nota"]
    assert "# Links" in nota and "links.gabrielfdev.com" in nota and "esperando: answers" in nota
    index = (app.state.config.knowledge_dir / "INDEX.md").read_text()
    assert "[[projetos/links|Links]]" in index and f"job #{job_id}" in index

    assert c.get("/api/v1/projetos/nao-existe").status_code == 404
    assert c.post("/api/v1/projetos", json={"pedido": "curto"}).status_code == 422


def test_adotar_projeto_existente_pelo_jarvis(api):
    c, app = api
    r = c.post(
        "/api/v1/projetos/adotar",
        json={
            "nome": "Chillers",
            "repositorio": "https://github.com/dev-gabrielferreira/chillers.git",
            "instrucoes": "hoje responde em chillers.gabrielfdev.com",
        },
    )
    assert r.status_code == 200
    job = app.state.db.job(r.json()["job"])
    project = app.state.db.project(job["project_id"])
    assert job["type"] == "adopt" and project["kind"] == "adopted" and r.json()["projeto"] == "chillers"
    assert project["repo_url"] == "https://github.com/dev-gabrielferreira/chillers"
    events = [e["message"] for e in app.state.db.events(job["id"])]
    assert "adoção de projeto pedida (via Jarvis)" in events
    bad = c.post("/api/v1/projetos/adotar", json={"nome": "X1", "repositorio": "https://evil.example/x"})
    assert bad.status_code == 422 and "GitHub" in bad.json()["detail"]


def test_ajustes_e_orientacao(api):
    c, app = api
    job_id = c.post(
        "/api/v1/projetos", json={"pedido": "Um app de lista de compras compartilhada", "nome": "Lista"}
    ).json()["job"]
    drive(app, job_id)
    assert c.post(f"/api/v1/jobs/{job_id}/ajustes", json={"texto": "mude o foco"}).status_code == 409
    c.post(f"/api/v1/jobs/{job_id}/respostas", json={"respostas": {"q1": "sim"}})
    drive(app, job_id)
    r = c.post(f"/api/v1/jobs/{job_id}/ajustes", json={"texto": "inclua modo offline"})
    assert r.status_code == 200
    job = app.state.db.job(job_id)
    assert job["phase"] == "spec" and job["answers"]["_spec_feedback"] == "inclua modo offline"
    assert c.post(f"/api/v1/jobs/{job_id}/orientar", json={"texto": "use SQLite"}).status_code == 200


class MemKV:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=""):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


def test_totp_segue_a_rfc_6238():
    # vetor de teste da RFC 6238 (SHA-1, segredo ASCII "12345678901234567890", T=59s → 94287082)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.code_at(secret, 59 // 30) == "287082"
    assert totp.code_at(secret, 1111111109 // 30) == "081804"


def test_totp_janela_antireplay_e_bloqueio():
    now = [1_700_000_000.0]
    store = MemKV()
    guard = totp.TotpGuard(SECRET, store, clock=lambda: now[0])
    counter = totp.counter_now(now[0])
    assert guard.verify(totp.code_at(SECRET, counter + 1), "deploy").ok  # relógio do celular adiantado
    assert not guard.verify(totp.code_at(SECRET, counter), "deploy").ok  # anterior ao último usado
    now[0] += 60
    assert guard.verify(totp.code_at(SECRET, totp.counter_now(now[0])), "deploy").ok
    for _ in range(totp.MAX_FAILURES):
        assert not guard.verify("123", "deploy").ok
    now[0] += 30
    locked = guard.verify(totp.code_at(SECRET, totp.counter_now(now[0])), "deploy")
    assert not locked.ok and "tentativas" in locked.reason
    now[0] += totp.LOCK_S
    assert guard.verify(totp.code_at(SECRET, totp.counter_now(now[0])), "deploy").ok
    assert not totp.TotpGuard("", store).verify("123456", "deploy").ok  # sem segredo, nada passa
