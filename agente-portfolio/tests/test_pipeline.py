import pytest

from tests.conftest import failing_eval, passing_eval, project_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def advance(pipeline, job_id):
    await pipeline.run_job(job_id)
    return pipeline.db.job(job_id)


async def test_novo_projeto_do_pedido_ate_producao(env):
    pipeline, db, runner, deployer, publisher, notifier = env
    runner.eval_queue = [failing_eval(), passing_eval()]
    job = pipeline.new_project("Quero um painel com a carga de energia do SIN usando dados da ONS", "Energia")

    job = await advance(pipeline, job["id"])
    assert (job["status"], job["waiting_for"]) == ("waiting", "answers")
    assert job["questions"]["items"][0]["id"] == "q1"
    pdir = project_dir(pipeline, job)
    assert (pdir / ".claude" / "hooks" / "guard.sh").exists()
    assert (pdir / ".claude" / "rules" / "lessons.md").exists()
    assert (pdir / "scripts" / "test_quality.py").exists() and (pdir / "tests" / "e2e" / "conftest.py").exists()

    await pipeline.submit_answers(job["id"], {"q1": "5 anos"}, "Painel ONS", "painel-ons")
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "spec_approval"
    pdir = project_dir(pipeline, job)
    assert pdir.name == "painel-ons" and (pdir / "SPEC.md").exists()
    spec_call = next(c for c in runner.calls if "Tarefa B" in c.task)
    assert "5 anos" in spec_call.task
    plan_call = runner.calls[-1]
    assert "Tarefa C" in plan_call.task and "`python-fastapi`" in plan_call.task and "`fastapi-react`" in plan_call.task
    assert job["spec_summary"]["plan"]["starter"] == "python-fastapi"
    assert db.project(job["project_id"])["tags"] == ["ui"]
    # tags da spec → skills de interface instaladas e preload do designer renderizado
    assert (pdir / ".claude" / "skills" / "design-system" / "SKILL.md").exists()
    assert "skills: frontend, design-system" in (pdir / ".claude" / "agents" / "designer.md").read_text()

    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "design_approval", job["message"]
    assert job["design_report"]["concept"] == "sala de controle"
    design_call = next(c for c in runner.calls if c.role == "designer")
    assert design_call.agent == "designer" and "design-system" in design_call.task
    assert "mcp__figma" in design_call.disallowed_tools  # Figma desabilitado neste teste

    pipeline.approve_design(job["id"])
    job = await advance(pipeline, job["id"])
    # build → gates → test-engineer (1ª rodada) → gates → staging → avaliador reprova → build → … → review
    assert job["waiting_for"] == "deploy_approval", job["message"]
    assert job["fix_round"] == 1 and job["tests_round"] == 1
    assert job["mutation_score"] == 90
    roles = [c.role for c in runner.calls]
    assert roles.count("builder") == 2 and roles.count("evaluator") == 2 and roles.count("tester") == 1
    assert roles.count("security") == 1 and roles.count("reviewer") == 1
    tester_call = next(c for c in runner.calls if c.role == "tester")
    assert tester_call.agent == "test-engineer" and "testes-que-importam" in tester_call.task
    assert (pdir / "tests" / "acceptance" / "test_f01.py").exists()
    fix_task = [c for c in runner.calls if c.role == "builder"][1].task
    assert "NEXT_FINDINGS" in fix_task and "test-engineer" in fix_task
    reviewer_call = next(c for c in runner.calls if c.role == "reviewer")
    assert "Write" in reviewer_call.disallowed_tools
    assert ("up", "painel-ons", "staging", db.project(job["project_id"])["staging_tag"]) in deployer.calls
    assert "Aprovar deploy" in notifier.sent[-1][0] and "mutation 90" in notifier.sent[-1][1]

    pipeline.approve_deploy(job["id"])
    job = await advance(pipeline, job["id"])
    project = db.project(job["project_id"])
    assert job["status"] == "done"
    assert project["status"] == "live" and project["prod_tag"] == project["staging_tag"]
    assert publisher.published == [("painel-ons", project["prod_tag"])]
    assert db.lessons("proposed")[0]["kind"] == "check"
    assert job["cost_usd"] > 0


async def to_build(pipeline, job):
    """Leva um job 'create' até depois das aprovações de spec e design."""
    job = await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    if job["waiting_for"] == "design_approval":
        pipeline.approve_design(job["id"])
    return await advance(pipeline, job["id"])


