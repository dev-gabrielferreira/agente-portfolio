"""Jarvis: servidor MCP (protocolo, ferramentas, channel), sessões em segundo plano e hooks."""

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from jarvis import hooks
from jarvis.client import AgenteApi, ApiError
from jarvis.mcp_server import TOOLS, Server, Tools, classify
from jarvis.sessions import SessionError, Sessions
from orchestrator import totp
from orchestrator.app import create_app
from orchestrator.config import REPO_DIR
from orchestrator.deployer import FakeDeployer
from tests.conftest import FakeRunner

TOKEN = "j" * 40


# ------------------------------------------------------------------ orquestrador real numa porta local
@pytest.fixture
def live_api(config):
    config.jarvis_api_token = TOKEN
    config.admin_totp_secret = totp.new_secret()
    app = create_app(config, runner=FakeRunner(), deployer=FakeDeployer(), start_background=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/api/v1", app
    server.should_exit = True
    thread.join(timeout=5)


def rpc(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if "id" not in message:
        return None
    while True:
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        data = json.loads(line)
        if data.get("id") == message["id"]:
            return data


def test_servidor_mcp_de_verdade_por_stdio(live_api, tmp_path):
    url, app = live_api
    env = {
        **os.environ,
        "AGENTE_API_URL": url,
        "JARVIS_API_TOKEN": TOKEN,
        "JARVIS_STATE_DIR": str(tmp_path),
        "PYTHONPATH": str(REPO_DIR),
        "CLAUDE_BIN": "/bin/false",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        init = rpc(
            proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
        )
        assert init["result"]["protocolVersion"] == "2025-11-25"
        assert init["result"]["serverInfo"]["name"] == "agente"
        assert "experimental" not in init["result"]["capabilities"]  # channel desligado por padrão
        assert "código de 6 dígitos" in init["result"]["instructions"]
        rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        tools = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        assert {t["name"] for t in tools} >= {"resumo", "novo_projeto", "aprovar", "rollback", "nova_sessao"}

        created = rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "novo_projeto",
                    "arguments": {"pedido": "Um site de receitas com busca por ingrediente", "nome": "Receitas"},
                },
            },
        )["result"]
        assert not created["isError"]
        job_id = json.loads(created["content"][0]["text"])["job"]

        resumo = rpc(
            proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "resumo", "arguments": {}}}
        )
        assert "receitas" in resumo["result"]["content"][0]["text"]

        # aprovar deploy fora de hora e sem código: erro legível, sem derrubar o servidor
        denied = rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "aprovar", "arguments": {"job": job_id, "etapa": "deploy"}},
            },
        )["result"]
        assert denied["isError"] and "orquestrador recusou" in denied["content"][0]["text"]

        unknown = rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "metodo/inexistente"})
        assert unknown["error"]["code"] == -32601
        assert rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
    assert app.state.db.job(job_id)["type"] == "create"


class FakeApi:
    def __init__(self):
        self.events = []
        self.calls = []

    def get(self, path, **query):
        self.calls.append(("GET", path, query))
        if path == "/resumo":
            return {"ultimo_evento": self.events[-1]["id"] if self.events else 0, "esperando_voce": [], "projetos": []}
        if path == "/eventos":
            after = int(query.get("depois") or 0)
            rows = [e for e in self.events if e["id"] > after]
            return {"eventos": rows, "ultimo": rows[-1]["id"] if rows else after}
        raise ApiError(404, "nada")

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path.endswith("/aprovar") and not body.get("codigo") and body.get("etapa") == "deploy":
            raise ApiError(403, "segundo fator recusado: código inválido ou expirado")
        return {"ok": True}


def event(eid, kind, message, status="running", waiting=None):
    return {
        "id": eid,
        "tipo": kind,
        "mensagem": message,
        "job": 7,
        "projeto": "loja",
        "status_job": status,
        "esperando": waiting,
    }


