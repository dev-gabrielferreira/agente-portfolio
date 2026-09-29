import json

import pytest

from orchestrator.config import Config
from orchestrator.runner import ClaudeRunner, RunSpec, describe_event

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


FAKE_CLAUDE = """#!/usr/bin/env bash
# imita `claude -p --output-format stream-json`
echo '{"type":"system","subtype":"init","model":"claude-opus-5-5","session_id":"s1"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"Lendo a tarefa"},{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]}}'
echo "role=$HARNESS_ROLE token=${GITHUB_TOKEN:-none}" >&2
echo '{"type":"result","subtype":"success","is_error":false,"num_turns":3,"total_cost_usd":0.42,"session_id":"s1","result":"feito","structured_output":{"verdict":"PASS"}}'
"""


def make_runner(tmp_path, script):
    fake = tmp_path / "claude"
    fake.write_text(script)
    fake.chmod(0o755)
    c = Config()
    c.claude_bin, c.run_as, c.base_dir = str(fake), "", tmp_path
    return ClaudeRunner(c)


async def test_executa_e_interpreta_stream_json(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "segredo")
    runner = make_runner(tmp_path, FAKE_CLAUDE)
    proj = tmp_path / "p"
    proj.mkdir()
    spec = RunSpec(
        role="evaluator",
        project_dir=proj,
        task="# avalie",
        log_path=tmp_path / "job.log",
        agent="evaluator",
        json_schema={"type": "object"},
    )
    r = await runner.run(spec)
    assert r.ok and r.structured == {"verdict": "PASS"} and r.cost_usd == 0.42 and r.turns == 3
    assert (proj / ".harness" / "TASK.md").read_text() == "# avalie"
    log = (tmp_path / "job.log").read_text()
    assert "🔧 Bash: pytest -q" in log and "Lendo a tarefa" in log


def test_ambiente_do_agente_nao_vaza_segredos(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "segredo")
    monkeypatch.setenv("ADMIN_PASSWORD", "segredo")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    runner = make_runner(tmp_path, FAKE_CLAUDE)
    env = runner.build_env(RunSpec(role="builder", project_dir=tmp_path, task="", log_path=tmp_path / "l"))
    assert "GITHUB_TOKEN" not in env and "ADMIN_PASSWORD" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth" and env["HARNESS_ROLE"] == "builder"


def test_comando_usa_opus_e_modo_headless(tmp_path):
    runner = make_runner(tmp_path, FAKE_CLAUDE)
    cmd = runner.build_command(
        RunSpec(
            role="evaluator",
            project_dir=tmp_path,
            task="",
            log_path=tmp_path / "l",
            agent="evaluator",
            json_schema={"type": "object"},
            disallowed_tools=["Write", "Edit"],
        )
    )
    joined = " ".join(cmd)
    assert "--model claude-opus-5-5" in joined and "-p" in cmd
    assert "--agent evaluator" in joined and "--disallowedTools Write,Edit" in joined
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}


async def test_detecta_limite_de_uso(tmp_path):
    script = '#!/usr/bin/env bash\necho \'{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Claude usage limit reached"}\'\n'
    runner = make_runner(tmp_path, script)
    r = await runner.run(RunSpec(role="builder", project_dir=tmp_path, task="", log_path=tmp_path / "l"))
    assert not r.ok and r.rate_limited


def test_describe_event_resultado():
    lines = describe_event(
        {"type": "result", "subtype": "success", "num_turns": 2, "total_cost_usd": 1.0, "is_error": False}
    )
    assert lines and "2 turnos" in lines[0]


async def test_limite_de_sessao_real_do_claude_code(tmp_path):
    """Mensagem real observada durante a construção do agente."""
    script = (
        '#!/usr/bin/env bash\necho \'{"type":"result","subtype":"success","is_error":true,"num_turns":49,'
        '"result":"You\'"\'"\'ve hit your session limit · resets 11:40pm (UTC)"}\'\n'
    )
    runner = make_runner(tmp_path, script)
    r = await runner.run(RunSpec(role="builder", project_dir=tmp_path, task="", log_path=tmp_path / "l"))
    assert not r.ok and r.rate_limited
    assert r.retry_at is not None and r.retry_at.hour in (23, 0) and r.retry_at.tzinfo is not None


def test_parse_reset():
    from datetime import UTC, datetime

    from orchestrator.runner import parse_reset

    now = datetime(2026, 9, 24, 22, 45, tzinfo=UTC)
    assert parse_reset("resets 11:40pm (UTC)", now) == datetime(2026, 9, 24, 23, 42, tzinfo=UTC)
    assert parse_reset("resets 9am (UTC)", now) == datetime(2026, 9, 25, 9, 2, tzinfo=UTC)
    assert parse_reset("resets 7pm (America/Sao_Paulo)", now) == datetime(2026, 9, 25, 22, 2, tzinfo=UTC)
    assert parse_reset("sem horário", now) is None


def test_conserta_campos_embutidos_numa_string_e_escolhe_a_tentativa_completa():
    from orchestrator import schemas
    from orchestrator.runner import pick_structured, repair_structured

    broken = {
        "project_name": "Termia",
        "understanding": 'App de chillers.</understanding>\n<questions>[{"id": "escopo", "question": "Um chiller ou vários?", "why": "modelo de dados", "default": "vários"}]',
    }
    fixed = repair_structured(broken, schemas.QUESTIONS)
    assert fixed["understanding"] == "App de chillers."
    assert fixed["questions"][0]["id"] == "escopo" and fixed["project_name"] == "Termia"
    fallback = {"understanding": "teste", "questions": [{"id": "q1", "question": "teste?", "why": "teste"}]}
    assert pick_structured([fixed, fallback], schemas.QUESTIONS) is fixed  # o objeto mínimo de "fuga" perde
    assert pick_structured([broken, fallback], schemas.QUESTIONS) is fallback  # quebrado não valida
    assert repair_structured({"understanding": "sem tags"}, schemas.QUESTIONS) == {"understanding": "sem tags"}
    closed = repair_structured(
        {"understanding": "x</understanding><one_liner>Linha</one_liner><lixo>ignorado</lixo>", "questions": []},
        schemas.QUESTIONS,
    )
    assert closed["one_liner"] == "Linha" and "lixo" not in closed and closed["questions"] == []