async def test_bug_achado_pelo_test_engineer_volta_para_o_builder(env):
    pipeline, db, runner, *_ = env
    runner.tester_bugs = [
        [
            {
                "feature": "F01",
                "test": "tests/acceptance/test_f01.py::test_f01_criterio_de_aceite",
                "observed": "500 com total zero",
                "expected": "422",
                "severity": "blocker",
            }
        ]
    ]
    job = pipeline.new_project("API de preços com descontos por faixa e cliente VIP para lojas", "Preços")
    job = await to_build(pipeline, job)
    assert job["waiting_for"] == "deploy_approval", job["message"]
    builder_tasks = [c.task for c in runner.calls if c.role == "builder"]
    assert len(builder_tasks) == 2 and "Bugs encontrados pelo test-engineer" not in builder_tasks[0]
    events = [e["message"] for e in db.events(job["id"])]
    assert any("1 bug(s)" in m for m in events)


async def test_testes_fracos_do_test_engineer_voltam_para_ele(env):
    pipeline, db, runner, *_ = env
    runner.tester_writes_weak = True
    job = pipeline.new_project("Cadastro de equipamentos de HVAC com histórico de manutenção", "HVAC")
    job = await to_build(pipeline, job)
    # os testes só verificam status 200 → o sensor devolve ao test-engineer até o limite
    assert job["waiting_for"] == "human" and "rodadas de teste" in job["message"]
    assert job["tests_round"] == pipeline.c.max_test_rounds
    tester_tasks = [c.task for c in runner.calls if c.role == "tester"]
    assert "so-verificacao-fraca" in tester_tasks[1]


async def test_mutation_score_baixo_devolve_ao_test_engineer(env):
    pipeline, db, runner, *_ = env
    job = pipeline.new_project("Calculadora de consumo de energia de chillers por período", "Chiller kWh")
    job = await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, job)
    (pdir / ".mutation-score").write_text("40")
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    pipeline.approve_design(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human"
    tester_tasks = [c.task for c in runner.calls if c.role == "tester"]
    assert any("mutation score 40" in t for t in tester_tasks)
    assert any("mutmut show" in t for t in tester_tasks)


async def test_disputa_do_builder_e_julgada_pelo_test_engineer(env):
    pipeline, db, runner, *_ = env
    job = pipeline.new_project("Painel de alarmes do BMS com reconhecimento e histórico", "Alarmes")
    job = await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, job)
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    # builder discorda de um teste e o gate está vermelho
    (pdir / ".harness" / "TEST_DISPUTES.md").write_text("test_f01 exige 422, a SPEC diz 400")
    (pdir / ".fail-check").write_text("x")
    runner.builder_fixes_gate = False
    pipeline.approve_design(job["id"])
    await pipeline.run_job(job["id"])
    assert not (pdir / ".harness" / "TEST_DISPUTES.md").exists()
    resolved = (pdir / ".harness" / "TEST_DISPUTES_RESOLVED.md").read_text()
    assert "a SPEC diz 400" in resolved and "kept" in resolved
    assert db.job(job["id"])["test_report"]["disputes"][0]["verdict"] == "kept"


async def test_bloqueante_da_revisao_de_codigo_impede_producao(env):
    pipeline, db, runner, *_ = env
    runner.review_blockers = True
    job = pipeline.new_project("Relatório mensal de disponibilidade de CCTV por prédio", "CCTV")
    job = await to_build(pipeline, job)
    assert job["waiting_for"] == "deploy_approval"
    assert job["fix_round"] == 1
    assert any("código 1 bloqueante" in e["message"] for e in db.events(job["id"]))


async def test_gate_vermelho_volta_para_build_com_saida_do_teste(env):
    pipeline, db, runner, *_ = env
    runner.stack_tags = ["api"]  # sem interface: não passa pelo design
    job = pipeline.new_project("Um sistema de ordens de manutenção para HVAC com histórico", "CMMS")
    job = await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, job)
    (pdir / ".fail-check").write_text("x")
    runner.builder_fixes_gate = False
    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "human"
    assert "Limite de" in job["message"]
    findings = (pdir / ".harness" / "NEXT_FINDINGS.md").read_text()
    assert "AssertionError" in findings
    # você orienta, o agente corrige e o fluxo segue
    runner.builder_fixes_gate = True
    await pipeline.request_changes(job["id"], "O teste falha porque a fixture de data está errada.")
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "deploy_approval"


