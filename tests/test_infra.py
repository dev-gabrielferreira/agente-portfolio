"""Propostas de mudança no Caddyfile principal: regras, segredos, validação, TOTP, reversão e desfazer."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import infra, totp
from orchestrator.app import create_app
from orchestrator.db import DB
from orchestrator.deployer import FakeDeployer
from orchestrator.infra import CaddyChanges, ChangeError, digest
from tests.conftest import FakeRunner

HASH = "$2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG"
CADDYFILE = f"""{{
\temail gabriel@example.com
}}

agent.gabrielfdev.com {{
\t@api path /api/*
\trespond @api 404
\treverse_proxy agente:8080
}}

gabrielfdev.com, www.gabrielfdev.com {{
\troot * /srv/site
\tfile_server
}}

chillers.gabrielfdev.com {{
\tbasic_auth {{
\t\tgabriel {HASH}
\t}}
\treverse_proxy chillers-velho:8000 {{
\t\theader_up X-Api-Key abcdef1234567890
\t}}
}}

import /etc/caddy/sites-agente/*.caddy
"""


class FakeCaddy:
    def __init__(self, content: str = CADDYFILE):
        self.content = content
        self.can_write = True
        self.reload_ok = True
        self.writes: list[str] = []
        self.reloads = 0

    async def read(self) -> str:
        return self.content

    async def writable(self) -> bool:
        return self.can_write

    async def validate(self, content: str) -> tuple[bool, str]:
        if "INVALIDO" in content:
            return False, "Error: adapting config using caddyfile: unrecognized directive: INVALIDO"
        return True, "Valid configuration"

    async def write(self, content: str) -> None:
        if not self.can_write:
            raise ChangeError("montado só leitura")
        self.writes.append(content)
        self.content = content

    async def reload(self) -> tuple[bool, str]:
        self.reloads += 1
        return self.reload_ok, "" if self.reload_ok else "reload falhou: porta em uso"


class Prober:
    """Status por host; `after` passa a valer depois da primeira medição (antes → depois do reload)."""

    def __init__(self, before: dict[str, int] | None = None, after: dict[str, int] | None = None):
        self.before = before or {}
        self.after = after if after is not None else self.before
        self.calls = 0

    async def __call__(self, targets: list[str]) -> dict[str, int]:
        table = self.before if self.calls == 0 else self.after
        self.calls += 1
        return {h: table.get(h, 200) for h in targets}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc(config):
    host = FakeCaddy()
    prober = Prober()
    return CaddyChanges(config, DB(config.db_path), host, prober, settle_s=0), host, prober


def base(host: FakeCaddy) -> str:
    return digest(host.content)


# ------------------------------------------------------------------ leitura e segredos
def test_leitura_esconde_segredos_e_lista_sites(svc):
    s, host, _ = svc
    state = run(s.current())
    assert HASH not in state["conteudo"] and "abcdef1234567890" not in state["conteudo"]
    assert "gabriel «segredo-1»" in state["conteudo"] and "X-Api-Key «segredo-2»" in state["conteudo"]
    assert state["sites"] == [
        "agent.gabrielfdev.com",
        "gabrielfdev.com",
        "www.gabrielfdev.com",
        "chillers.gabrielfdev.com",
    ]
    assert state["versao"] == base(host) and state["gravavel"] is True


def test_edicao_com_marcador_volta_o_segredo_real(svc):
    s, host, _ = svc
    row = run(
        s.propose(
            "troca o upstream do chillers para o container novo",
            base(host),
            edicoes=[{"antes": "reverse_proxy chillers-velho:8000", "depois": "reverse_proxy chillers-novo:8000"}],
        )
    )
    assert row["status"] == "proposta" and "chillers-novo" in row["diff"]
    assert HASH not in row["diff"] and "abcdef1234567890" not in row["diff"]
    stored = s.db.infra_change(row["id"])
    assert HASH in stored["after"] and "abcdef1234567890" in stored["after"]  # o arquivo real mantém os segredos

    # o modelo reescreve o arquivo inteiro a partir da versão sem segredos: os marcadores voltam ao valor real
    text = run(s.current())["conteudo"].replace("www.gabrielfdev.com", "www2.gabrielfdev.com")
    row2 = run(s.propose("renomeia o www para www2 (teste)", base(host), conteudo=text))
    assert HASH in s.db.infra_change(row2["id"])["after"]
    with pytest.raises(ChangeError, match="marcador «segredo-9» não existe"):
        run(s.propose("marcador inventado pelo modelo", base(host), conteudo=text + "\n# «segredo-9»\n"))


# ------------------------------------------------------------------ regras
@pytest.mark.parametrize(
    ("antes", "depois", "motivo"),
    [
        ("import /etc/caddy/sites-agente/*.caddy\n", "", "remove o `import` dos sites do agente"),
        ("\trespond @api 404\n", "", "remove a proteção da API do Jarvis"),
        (
            "\temail gabriel@example.com\n",
            "\temail gabriel@example.com\n\tadmin 0.0.0.0:2019\n",
            "expõe a API de administração",
        ),
    ],
)
def test_regras_bloqueiam_o_que_derruba_ou_expoe(svc, antes, depois, motivo):
    s, host, _ = svc
    with pytest.raises(ChangeError, match=motivo):
        run(s.propose("mudança perigosa de teste", base(host), edicoes=[{"antes": antes, "depois": depois}]))
    assert s.db.infra_changes() == []


def test_site_do_agente_nao_pode_ser_duplicado(svc, config):
    s, host, _ = svc
    (config.caddy_sites_dir / "loja-production.caddy").write_text("loja.gabrielfdev.com {\n}\n")
    with pytest.raises(ChangeError, match=r"loja\.gabrielfdev\.com já é servido pelo agente"):
        run(
            s.propose(
                "site da loja à mão", base(host), edicoes=[{"acrescentar": "loja.gabrielfdev.com {\n\trespond ok\n}"}]
            )
        )


def test_alertas_acompanham_o_diff(svc):
    s, host, _ = svc
    edits = [
        {
            "antes": "chillers.gabrielfdev.com {\n\tbasic_auth {\n\t\tgabriel «segredo-1»\n\t}\n",
            "depois": "chillers.gabrielfdev.com {\n",
        },
        {"acrescentar": "blog.gabrielfdev.com {\n\troot * /\n\tfile_server browse\n}"},
    ]
    row = run(s.propose("abre o chillers e cria o blog", base(host), edicoes=edits))
    alerts = " | ".join(row["alertas"])
    assert "remove uma proteção por senha" in alerts and "site novo blog.gabrielfdev.com" in alerts
    assert "pasta de sistema" in alerts and "listagem de pastas" in alerts


def test_base_errada_trecho_ambiguo_e_invalido_nao_viram_proposta(svc):
    s, host, _ = svc
    with pytest.raises(ChangeError, match="o Caddyfile mudou"):
        run(s.propose("qualquer mudança", "0000", edicoes=[{"acrescentar": "a.gabrielfdev.com {\n}"}]))
    with pytest.raises(ChangeError, match="aparece 2 vez"):
        run(s.propose("trecho ambíguo", base(host), edicoes=[{"antes": "reverse_proxy", "depois": "x"}]))
    with pytest.raises(ChangeError, match="o Caddy recusou"):
        run(s.propose("diretiva inválida", base(host), edicoes=[{"acrescentar": "a.gabrielfdev.com {\n\tINVALIDO\n}"}]))
    assert s.db.infra_changes() == []


# ------------------------------------------------------------------ aplicar, reverter, desfazer
def propose_redirect(s, host):
    return run(
        s.propose(
            "redireciona www para o domínio principal",
            base(host),
            edicoes=[
                {"antes": "gabrielfdev.com, www.gabrielfdev.com {", "depois": "gabrielfdev.com {"},
                {"acrescentar": "www.gabrielfdev.com {\n\tredir https://gabrielfdev.com{uri} permanent\n}"},
            ],
        )
    )


def test_aprovar_aplica_com_backup_e_marca_as_outras_como_obsoletas(svc, config):
    s, host, _ = svc
    row = propose_redirect(s, host)
    other = run(
        s.propose("outra ideia sobre a mesma versão", base(host), edicoes=[{"acrescentar": "x.gabrielfdev.com {\n}"}])
    )
    before = host.content
    done = run(s.approve(row["id"]))
    assert done["status"] == "aplicada" and host.reloads == 1
    assert "redir https://gabrielfdev.com{uri}" in host.content and HASH in host.content
    backups = list(config.infra_dir.glob(f"caddyfile-{row['id']}-antes-*"))
    assert len(backups) == 1 and backups[0].read_text() == before
    assert s.db.infra_change(other["id"])["status"] == "obsoleta"
    with pytest.raises(ChangeError, match="não pendente"):
        run(s.approve(row["id"]))


def test_reload_recusado_volta_o_arquivo_anterior(svc):
    s, host, _ = svc
    row = propose_redirect(s, host)
    original = host.content
    host.reload_ok = False
    done = run(s.approve(row["id"]))
    assert done["status"] == "revertida" and "recusou o reload" in done["resultado"]
    assert host.content == original


def test_site_que_parou_de_responder_desfaz_sozinho(config):
    host = FakeCaddy()
    prober = Prober(before={"chillers.gabrielfdev.com": 200}, after={"chillers.gabrielfdev.com": 502})
    s = CaddyChanges(config, DB(config.db_path), host, prober, settle_s=0)
    original = host.content
    row = run(
        s.propose(
            "upstream novo do chillers",
            base(host),
            edicoes=[{"antes": "chillers-velho:8000", "depois": "chillers-novo:8000"}],
        )
    )
    done = run(s.approve(row["id"]))
    assert done["status"] == "revertida" and "chillers.gabrielfdev.com (200→502)" in done["resultado"]
    assert host.content == original


def test_site_removido_de_proposito_nao_conta_como_quebra(config):
    host = FakeCaddy()
    prober = Prober(before={"chillers.gabrielfdev.com": 200}, after={"chillers.gabrielfdev.com": 0})
    s = CaddyChanges(config, DB(config.db_path), host, prober, settle_s=0)
    shown = run(s.current())["conteudo"]  # o que o Jarvis vê (com marcadores)
    block = shown[shown.index("chillers.gabrielfdev.com {") : shown.index("import /etc/caddy")]
    row = run(
        s.propose(
            "tira o bloco antigo do chillers (projeto adotado)", base(host), edicoes=[{"antes": block, "depois": ""}]
        )
    )
    assert "tira do ar o site chillers.gabrielfdev.com" in row["alertas"]
    assert run(s.approve(row["id"]))["status"] == "aplicada"
    assert "chillers.gabrielfdev.com" not in host.content and HASH not in host.content


def test_arquivo_mudou_depois_da_proposta(svc):
    s, host, _ = svc
    row = propose_redirect(s, host)
    host.content += "# editado à mão\n"
    with pytest.raises(ChangeError, match="mudou desde a proposta"):
        run(s.approve(row["id"]))
    assert s.db.infra_change(row["id"])["status"] == "obsoleta" and host.writes == []


def test_desfazer_volta_a_versao_anterior(svc):
    s, host, _ = svc
    original = host.content
    row = propose_redirect(s, host)
    run(s.approve(row["id"]))
    assert run(s.undo(row["id"]))["status"] == "desfeita" and host.content == original
    with pytest.raises(ChangeError, match="só dá para desfazer uma mudança aplicada"):
        run(s.undo(row["id"]))


def test_desfazer_recusa_se_o_arquivo_mudou_depois(svc):
    s, host, _ = svc
    row = propose_redirect(s, host)
    run(s.approve(row["id"]))
    host.content += "# outra mudança\n"
    with pytest.raises(ChangeError, match="desfaça antes as mais recentes"):
        run(s.undo(row["id"]))


# ------------------------------------------------------------------ API do Jarvis e painel
TOKEN = "t" * 40
SECRET = totp.new_secret()


@pytest.fixture
def api(config):
    config.jarvis_api_token = TOKEN
    config.admin_totp_secret = SECRET
    host = FakeCaddy()
    app = create_app(
        config,
        runner=FakeRunner(),
        deployer=FakeDeployer(),
        start_background=False,
        caddy_host=host,
        caddy_prober=Prober(),
    )
    app.state.caddy.settle_s = 0
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c, app, host


def code(offset: int = 0) -> str:
    return totp.code_at(SECRET, totp.counter_now() + offset)


def test_api_propor_e_aplicar_exige_codigo_e_confirmacao(api):
    c, app, host = api
    state = c.get("/api/v1/infra/caddy").json()
    assert HASH not in state["conteudo"] and state["gravavel"] is True
    body = {
        "motivo": "redireciona www para o domínio principal",
        "base": state["versao"],
        "edicoes": [
            {"antes": "gabrielfdev.com, www.gabrielfdev.com {", "depois": "gabrielfdev.com {"},
            {"acrescentar": "www.gabrielfdev.com {\n\tredir https://gabrielfdev.com{uri}\n}"},
        ],
    }
    r = c.post("/api/v1/infra/caddy/propostas", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    confirm = app.state.caddy.confirmation(app.state.db.infra_change(pid))
    assert confirm not in r.text  # o Jarvis nunca recebe a confirmação
    title, message = app.state.pipeline.notifier.sent[-1]
    assert title == f"Caddyfile: proposta #{pid}" and f"Confirmação: {confirm}" in message
    assert "Motivo (do Jarvis)" in message and "Linhas alteradas" in message

    url = f"/api/v1/infra/caddy/propostas/{pid}/aprovar"
    r = c.post(url, json={"codigo": code()})
    assert r.status_code == 403 and "confirmação" in r.json()["detail"]
    # confirmação errada não gasta o código TOTP (o mesmo código ainda serve depois)
    assert c.post(url, json={"codigo": "000000", "confirmacao": confirm}).status_code == 403
    assert host.writes == []
    r = c.post(url, json={"codigo": code(), "confirmacao": confirm.lower()})
    assert r.status_code == 200 and r.json()["status"] == "aplicada"
    assert "redir https://gabrielfdev.com{uri}" in host.content
    assert c.post(url, json={"codigo": code(1), "confirmacao": confirm}).status_code == 409  # já aplicada

    undo = f"/api/v1/infra/caddy/propostas/{pid}/desfazer"
    r = c.post(undo, json={"codigo": code(), "confirmacao": confirm})
    assert r.status_code == 403 and "já usado" in r.json()["detail"]  # anti-replay
    r = c.post(undo, json={"codigo": code(1), "confirmacao": confirm})
    assert r.status_code == 200 and r.json()["status"] == "desfeita"


def test_codigo_dado_para_uma_proposta_nao_aplica_outra(api):
    c, app, host = api
    s = app.state.caddy
    shown = propose_redirect(s, host)  # a que o Jarvis mostrou ao Gabriel
    evil = run(s.propose("outra coisa", base(host), edicoes=[{"acrescentar": "x.gabrielfdev.com {\n}"}]))
    confirm_shown = s.confirmation(app.state.db.infra_change(shown["id"]))
    r = c.post(
        f"/api/v1/infra/caddy/propostas/{evil['id']}/aprovar", json={"codigo": code(), "confirmacao": confirm_shown}
    )
    assert r.status_code == 403 and host.writes == []


def test_api_erros_viram_409_e_regras_desligam(api, config):
    c, app, host = api
    r = c.post(
        "/api/v1/infra/caddy/propostas",
        json={
            "motivo": "tira a proteção da API",
            "base": digest(host.content),
            "edicoes": [{"antes": "\trespond @api 404\n", "depois": ""}],
        },
    )
    assert r.status_code == 409 and "proteção da API do Jarvis" in r.json()["detail"]
    config.caddy_proposals = False
    assert c.get("/api/v1/infra/caddy").status_code == 503


def test_painel_mostra_diff_e_aplica_com_codigo(api):
    c, app, host = api
    s = app.state.caddy
    row = propose_redirect(s, host)
    web = TestClient(app)
    web.post("/login", data={"password": "senha-teste"})
    page = web.get("/infra").text
    assert f"#{row['id']}" in page and "redireciona www" in page
    assert s.confirmation(app.state.db.infra_change(row["id"])) in page
    r = web.post(f"/infra/{row['id']}/aprovar", data={"codigo": "123"})
    assert "Código recusado" in r.text and host.writes == []
    r = web.post(f"/infra/{row['id']}/aprovar", data={"codigo": code()})
    assert "aplicada" in r.text and app.state.db.infra_change(row["id"])["status"] == "aplicada"


def test_leitura_de_sites_ignora_curinga_porta_e_comentario():
    text = "# old.gabrielfdev.com {\n:8080 {\n}\n*.gabrielfdev.com {\n}\nhttp://a.gabrielfdev.com:80, b.gabrielfdev.com {\n}\n(snip) {\n}\n"
    assert infra.hosts(text) == ["a.gabrielfdev.com", "b.gabrielfdev.com"]


# ------------------------------------------------------------------ o caminho real (docker exec) com binários falsos
FAKE_DOCKER = r"""#!/usr/bin/env bash
# docker falso: "exec [-i] <container> cmd..." roda cmd aqui mesmo
[[ "$1" == exec ]] || exit 9
shift
[[ "$1" == -i ]] && shift
shift  # container
exec "$@"
"""
FAKE_CADDY = r"""#!/usr/bin/env bash
cmd="$1"; shift
cfg=""; while [[ $# -gt 0 ]]; do [[ "$1" == --config ]] && cfg="$2"; shift; done
if [[ "$cmd" == validate ]]; then grep -q INVALIDO "$cfg" && { echo "Error: INVALIDO"; exit 1; }; echo "Valid configuration"; fi
if [[ "$cmd" == reload ]]; then echo "recarregado $cfg" >> "$(dirname "$cfg")/reloads.log"; fi
exit 0
"""


def test_docker_caddy_host_le_valida_grava_e_recarrega(config, tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in {"docker": FAKE_DOCKER, "caddy": FAKE_CADDY}.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    etc = tmp_path / "etc-caddy"
    etc.mkdir()
    cfg = etc / "Caddyfile"
    cfg.write_text(CADDYFILE)
    config.caddy_config_path = str(cfg)
    host = infra.DockerCaddyHost(config)

    assert run(host.read()) == CADDYFILE and run(host.writable()) is True
    ok, out = run(host.validate(CADDYFILE + "a.gabrielfdev.com {\n}\n"))
    assert ok and "Valid" in out
    ok, out = run(host.validate(CADDYFILE + "a.gabrielfdev.com {\n\tINVALIDO\n}\n"))
    assert not ok and "INVALIDO" in out
    assert sorted(p.name for p in etc.iterdir()) == ["Caddyfile"]  # a cópia de validação não fica para trás
    inode = cfg.stat().st_ino
    run(host.write(CADDYFILE + "# novo\n"))
    assert cfg.read_text().endswith("# novo\n") and cfg.stat().st_ino == inode  # mesmo arquivo (bind mount)
    assert run(host.reload())[0] and "recarregado" in (etc / "reloads.log").read_text()

    cfg.chmod(0o444)
    if __import__("os").geteuid() != 0:  # root escreve mesmo sem permissão; o teste só vale fora do root
        assert run(host.writable()) is False
        with pytest.raises(ChangeError, match="só leitura"):
            run(host.write("x\n"))


def test_segredo_nao_pode_ir_para_onde_o_visitante_ve(svc):
    s, host, _ = svc
    leak = {"acrescentar": "vaza.gabrielfdev.com {\n\trespond «segredo-1»\n}"}
    with pytest.raises(ChangeError, match="copia um segredo"):
        run(s.propose("página de teste", base(host), edicoes=[leak]))
    move = [
        {"antes": "\t\tgabriel «segredo-1»\n", "depois": "\t\tgabriel trocado\n"},
        {"acrescentar": "vaza.gabrielfdev.com {\n\theader X-Debug «segredo-1»\n}"},
    ]
    with pytest.raises(ChangeError, match="diretiva que o visitante vê"):
        run(s.propose("move o segredo de lugar", base(host), edicoes=move))
    fora = [{"antes": "reverse_proxy chillers-velho:8000", "depois": "reverse_proxy https://coletor.exemplo.net"}]
    row = run(s.propose("manda o chillers para outro servidor", base(host), edicoes=fora))
    assert "encaminha tráfego para um endereço fora do VPS (https://coletor.exemplo.net)" in row["alertas"]
