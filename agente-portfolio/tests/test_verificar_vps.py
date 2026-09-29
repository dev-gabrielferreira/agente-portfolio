"""scripts/verificar-vps.sh com um docker falso: caminho feliz e cada problema apontado com a correção."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason="o script exige root (sudo), como no VPS")

FAKE_DOCKER = r"""#!/usr/bin/env bash
# docker falso para exercitar o verificar-vps.sh; MODO=ruim simula problemas
all="$*"
bad() { [[ "${MODO:-}" == ruim ]]; }
case "$all" in
  "--version") echo "Docker version 27.3.1, build ce12230";;
  "compose version") echo "Docker Compose version v2.29";;
  "network inspect"*) bad && [[ "$all" == *agente-painel* ]] && exit 1; exit 0;;
  "inspect -f {{.State.Running}}"*) echo true;;
  *"NetworkSettings.Networks"*) bad && echo "interna " || echo "interna agente-painel ";;
  *".Mounts"*) echo "${AGENTE_SRV:-/srv/agente}/caddy-sites /opt/caddy/Caddyfile /opt/caddy/data ";;
  *".Config.Env"*) printf 'PATH=/usr/bin\nJARVIS_API_TOKEN=abc\nLAB_CLAUDE_CODE_OAUTH_TOKEN=x\n'; bad && echo "CLAUDE_CODE_OAUTH_TOKEN=y"; true;;
  "exec agente curl"*) exit 0;;
  "exec agente runuser -u agent -- docker ps") bad && exit 0; exit 1;;
  "exec agente claude --version") echo "2.1.284 (Claude Code)";;
  "exec agente runuser -u agent -- claude -p"*) bad && echo '{"is_error":true,"result":"Invalid API key"}' || echo '{"type":"result","is_error":false,"result":"ok"}';;
  "exec caddy cat"*) cat <<'CF'
{
	email gabriel@example.com
}
(comum) {
	encode zstd gzip
}
gabrielfdev.com, www.gabrielfdev.com {
	root * /srv/site
	file_server
}
agent.gabrielfdev.com {
	@api path /api/*
	respond @api 404
	reverse_proxy agente:8080
}
chillers.gabrielfdev.com {
	reverse_proxy chillers:8000
}
CF
    bad && printf 'energia.gabrielfdev.com {\n\treverse_proxy velho:80\n}\n'
    echo "import /etc/caddy/sites-agente/*.caddy";;
  "exec agente docker exec caddy caddy validate"*) bad && exit 1; exit 0;;
  "exec caddy sh -c test -w"*) bad && exit 1; exit 0;;
  *"python3 -c"*) if [[ "$all" == *"-u lab"* ]]; then echo "nao desconhecido"; else bad && echo "sim oauth_token" || echo "sim claude.ai"; fi;;
  "exec -u jarvis jarvis tmux has-session"*) exit 0;;
  "exec -u jarvis jarvis tmux list-windows"*) printf '_\nloja\n';;
  "exec -u jarvis jarvis tmux capture-pane"*) bad && echo "Enable Remote Control? (y/n)" || echo "  ? for shortcuts        /rc active";;
  "exec jarvis test -S /run/jarvis/broker.sock") exit 0;;
  "exec jarvis test -S /var/run/docker.sock") exit 1;;
  "exec -u jarvis jarvis sh -c"*) bad && echo 401 || echo 200;;
  "exec -u jarvis jarvis touch"*) exit 1;;
  "exec -u lab jarvis ls"*) exit 1;;
  "ps -a --filter"*) printf 'energia-production\tUp 3 days\nenergia-staging\tUp 3 days\n'; bad && printf 'frete-production\tExited (1) 2 hours ago\n'; true;;
  *) echo "docker falso: não sei '$all'" >&2; exit 3;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
url="${!#}"
case "$url" in
  */api/v1/*) [[ "${MODO:-}" == ruim ]] && echo -n 200 || echo -n 404;;
  *energia-staging*) echo -n 401;;
  *) echo -n 200;;
esac
"""

FAKE_GETENT = r"""#!/usr/bin/env bash
[[ "${MODO:-}" == ruim && "$2" == verificacao-* ]] && exit 2
echo "203.0.113.10    STREAM $2"
"""

ENV = """CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-xxx
ANTHROPIC_API_KEY=
AGENT_MODEL=claude-opus-5-5
PORTFOLIO_DOMAIN=gabrielfdev.com
GITHUB_TOKEN=ghp_x
JARVIS_API_TOKEN=abc
ADMIN_TOTP_SECRET=JBSWY3DPEHPK3PXP
JARVIS_MODEL=claude-opus-5-5
"""


def run(tmp_path, modo="", *args):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "verificar-vps.sh", repo / "scripts")
    shutil.copy(ROOT / "docker-compose.yml", repo)
    (repo / ".env").write_text(ENV)
    (repo / ".env").chmod(0o600)
    srv = tmp_path / "srv"
    (srv / "projects").mkdir(parents=True, exist_ok=True)
    (srv / "caddy-sites").mkdir(exist_ok=True)
    (srv / "caddy-sites" / "energia-production.caddy").write_text("energia.gabrielfdev.com {\n}\n")
    (srv / "caddy-sites" / "00-agente.caddy").write_text("# sites gerados pelo agente entram nesta pasta\n")
    (srv / "caddy-sites" / "energia-staging.caddy").write_text("energia-staging.gabrielfdev.com {\n}\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in {"docker": FAKE_DOCKER, "curl": FAKE_CURL, "getent": FAKE_GETENT}.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "AGENTE_SRV": str(srv), "MODO": modo}
    return subprocess.run(
        ["bash", str(repo / "scripts" / "verificar-vps.sh"), *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_tudo_certo_sai_com_zero_e_lista_o_que_e_seu(tmp_path):
    out = run(tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "0 falha(s)" in out.stdout and "✖" not in out.stdout
    assert "https://#" not in out.stdout  # arquivo só com comentário não vira site
    assert "worker chamou o claude-opus-5-5 pela assinatura" in out.stdout
    assert "Remote Control ativo" in out.stdout
    assert "Caddyfile principal gravável" in out.stdout
    assert "conversas por projeto no app: loja" in out.stdout
    # sites do Caddyfile principal ficam com o Gabriel; o agente só administra os dele
    assert "- chillers.gabrielfdev.com" in out.stdout and "- agent.gabrielfdev.com" not in out.stdout


def test_problemas_viram_falhas_com_a_correcao(tmp_path):
    out = run(tmp_path, "ruim", "--rapido")
    assert out.returncode == 1
    for trecho in (
        "consegue usar o Docker",
        "Caddy fora da rede agente-painel",
        "domínio energia.gabrielfdev.com está no seu Caddyfile E nos sites do agente",
        "a API respondeu '200' pela internet",
        "remova do container jarvis: CLAUDE_CODE_OAUTH_TOKEN",
        "o Remote Control exige o login completo",
        "responder 'Enable Remote Control?'",
        "frete-production: Exited",
        "Caddyfile principal só leitura",
        "a conversa do projeto loja espera o 'Enable Remote Control?'",
    ):
        assert trecho in out.stdout, trecho
    assert "pulei a chamada ao modelo" in out.stdout
