"""Descoberta em rodadas, plano técnico validado, starter e build um ticket por sessão."""

import json

import pytest

from orchestrator.runner import RunResult
from tests.conftest import project_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def advance(pipeline, job_id):
    await pipeline.run_job(job_id)
    return pipeline.db.job(job_id)


async def test_sabatina_em_rodadas_acumula_decisoes(env):
    pipeline, db, runner, *_ = env
    runner.discovery_rounds = [
        [{"id": "q1", "question": "Quem usa?", "why": "login", "default": "só você"}],
        [{"id": "q1", "question": "Precisa exportar CSV?", "why": "escopo", "default": "sim"}],
        [],
    ]
    job = pipeline.new_project("Um painel de consumo de energia do prédio com alertas", "Energia")
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "answers" and job["questions"]["round"] == 1

    await pipeline.submit_answers(job["id"], {"q1": "a equipe de manutenção"})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "answers" and job["message"].startswith("Rodada 2")
    assert [q["id"] for q in job["questions"]["items"]] == ["r2_q1"]  # ids únicos entre rodadas
    round2 = next(c for c in runner.calls if "rodada 2 de" in c.task)
    assert "a equipe de manutenção" in round2.task  # a rodada 2 vê as respostas da 1

    await pipeline.submit_answers(job["id"], {})  # em branco: vale a recomendação
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval", job["message"]
    assert len(job["questions"]["history"]) == 2
    spec_call = next(c for c in runner.calls if "Tarefa B" in c.task)
    assert "a equipe de manutenção" in spec_call.task
    assert "sim (recomendação aceita em silêncio)" in spec_call.task
    events = [e["message"] for e in db.events(job["id"])]
    assert "descoberta concluída (2 rodada(s) de perguntas)" in events


async def test_limite_de_rodadas_vai_direto_para_a_spec(env):
    pipeline, db, runner, *_ = env
    pipeline.c.discovery_rounds = 1
    job = pipeline.new_project("Um encurtador de links com estatísticas por dia", "Links")
    job = await advance(pipeline, job["id"])
    assert "ÚLTIMA rodada" in runner.calls[-1].task
    await pipeline.submit_answers(job["id"], {"q1": "1 ano"})
    assert db.job(job["id"])["phase"] == "spec"


async def test_build_anda_um_ticket_por_sessao_com_starter(env):
    pipeline, db, runner, *_ = env
    runner.plan_tickets = 3
    runner.stack_tags = ["api"]  # sem interface: sem design
    job = pipeline.new_project("Uma API de cotação de frete com regras por região", "Frete")
    job = await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval"
    pdir = project_dir(pipeline, job)
    assert (pdir / ".claude" / "starters" / "fastapi-react.json").exists()  # referência para o planner
    assert not (pdir / "app").exists()  # nada de código antes da aprovação do plano

    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "deploy_approval", job["message"]
    builder_tasks = [c.task for c in runner.calls if c.role == "builder"]
    assert len(builder_tasks) == 3
    assert ["T01" in builder_tasks[0], "T02" in builder_tasks[1], "T03" in builder_tasks[2]] == [True] * 3
    assert "Primeiro ticket: fundação" in builder_tasks[0] and "starter `python-fastapi`" in builder_tasks[0]
    assert "Primeiro ticket" not in builder_tasks[1] and "Tickets já concluídos: T01" in builder_tasks[1]
    # o builder recebe o ticket inteiro (não um resumo) e a definição de pronto
    assert "## Critérios de aceite" in builder_tasks[1] and "## Fora do escopo" in builder_tasks[1]
    assert "Definição de pronto" in builder_tasks[1] and "feat(T02)" in builder_tasks[1]
    tickets = json.loads((pdir / ".harness" / "tickets.json").read_text())["tickets"]
    assert [t["status"] for t in tickets] == ["done", "done", "done"]
    assert (pdir / "app" / "main.py").exists() and (pdir / ".harness" / ".starter-applied").exists()
    assert (pdir / "tests" / "test_api_contract.py").exists()  # manifesto declara OpenAPI
    events = [e["message"] for e in db.events(job["id"])]
    assert any(m.startswith("starter python-fastapi aplicado") for m in events)
    assert "ticket T03 concluído: Fatia 3" in events


def plan_calls(runner):
    return [
        c for c in runner.calls if c.role == "planner" and c.json_schema and "tickets" in c.json_schema["properties"]
    ]


async def test_plano_invalido_para_e_explica(env):
    pipeline, db, runner, *_ = env
    runner.plan_breaks = "manifest"
    job = pipeline.new_project("Um diário de manutenção de chillers com gráficos", "Chillers")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human"
    assert "plano técnico não passou" in job["message"] and "start" in job["message"]
    # antes de chamar você, o planner recebeu os problemas e teve uma chance de corrigir
    calls = plan_calls(runner)
    assert len(calls) == 2
    assert "(nenhum: primeira versão)" in calls[0].task
    assert "foi **reprovada** pela validação" in calls[1].task and "start deve ser" in calls[1].task


async def test_ticket_raso_volta_para_o_planner_e_ele_corrige(env):
    pipeline, db, runner, *_ = env
    runner.plan_breaks = "vago"
    runner.plan_fixes_on_retry = True
    job = pipeline.new_project("Um diário de manutenção de chillers com gráficos", "Chillers")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval", job["message"]
    calls = plan_calls(runner)
    assert len(calls) == 2
    assert "ticket T01: falta a seção '## Critérios de aceite'" in calls[1].task
    assert any("plano reprovado na validação" in e["message"] for e in db.events(job["id"]))


async def test_ticket_raso_que_continua_raso_chama_voce(env):
    pipeline, db, runner, *_ = env
    runner.plan_breaks = "vago"
    job = pipeline.new_project("Um diário de manutenção de chillers com gráficos", "Chillers")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human"
    assert "ticket T01: raso" in job["message"]
    assert len(plan_calls(runner)) == 2


async def test_features_sem_ticket_reprovam_o_plano(env):
    pipeline, db, runner, *_ = env
    runner.plan_breaks = "tickets"
    job = pipeline.new_project("Um diário de manutenção de chillers com gráficos", "Chillers")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human" and "features sem ticket: F01" in job["message"]


async def test_ticket_que_nao_fecha_e_bloqueado_depois_de_duas_sessoes(env, monkeypatch):
    pipeline, db, runner, *_ = env
    runner.plan_tickets = 2
    runner.stack_tags = ["api"]
    job = pipeline.new_project("Uma API de cotação de frete com regras por região", "Frete")
    await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    await advance(pipeline, job["id"])
    pipeline.approve_spec(job["id"])

    real_run = runner.run
    failures = {"n": 0}

    async def flaky(spec):
        if spec.role == "builder" and "T01" in spec.task and failures["n"] < 2:
            failures["n"] += 1
            return RunResult(ok=False, error="max turns", turns=250)
        return await real_run(spec)

    monkeypatch.setattr(runner, "run", flaky)
    job = await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, job)
    tickets = {t["id"]: t for t in json.loads((pdir / ".harness" / "tickets.json").read_text())["tickets"]}
    assert tickets["T01"]["status"] == "blocked" and tickets["T01"]["attempts"] == 2
    assert tickets["T02"]["status"] == "done"
    assert any("T01 não fechou em 2 sessões" in e["message"] for e in db.events(job["id"]))
