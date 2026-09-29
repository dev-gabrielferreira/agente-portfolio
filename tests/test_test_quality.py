"""O detector de testes fracos é um sensor: ele próprio precisa de testes."""

import json
import shutil
import subprocess
import sys

import pytest

from orchestrator.config import REPO_DIR

SCRIPT = REPO_DIR / "harness/project-template/scripts/test_quality.py"


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts")
    for d in ("tests/unit", "tests/acceptance", "tests/e2e"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / ".harness").mkdir()
    return tmp_path


def run(proj, *args):
    out = proj / "q.json"
    r = subprocess.run(
        [sys.executable, "scripts/test_quality.py", *args, "--json", str(out)], cwd=proj, capture_output=True, text=True
    )
    return r.returncode, json.loads(out.read_text())


def rules(report):
    return sorted({p["rule"] for p in report["problems"]})


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("def test_x():\n    pass\n", "sem-verificacao"),
        ("def test_x():\n    assert True\n", "assert-constante"),
        ("def test_x(v):\n    assert v == v\n", "tautologia"),
        ("def test_x(c):\n    r = c.get('/')\n    assert r.status_code == 200\n", "so-verificacao-fraca"),
        ("def test_x(f):\n    assert f() is not None\n", "so-verificacao-fraca"),
        ("def test_x(f):\n    assert isinstance(f(), dict)\n", "so-verificacao-fraca"),
        ("import pytest\n@pytest.mark.skip\ndef test_x(f):\n    assert f() == 1\n", "skip-escondido"),
        ("import time\ndef test_x(f):\n    time.sleep(1)\n    assert f() == 1\n", "sleep"),
        ("def test_x(f):\n    try:\n        assert f() == 1\n    except Exception:\n        pass\n", "except-engolido"),
    ],
)
def test_detecta_teste_feito_para_passar(proj, body, rule):
    (proj / "tests/unit/test_a.py").write_text(body)
    code, report = run(proj)
    assert code == 1 and rule in rules(report)


@pytest.mark.parametrize(
    "body",
    [
        "def test_x(c):\n    r = c.get('/api/items')\n    assert r.status_code == 200\n    assert r.json()['total'] == 3\n",
        "import pytest\ndef test_x(f):\n    with pytest.raises(ValueError, match='positive'):\n        f(0)\n",
        "from playwright.sync_api import expect\ndef test_x(page):\n    expect(page.get_by_role('heading')).to_have_text('Oi')\n",
        "def test_x(case):\n    case.call_and_validate()\n",
        "import pytest\n@pytest.mark.skip(reason='API externa só em produção')\ndef test_x(f):\n    assert f() == 1\n",
    ],
)
def test_aceita_testes_que_verificam_comportamento(proj, body):
    (proj / "tests/unit/test_a.py").write_text(body)
    code, report = run(proj)
    assert code == 0, report


def test_rastreio_de_features_so_conta_testes_do_test_engineer(proj):
    (proj / ".harness/features.json").write_text(
        json.dumps({"features": [{"id": "F01"}, {"id": "F02"}, {"id": "F03"}]})
    )
    (proj / "tests/unit/test_f03_builder.py").write_text("def test_f03_x(f):\n    assert f() == 1\n")
    (proj / "tests/acceptance/test_a.py").write_text(
        "import pytest\n@pytest.mark.feature('F01')\ndef test_a(f):\n    assert f() == 1\n\n"
        "def test_f02_pelo_nome(f):\n    assert f() == 2\n"
    )
    code, report = run(proj, "--owner", "tester", "--trace")
    assert code == 1 and report["untraced"] == ["F03"]


def test_filtro_por_dono(proj):
    (proj / "tests/unit/test_a.py").write_text("def test_x():\n    pass\n")
    (proj / "tests/acceptance/test_b.py").write_text("def test_y(f):\n    assert f() == 1\n")
    code, report = run(proj, "--owner", "tester")
    assert code == 0 and report["problems"] == []
    code, report = run(proj, "--owner", "builder")
    assert code == 1 and report["problems"][0]["owner"] == "builder"


def test_rastreio_por_alias_pytestmark_e_classe(proj):
    (proj / ".harness/features.json").write_text(
        json.dumps({"features": [{"id": "F01"}, {"id": "F02"}, {"id": "F03"}]})
    )
    (proj / "tests/acceptance/test_a.py").write_text(
        "import pytest\nf01 = pytest.mark.feature('F01')\n\n@f01\ndef test_a(f):\n    assert f() == 1\n"
    )
    (proj / "tests/acceptance/test_b.py").write_text(
        "import pytest\npytestmark = [pytest.mark.feature('F02')]\n\ndef test_b(f):\n    assert f() == 1\n"
    )
    (proj / "tests/acceptance/test_c.py").write_text(
        "import pytest\n@pytest.mark.feature('F03')\nclass TestC:\n    def test_c(self, f):\n        assert f() == 1\n"
    )
    code, report = run(proj, "--owner", "tester", "--trace")
    assert code == 0 and report["untraced"] == [], report
