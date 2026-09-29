from fastapi.testclient import TestClient

from orchestrator.app import create_app
from tests.conftest import FakeRunner


def client(config):
    app = create_app(config, runner=FakeRunner(), start_background=False)
    return TestClient(app), app


def test_exige_login(config):
    c, _ = client(config)
    assert c.get("/", follow_redirects=False).status_code == 303
    assert c.post("/new", data={"request": "x" * 30}).status_code == 401
    assert c.get("/healthz").status_code == 200


def test_fluxo_basico_do_painel(config):
    c, app = client(config)
    assert c.post("/login", data={"password": "senha-teste"}).status_code == 200
    r = c.post("/new", data={"request": "Um painel de energia com dados abertos da ONS e alertas", "name": "Energia"})
    assert r.status_code == 200 and "/jobs/1" in str(r.url)
    assert "Painel" in c.get("/").text
    assert c.get("/jobs/1/log").json() == {"text": "", "offset": 0}
    assert c.get("/projects/1").status_code == 200
    r = c.post("/projects/1/secrets/production", data={"content": "LLM_API_KEY=abc\nlixo\n"})
    assert r.status_code == 200
    secret = config.secrets_dir / "energia.production.env"
    assert secret.read_text() == "LLM_API_KEY=abc\n" and oct(secret.stat().st_mode)[-3:] == "600"
    assert "abc" not in c.get("/projects/1").text  # valores nunca aparecem no painel
    assert c.get("/lessons").status_code == 200
    assert c.get("/public/portfolio.json").json() == []


def test_senha_errada(config):
    c, _ = client(config)
    r = c.post("/login", data={"password": "errada"})
    assert "Senha incorreta" in r.text


def test_aprovacao_de_design_e_arquivos_servidos_com_sandbox(config):
    import asyncio

    c, app = client(config)
    c.post("/login", data={"password": "senha-teste"})
    c.post("/new", data={"request": "Um painel de alarmes prediais com histórico e reconhecimento", "name": "Alarmes"})
    p = app.state.pipeline
    asyncio.run(p.run_job(1))
    asyncio.run(p.submit_answers(1, {}))
    asyncio.run(p.run_job(1))
    p.approve_spec(1)
    asyncio.run(p.run_job(1))
    page = c.get("/jobs/1").text
    assert "Aprovar design" in page and "sala de controle" in page
    shot = c.get("/jobs/1/file", params={"path": ".harness/evidence/design/home-desktop.png"})
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
    mock = c.get("/jobs/1/file", params={"path": "design/mockups/home.html"})
    assert mock.status_code == 200 and mock.headers["content-security-policy"].startswith("sandbox")
    for bad in ("../../agente.db", "app/main.py", ".harness/../../../../etc/passwd", "SPEC.md"):
        assert c.get("/jobs/1/file", params={"path": bad}).status_code == 404, bad
    assert c.post("/jobs/1/approve-design").status_code == 200
    assert app.state.db.job(1)["phase"] == "build"


def test_pagina_do_harness(config):
    c, _ = client(config)
    c.post("/login", data={"password": "senha-teste"})
    html = c.get("/harness").text
    assert "testes-que-importam" in html and "context7" in html and "anthropics/skills" in html
