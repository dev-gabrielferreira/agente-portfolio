"""Manifesto da arquitetura (.harness/stack.json): validação, detecção, starters e leitura no gate."""

import json
import subprocess

from orchestrator import stack
from orchestrator.config import REPO_DIR

HARNESS = REPO_DIR / "harness"


def base() -> dict:
    return json.loads((HARNESS / "starters" / "fastapi-react" / "stack.json").read_text())


def test_starters_do_harness_sao_validos():
    known = stack.starters(HARNESS)
    assert set(known) == {"python-fastapi", "fastapi-react"}
    for path in known.values():
        assert stack.validate(json.loads((path / "stack.json").read_text()), set(known)) == []


def test_validacao_explica_cada_problema():
    data = base()
    data.update(start="uvicorn app.main:app", health="health", starter="django-magico")
    data["backend"].update(language="go", asgi="app.main", domain="../fora")
    data["frontend"].update(dir="/abs", package_manager="yarn", test="npm test\nrm -rf /")
    problems = " | ".join(stack.validate(data, {"python-fastapi"}))
    for expected in (
        "start deve ser",
        "health deve ser",
        "starter 'django-magico' não existe",
        "backend.language",
        "backend.asgi",
        "backend.domain",
        "frontend.dir",
        "frontend.package_manager",
        "frontend.test",
    ):
        assert expected in problems, expected
    assert stack.validate({}, set()) == [".harness/stack.json ausente ou não é um objeto JSON"]
    assert "declare ao menos backend ou frontend" in " ".join(
        stack.validate({"version": 1, "summary": "x", "start": "x {port}", "backend": None, "frontend": None})
    )


def test_deteccao_para_projeto_adotado(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = None\n")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}")
    (tmp_path / "web" / "pnpm-lock.yaml").write_text("")
    data = stack.detect(tmp_path)
    assert data["backend"]["asgi"] == "app.main:app" and data["frontend"] == {"dir": "web", "package_manager": "pnpm"}
    assert stack.validate(data) == []


def test_starter_nao_sobrescreve_e_completa_o_manifesto(tmp_path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("# do planner/builder\n")
    manifest = {
        "version": 1,
        "starter": "python-fastapi",
        "summary": "meu resumo",
        "start": "x {port}",
        "backend": {"package": "app"},
    }
    (tmp_path / ".harness" / "stack.json").write_text(json.dumps(manifest))
    copied = stack.apply_starter(tmp_path, HARNESS)
    assert "app/main.py" not in copied and (tmp_path / "app" / "main.py").read_text() == "# do planner/builder\n"
    assert "pyproject.toml" in copied and "Dockerfile" in copied
    merged = stack.load(tmp_path)
    assert merged["summary"] == "meu resumo" and merged["start"] == "x {port}"  # o plano manda
    assert (
        merged["backend"]["asgi"] == "app.main:app" and merged["backend"]["package"] == "app"
    )  # o resto vem do starter
    assert not any("node_modules" in c or "__pycache__" in c for c in copied)


def test_gate_le_o_manifesto(tmp_path):
    script = (HARNESS / "project-template" / "scripts" / "check.sh").read_text()
    fn = script[script.index("m() {") : script.index("\n}\n", script.index("m() {")) + 3]
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "stack.json").write_text(json.dumps(base()))

    def m(field):
        return subprocess.run(["bash", "-c", f"{fn}\nm {field}"], cwd=tmp_path, capture_output=True, text=True).stdout

    assert m("frontend.dir") == "frontend\n"
    assert m("backend.package") == "app\n"
    assert m("ui_paths") == "frontend/src\n"
    assert m("frontend.nada") == "" and m("backend.language.x") == ""
    (tmp_path / ".harness" / "stack.json").write_text("{quebrado")
    assert m("frontend.dir") == ""
