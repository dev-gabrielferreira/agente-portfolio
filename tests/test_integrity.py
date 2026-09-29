"""Sensor de integridade: escrita fora da área do papel por QUALQUER caminho é desfeita."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator import integrity
from orchestrator.config import REPO_DIR
from orchestrator.workspace import Workspace
from tests.test_pipeline import advance, project_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, config):
    r = tmp_path / "proj"
    for rel, text in {
        "app/main.py": "APP = 1\n",
        "app/services/preco.py": "def total(x):\n    return x\n",
        "tests/acceptance/test_f01.py": "def test_f01():\n    assert 1 + 1 == 2\n",
        "tests/unit/test_u.py": "def test_u():\n    assert 2 == 2\n",
        "tests/conftest.py": "",
        "scripts/check.sh": "#!/bin/sh\necho gate\n",
        "design/DESIGN.md": "# design\n",
        "SPEC.md": "# spec\n",
        ".gitignore": ".venv/\n",
    }.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    git(r, "init", "-q")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return r, Workspace(config)


async def test_builder_nao_consegue_mexer_em_teste_alheio_nem_gate_pelo_shell(repo):
    r, ws = repo
    pre, base = await integrity.snapshot(ws, r)
    # o que um `sed -i`, `cat >` ou `git checkout` fariam
    (r / "tests/acceptance/test_f01.py").write_text("def test_f01():\n    assert True\n")
    (r / "scripts/check.sh").write_text("#!/bin/sh\nexit 0\n")
    (r / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests/acceptance\n")
    (r / "design/DESIGN.md").unlink()
    (r / "app/main.py").write_text("APP = 2\n")  # código é do builder: fica
    (r / "tests/unit/test_novo.py").write_text("def test_n():\n    assert 3 == 3\n")  # teste dele: fica
    violations = await integrity.enforce(ws, r, "builder", pre, base)
    assert {v.path for v in violations} == {
        "tests/acceptance/test_f01.py",
        "scripts/check.sh",
        "pytest.ini",
        "design/DESIGN.md",
    }
    assert "1 + 1 == 2" in (r / "tests/acceptance/test_f01.py").read_text()
    assert (r / "scripts/check.sh").read_text() == "#!/bin/sh\necho gate\n"
    assert not (r / "pytest.ini").exists() and (r / "design/DESIGN.md").exists()
    assert (r / "app/main.py").read_text() == "APP = 2\n" and (r / "tests/unit/test_novo.py").exists()


async def test_arquivo_escondido_pelo_gitignore_tambem_conta(repo):
    r, ws = repo
    pre, base = await integrity.snapshot(ws, r)
    (r / ".gitignore").write_text(".venv/\ntests/acceptance/conftest.py\n")
    (r / "tests/acceptance/conftest.py").write_text("collect_ignore = ['test_f01.py']\n")
    violations = await integrity.enforce(ws, r, "builder", pre, base)
    assert [v.path for v in violations] == ["tests/acceptance/conftest.py"]
    assert not (r / "tests/acceptance/conftest.py").exists()


async def test_test_engineer_nao_corrige_o_codigo_da_aplicacao(repo):
    r, ws = repo
    pre, base = await integrity.snapshot(ws, r)
    (r / "app/services/preco.py").write_text("def total(x):\n    return max(x, 0)\n")
    (r / "tests/acceptance/test_f02.py").write_text("def test_f02():\n    assert 0 == 0\n")
    (r / "tests/unit/test_u.py").write_text("")
    violations = await integrity.enforce(ws, r, "tester", pre, base)
    assert {v.path for v in violations} == {"app/services/preco.py", "tests/unit/test_u.py"}
    assert "max" not in (r / "app/services/preco.py").read_text()
    assert (r / "tests/acceptance/test_f02.py").exists()


async def test_revisores_so_leem(repo):
    r, ws = repo
    pre, base = await integrity.snapshot(ws, r)
    (r / "app/main.py").write_text("APP = 99\n")
    (r / ".harness").mkdir()
    (r / ".harness/nota.md").write_text("ok")
    violations = await integrity.enforce(ws, r, "evaluator", pre, base)
    assert [v.path for v in violations] == ["app/main.py"] and (r / ".harness/nota.md").exists()


async def test_alteracao_anterior_a_sessao_nao_e_atribuida_ao_agente(repo):
    r, ws = repo
    (r / "app/main.py").write_text("APP = 'editado pelo Gabriel'\n")  # sujo antes da sessão
    pre, base = await integrity.snapshot(ws, r)
    assert await integrity.enforce(ws, r, "designer", pre, base) == []
    assert "Gabriel" in (r / "app/main.py").read_text()
    (r / "app/main.py").write_text("APP = 'designer mexeu'\n")
    violations = await integrity.enforce(ws, r, "designer", pre, base)
    assert [v.path for v in violations] == ["app/main.py"]
    assert "Gabriel" in (r / "app/main.py").read_text()  # volta ao estado de antes da sessão, não ao commit


def test_gates_sao_restaurados_do_harness(tmp_path):
    template = REPO_DIR / "harness" / "project-template"
    proj = tmp_path / "p"
    (proj / "scripts").mkdir(parents=True)
    for gate in integrity.GATE_SCRIPTS:
        (proj / gate).write_bytes((template / gate).read_bytes())
    assert integrity.restore_gates(template, proj) == []
    (proj / "scripts/mutation.sh").write_text("#!/bin/sh\necho '{\"score\": 100}'\n")
    assert integrity.restore_gates(template, proj) == ["scripts/mutation.sh"]
    assert (proj / "scripts/mutation.sh").read_bytes() == (template / "scripts/mutation.sh").read_bytes()


def test_configuracao_que_desligaria_testes(tmp_path):
    p = tmp_path
    (p / "app/services").mkdir(parents=True)
    (p / "app/services/preco.py").write_text("X = 1\n")
    (p / "tests/unit").mkdir(parents=True)
    (p / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = \"-k 'not f01'\"\n"
        '[tool.mutmut]\nsource_paths = ["app/utils"]\npytest_add_cli_args = ["-m", "not feature"]\n'
        'do_not_mutate = ["app/services/*"]\n'
    )
    (p / "tox.ini").write_text("[pytest]\n")
    (p / "tests/unit/conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
    problems = "\n".join(integrity.config_problems(p))
    for expected in (
        "tox.ini",
        "addopts",
        "source_paths",
        "do_not_mutate",
        "pytest_add_cli_args",
        "tests/unit/conftest.py",
    ):
        assert expected in problems, expected


@pytest.mark.parametrize("starter", ["python-fastapi", "fastapi-react"])
def test_template_mais_starter_e_valido(tmp_path, starter):
    from orchestrator import stack

    proj = tmp_path / "p"
    shutil.copytree(REPO_DIR / "harness" / "project-template", proj)
    shutil.copy2(REPO_DIR / "harness" / "starters" / starter / "stack.json", proj / ".harness" / "stack.json")
    assert stack.apply_starter(proj, REPO_DIR / "harness")
    assert stack.validate(stack.load(proj), set(stack.starters(REPO_DIR / "harness"))) == []
    assert integrity.config_problems(proj) == []
    assert (proj / ".harness" / ".starter-applied").read_text().strip() == starter
    assert stack.apply_starter(proj, REPO_DIR / "harness") == []  # uma vez só


def test_comando_do_frontend_que_sempre_passa_e_reprovado(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(REPO_DIR / "harness" / "project-template", proj)
    shutil.copy2(REPO_DIR / "harness" / "starters" / "fastapi-react" / "stack.json", proj / ".harness" / "stack.json")
    from orchestrator import stack

    stack.apply_starter(proj, REPO_DIR / "harness")
    data = stack.load(proj)
    data["frontend"]["test"] = "npm test || true"
    (proj / ".harness" / "stack.json").write_text(json.dumps(data))
    assert any("frontend.test" in p for p in integrity.config_problems(proj))
    data["frontend"]["test"] = "npm test"
    data["backend"]["domain"] = "app/regras"
    (proj / ".harness" / "stack.json").write_text(json.dumps(data))
    (proj / "app" / "regras").mkdir()
    (proj / "app" / "regras" / "tarifa.py").write_text("def x():\n    return 1\n")
    assert any("app/regras" in p for p in integrity.config_problems(proj))


def test_teste_do_test_engineer_que_nao_rodou_e_detectado(tmp_path):
    p = tmp_path
    (p / "tests/acceptance").mkdir(parents=True)
    (p / "tests/acceptance/test_f01.py").write_text(
        "import pytest\n\n"
        "def test_a():\n    assert 1\n\n"
        "@pytest.mark.parametrize('x', [1, 2])\ndef test_b(x):\n    assert x\n\n"
        "def test_c():\n    assert 1\n\n"
        "@pytest.mark.skip(reason='API externa fora do ar')\ndef test_d():\n    assert 1\n\n"
        "class TestE:\n    def test_e(self):\n        assert 1\n"
    )
    (p / ".harness/evidence").mkdir(parents=True)
    (p / ".harness/evidence/junit.xml").write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.acceptance.test_f01" name="test_a"/>'
        '<testcase classname="tests.acceptance.test_f01" name="test_b[1]"/>'
        '<testcase classname="tests.acceptance.test_f01" name="test_c"><skipped message="x"/></testcase>'
        "</testsuite></testsuites>"
    )
    # test_c foi pulado por fora; TestE.test_e nem foi coletado; test_d o próprio dono marcou skip
    assert integrity.unexecuted_tester_tests(p) == [
        "tests.acceptance.test_f01.TestE::test_e",
        "tests.acceptance.test_f01::test_c",
    ]


async def test_pipeline_desfaz_adulteracao_e_devolve_ao_builder(env):
    pipeline, db, runner, *_ = env
    job = pipeline.new_project("Relatório de manutenção preventiva por equipamento com histórico", "Preventiva")
    job = await advance(pipeline, job["id"])
    await pipeline.submit_answers(job["id"], {})
    job = await advance(pipeline, job["id"])
    pipeline.approve_spec(job["id"])
    job = await advance(pipeline, job["id"])
    pdir = project_dir(pipeline, job)
    pipeline.approve_design(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["waiting_for"] == "deploy_approval", job["message"]
    pipeline.approve_deploy(job["id"])
    job = await advance(pipeline, job["id"])
    assert job["status"] == "done"

    runner.builder_tampers = True
    change = pipeline.new_job(job["project_id"], "change", "Filtrar o relatório por andar")
    change = await advance(pipeline, change["id"])
    await pipeline.submit_answers(change["id"], {})
    change = await advance(pipeline, change["id"])
    if change["waiting_for"] == "design_approval":
        pipeline.approve_design(change["id"])
        change = await advance(pipeline, change["id"])
    assert change["waiting_for"] == "deploy_approval", change["message"]
    events = [e["message"] for e in db.events(change["id"])]
    assert any("integridade: builder alterou" in e and "tests/acceptance/test_f01.py" in e for e in events)
    assert "assert True" not in (pdir / "tests/acceptance/test_f01.py").read_text()
    assert "exit 0" not in (pdir / "scripts/check.sh").read_text() and not (pdir / "pytest.ini").exists()
    gate_problems = [
        p for e in db.events(change["id"]) if e["kind"] == "gate" for p in (e.get("data") or {}).get("problems", [])
    ]
    assert any("Alterações desfeitas pelo orquestrador" in p for p in gate_problems)
    assert any("Gates alterados" in p or "scripts/check.sh" in p for p in gate_problems)