async def test_bloqueante_de_seguranca_impede_producao(env):
    pipeline, db, runner, *_ = env
    runner.security_blockers = True
    job = pipeline.new_project("API de consulta de chamados com filtro por prédio e prioridade", "Chamados")
    job = await to_build(pipeline, job)
    assert job["waiting_for"] == "deploy_approval"
    assert job["fix_round"] == 1
    assert any("segurança 1 bloqueante" in e["message"] for e in db.events(job["id"]))


async def test_falha_em_producao_faz_rollback_automatico(env):
    pipeline, db, runner, deployer, *_ = env
    job = pipeline.new_project("Portal de documentação técnica de equipamentos prediais", "Docs")
    job = await to_build(pipeline, job)
    pipeline.approve_deploy(job["id"])
    job = await advance(pipeline, job["id"])
    project = db.project(job["project_id"])
    v1 = project["prod_tag"]
    assert job["status"] == "done" and v1

    change = pipeline.new_job(project["id"], "change", "Adicionar busca por fabricante")
    change = await advance(pipeline, change["id"])  # o fake sempre pergunta
    await pipeline.submit_answers(change["id"], {})
    change = await advance(pipeline, change["id"])
    if change["waiting_for"] == "design_approval":
        pipeline.approve_design(change["id"])
        change = await advance(pipeline, change["id"])
    assert change["waiting_for"] == "deploy_approval", change["message"]
    deployer.fail_health[f"{project['slug']}:production"] = 1
    pipeline.approve_deploy(change["id"])
    change = await advance(pipeline, change["id"])
    assert change["waiting_for"] == "human" and "Rollback automático" in change["message"]
    assert deployer.running[f"{project['slug']}:production"] == v1
    assert db.project(project["id"])["prod_tag"] == v1


async def test_limite_de_uso_reagenda_o_job(env):
    pipeline, db, runner, *_ = env
    runner.rate_limit_next = True
    job = pipeline.new_project("Dashboard de temperatura de água gelada do chiller", "Chiller")
    job = await advance(pipeline, job["id"])
    assert job["status"] == "queued" and job["not_before"]
    assert db.next_runnable_job() is None


async def test_pausa_e_retomada(env):
    pipeline, db, runner, *_ = env
    job = pipeline.new_project("Sistema de inventário de sensores BACnet com importação CSV", "Inventário")
    await pipeline.pause(job["id"])
    job = db.job(job["id"])
    assert job["status"] == "waiting" and job["waiting_for"] == "human"
    assert (project_dir(pipeline, job).parent).exists()
    await pipeline.retry(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "answers"
    assert not (project_dir(pipeline, job) / "AGENT_STOP").exists()


async def test_licao_aceita_entra_no_harness_dos_projetos(env):
    pipeline, db, *_ = env
    db.add_lesson(None, "Valide datas no fuso America/Sao_Paulo", "rule", "bug de fuso")
    lesson = db.lessons("proposed")[0]
    pipeline.accept_lesson(lesson["id"])
    assert "America/Sao_Paulo" in pipeline.c.lessons_file.read_text()
    job = pipeline.new_project("Agenda de manutenção preventiva com lembretes por e-mail", "Agenda")
    await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, db.job(job["id"]))
    assert "America/Sao_Paulo" in (pdir / ".claude" / "rules" / "lessons.md").read_text()


async def test_falha_de_infra_no_staging_chama_voce_sem_gastar_rodada(env):
    from orchestrator.deployer import DeployError

    pipeline, db, runner, deployer, *_ = env

    async def broken_up(slug, env_name, tag):
        raise DeployError("reload do Caddy falhou", "erro de config", infra=True)

    deployer.up = broken_up
    job = pipeline.new_project("Catálogo de pontos BACnet com busca e exportação para CSV", "Pontos")
    job = await to_build(pipeline, job)
    assert job["waiting_for"] == "human" and "infraestrutura" in job["message"]
    assert job["fix_round"] == 0


async def test_worker_pega_job_criado_por_outra_thread(env):
    import asyncio

    pipeline, db, *_ = env
    task = asyncio.create_task(pipeline.worker())
    await asyncio.sleep(0.05)
    job = await asyncio.to_thread(
        pipeline.new_project, "Relatório de consumo de energia por andar com gráficos", "Consumo"
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        if db.job(job["id"])["status"] == "waiting":
            break
    task.cancel()
    assert db.job(job["id"])["waiting_for"] == "answers"