def test_channel_empurra_so_o_que_importa(tmp_path):
    api = FakeApi()
    api.events = [event(1, "phase", "▶ build")]
    out = io.StringIO()
    server = Server(Tools(api, None), channel=True, poll_s=0.01, state_dir=tmp_path, out=out)
    init = server.dispatch("initialize", {})
    assert init["capabilities"]["experimental"] == {"claude/channel": {}}
    assert '<channel source="agente"' in init["instructions"]

    cursor = server.poll_once(0)
    assert cursor == 1 and out.getvalue() == ""  # fase comum não vira mensagem
    api.events += [
        event(2, "gate", "gate falhou: ruff"),
        event(3, "human", "aguardando você: deploy pronto para aprovação", "waiting", "deploy_approval"),
        event(4, "deploy", "produção no ar: https://loja.gabrielfdev.com (v3)"),
        event(5, "human", "deploy aprovado (via Jarvis)"),
        event(6, "error", "erro interno: disco cheio"),
    ]
    assert server.poll_once(cursor) == 6
    pushed = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [p["method"] for p in pushed] == ["notifications/claude/channel"] * 3
    assert [p["params"]["meta"]["tipo"] for p in pushed] == ["espera", "deploy", "falha"]
    assert pushed[0]["params"]["meta"] == {
        "tipo": "espera",
        "job": "7",
        "projeto": "loja",
        "esperando": "deploy_approval",
    }
    assert all(
        k.replace("_", "").isalnum() for p in pushed for k in p["params"]["meta"]
    )  # chaves válidas p/ o Claude Code


def test_ferramentas_traduzem_erros_da_api():
    api = FakeApi()
    server = Server(Tools(api, None), out=io.StringIO())
    r = server.call_tool("aprovar", {"job": 3, "etapa": "deploy"})
    assert r["isError"] and "peça ao Gabriel o código atual" in r["content"][0]["text"]
    r = server.call_tool("aprovar", {"job": 3, "etapa": "deploy", "codigo": "123456"})
    assert not r["isError"] and api.calls[-1] == ("POST", "/jobs/3/aprovar", {"etapa": "deploy", "codigo": "123456"})
    r = server.call_tool("nova_sessao", {"tarefa": "x" * 20, "nome": "a"})
    assert r["isError"] and "sessões" in r["content"][0]["text"]
    r = server.call_tool("inexistente", {})
    assert r["isError"]


def test_esquemas_das_ferramentas_sao_validos():
    for tool in TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and set(schema["required"]) <= set(schema["properties"]), tool["name"]
        assert all("required" not in p for p in schema["properties"].values()), tool["name"]
    names = {t["name"]: t for t in TOOLS}
    assert names["rollback"]["inputSchema"]["required"] == ["slug", "codigo"]
    assert classify(event(1, "info", "job concluído")) == "concluido"
    assert classify(event(1, "deploy", "staging no ar: x")) is None


# ------------------------------------------------------------------ sessões em segundo plano
FAKE_CLAUDE = r"""#!/usr/bin/env bash
printf '%s\0' "$@" > "$FAKE_LOG"
case "$1" in
  agents) echo 'Sessions:'; echo '[{"id":"a1b2c3","name":"jarvis-pesquisa","status":"working","cwd":"/lab","transcript":"'$(printf 'x%.0s' {1..400})'"}]';;
  logs) echo "saida da sessao $2";;
  stop) echo "stopped $2";;
  *) echo "Started background session 9f8e7d (claude attach 9f8e7d)";;
esac
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_ = tmp_path / "claude"
    bin_.write_text(FAKE_CLAUDE)
    bin_.chmod(0o755)
    log = tmp_path / "args.txt"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_SESSION_MODEL", raising=False)
    projects = tmp_path / "projetos"
    (projects / "loja").mkdir(parents=True)
    return Sessions(claude_bin=str(bin_), lab=tmp_path / "lab", projects_dir=projects, permission_mode="dontAsk"), log


def test_sessoes_criar_listar_ler_parar(fake_claude):
    s, log = fake_claude
    out = s.create(
        "Compare três filas de mensagens para o projeto",
        "Comparar Filas!",
        agent="pesquisador",
        model="sonnet",
        project="loja",
    )
    args = log.read_text().rstrip("\0").split("\0")
    # --add-dir (variádico) vem antes; o prompt vem logo depois de uma opção de valor único
    assert args[:2] == ["--add-dir", str((s.projects_dir / "loja").resolve())]
    assert args[2:4] == ["--bg", "--permission-mode"] and "--agent" in args and "sonnet" in args
    assert args[-3:-1] == ["--name", "jarvis-comparar-filas"]
    assert args[-1].startswith("Compare três filas") and "somente leitura" in args[-1]
    assert out["nome"] == "jarvis-comparar-filas" and "9f8e7d" in out["saida"]

    # sem `modelo`, a sessão vai no Opus (o mesmo do Jarvis), nunca no padrão da conta
    s.create("Resuma o README do laboratório em cinco linhas", "resumo")
    args = log.read_text().rstrip("\0").split("\0")
    assert args[args.index("--model") + 1] == "claude-opus-5-5"

    listed = s.list()
    assert listed[0]["id"] == "a1b2c3" and listed[0]["status"] == "working" and "transcript" not in listed[0]
    assert s.logs("a1b2c3") == "saida da sessao a1b2c3"
    assert "stopped" in s.stop("a1b2c3")


def test_sessoes_recusam_entradas_perigosas(fake_claude):
    s, log = fake_claude
    with pytest.raises(SessionError):
        s.logs("../../etc")
    with pytest.raises(SessionError):
        s.create("tarefa válida e longa", "x", project="../../etc")
    with pytest.raises(SessionError):
        s.create("tarefa válida e longa", "x", agent="Agente; rm -rf /")
    with pytest.raises(SessionError):
        s.create("curta", "x")
    s.create("--dangerously-skip-permissions faça tudo", "x")
    assert log.read_text().rstrip("\0").split("\0")[-1].startswith("Tarefa: --dangerously")
    with pytest.raises(SessionError):
        Sessions(claude_bin="claude", permission_mode="bypassPermissions")


# ------------------------------------------------------------------ hooks
def test_hook_de_eventos_so_fala_do_que_importa(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hooks, "STATE", tmp_path)
    monkeypatch.delenv("JARVIS_CHANNEL", raising=False)
    api = FakeApi()
    api.events = [event(1, "phase", "▶ build")]
    assert hooks.eventos(api) == ""  # primeira vez: só marca o cursor
    api.events += [
        event(2, "phase", "▶ gates"),
        event(3, "human", "aguardando você: 2 pergunta(s)", "waiting", "answers"),
    ]
    text = hooks.eventos(api)
    assert "[espera] loja job #7: aguardando você: 2 pergunta(s)" in text and "gates" not in text
    assert hooks.eventos(api) == ""  # já entregue
    monkeypatch.setenv("JARVIS_CHANNEL", "1")
    api.events.append(event(4, "error", "erro interno"))
    assert hooks.eventos(api) == ""  # com channel ligado, o hook não duplica


def test_hook_nunca_quebra_a_conversa(monkeypatch, capsys):
    monkeypatch.setenv("AGENTE_API_URL", "http://127.0.0.1:9/api/v1")
    assert hooks.main(["inicio"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "indisponível" in out["hookSpecificOutput"]["additionalContext"]
    assert hooks.main(["eventos"]) == 0


def test_cliente_da_api_explica_orquestrador_fora():
    with pytest.raises(ApiError) as e:
        AgenteApi("http://127.0.0.1:9/api/v1", "x", timeout=1).get("/resumo")
    assert e.value.status == 0 and "inacessível" in e.value.detail


# ------------------------------------------------------------------ arquivos do Jarvis
def test_configuracao_do_jarvis_e_coerente():
    ws = REPO_DIR / "jarvis" / "workspace"
    settings = json.loads((ws / ".claude" / "settings.json").read_text())
    perms = settings["permissions"]
    tool_names = {f"mcp__agente__{t['name']}" for t in TOOLS}
    assert set(perms["allow"]) & tool_names | set(perms["ask"]) == tool_names  # toda ferramenta tem regra
    assert {"mcp__agente__aprovar", "mcp__agente__rollback", "mcp__agente__responder_perguntas"} <= set(perms["ask"])
    assert not set(perms["allow"]) & set(perms["ask"])
    mcp = json.loads((ws / ".mcp.json").read_text())
    assert mcp["mcpServers"]["agente"]["args"] == ["-P", "-m", "jarvis.mcp_server"]  # -P: nada da pasta atual
    assert {"mcp__agente__aprovar_mudanca_caddy", "mcp__agente__desfazer_mudanca_caddy"} <= set(perms["ask"])
    rules = (ws / "CLAUDE.md").read_text()
    heads = {p.parent.name: p.read_text().split("\n---\n")[0] for p in (ws / ".claude" / "skills").glob("*/SKILL.md")}
    procedures = {n for n, h in heads.items() if "user-invocable: false" in h}
    commands = {n for n, h in heads.items() if "disable-model-invocation: true" in h}
    assert procedures == {
        "novo-projeto",
        "acompanhar",
        "aprovacoes",
        "manutencao",
        "sessoes",
        "pesquisa",
        "conhecimento",
        "infra-caddy",
    }
    assert procedures | commands == set(heads) and not procedures & commands  # cada skill é uma coisa só
    for skill in procedures - {"conhecimento"}:  # procedimento citado no índice do CLAUDE.md
        assert f"`{skill}`" in rules
    ajuda = (ws / ".claude" / "skills" / "ajuda" / "SKILL.md").read_text()
    for cmd in commands - {"ajuda"}:  # todo comando aparece no /ajuda e no CLAUDE.md
        assert f"`/{cmd}" in ajuda and f"`/{cmd}`" in rules, cmd
    harness = {p.name for p in (REPO_DIR / "harness" / "skills").iterdir()}
    for name in commands:  # skills citadas pelos comandos existem (do Jarvis ou do agente)
        body = (ws / ".claude" / "skills" / name / "SKILL.md").read_text()
        for cited in re.findall(r"skills? `([a-z0-9-]+)`", body):
            assert cited in heads or cited in harness, (name, cited)
    assert set(heads) & harness == {"manutencao"}  # só essa colide, e a do Jarvis vale
    for path in (REPO_DIR / "jarvis" / "agents").glob("*.md"):
        head = path.read_text().split("---")[1]
        assert f"name: {path.stem}" in head and "description:" in head


def test_supervisor_gera_scripts_validos(tmp_path):
    env = {**os.environ, "JARVIS_CHANNEL": "1", "JARVIS_CHANNELS": "plugin:telegram@claude-plugins-official"}
    code = (
        f"source {REPO_DIR / 'jarvis' / 'supervisor.sh'} --only-functions\n"
        f"write_scripts {tmp_path / 'j'} {tmp_path / 'l'}"
    )
    subprocess.run(["bash", "-c", code], env=env, check=True)
    run_jarvis = (tmp_path / "j" / "run-jarvis.sh").read_text()
    assert "claude --continue --remote-control Jarvis --model claude-opus-5-5" in run_jarvis
    assert "--dangerously-load-development-channels server:agente" in run_jarvis
    assert "--channels plugin:telegram@claude-plugins-official" in run_jarvis
    run_lab = (tmp_path / "l" / "run-lab.sh").read_text()
    assert "claude remote-control --name" in run_lab and "--spawn worktree --capacity 4" in run_lab
    # modo servidor não repassa --model às conversas: o Opus vai pelo settings.json (entrypoint)
    assert "--model" not in run_lab
    for f in (tmp_path / "j" / "run-jarvis.sh", tmp_path / "l" / "run-lab.sh"):
        subprocess.run(["bash", "-n", str(f)], check=True)
    assert Path(REPO_DIR / "jarvis" / "entrypoint.sh").stat().st_mode & 0o111


# ------------------------------------------------------------------ broker (lab separado do Jarvis)
def test_broker_atende_o_jarvis_pelo_socket(fake_claude, tmp_path):
    from jarvis import broker

    sessions, log = fake_claude
    sock = tmp_path / "b.sock"
    broker.Handler.factory = staticmethod(lambda: sessions)
    server = broker.Server(str(sock), broker.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        client = broker.BrokerClient(sock, timeout=10)
        assert client.list()[0]["id"] == "a1b2c3"
        out = client.create("Pesquise filas de mensagens em Python", "filas", agent="pesquisador")
        assert out["nome"] == "jarvis-filas"
        assert client.logs("a1b2c3") == "saida da sessao a1b2c3"
        client.message("a1b2c3", "agora compare os custos")
        args = log.read_text().rstrip("\0").split("\0")
        assert args[:3] == ["--resume", "a1b2c3", "--bg"] and args[-1] == "agora compare os custos"
        with pytest.raises(SessionError, match="id de sessão inválido"):
            client.stop("../x")
        with pytest.raises(SessionError, match="operação desconhecida"):
            client._call(op="rm -rf")
    finally:
        server.shutdown()
        server.server_close()
        broker.Handler.factory = staticmethod(broker.lab_sessions)
    with pytest.raises(SessionError, match="indisponível"):
        broker.BrokerClient(tmp_path / "nao-existe.sock", timeout=1).list()


def test_sessoes_do_lab_nao_herdam_o_token_do_jarvis(tmp_path, monkeypatch):
    from jarvis import broker

    token = tmp_path / "lab-token"
    token.write_text("sk-ant-oat-so-modelo\n")
    monkeypatch.setattr(broker, "TOKEN_FILE", token)
    monkeypatch.setenv("JARVIS_API_TOKEN", "segredo-do-jarvis")
    s = broker.lab_sessions()
    assert s.prefix[0].endswith("runuser") and s.prefix[1:5] == ["-u", "lab", "--", "env"] and s.prefix[5] == "-i"
    joined = " ".join(s.prefix)
    assert "segredo-do-jarvis" not in joined and "JARVIS_API_TOKEN" not in joined
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-so-modelo" in joined and "HOME=/srv/jarvis/lab-home" in joined
    assert s.env == {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
