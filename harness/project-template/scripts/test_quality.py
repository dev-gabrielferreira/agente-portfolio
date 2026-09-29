#!/usr/bin/env python3
"""Detector de testes fracos ("feitos para passar") e de rastreabilidade feature → teste.

Gerenciado pelo harness — agentes não editam este arquivo.

    python scripts/test_quality.py                      # todos os testes, só padrões fracos
    python scripts/test_quality.py --owner tester       # só os testes do test-engineer
    python scripts/test_quality.py --owner tester --trace --json out.json

Regras (erro = reprova o gate):
  sem-verificacao   teste sem assert, pytest.raises, expect(...) ou validação equivalente
  assert-constante  assert True / assert 1 / assert "texto"
  tautologia        assert x == x
  so-verificacao-fraca  todos os asserts são fracos (is not None, isinstance, status 2xx, truthiness)
  skip-escondido    skip/xfail sem reason, ou pytest.skip() no corpo
  sleep             time.sleep em teste (use espera por condição)
  except-engolido   try/except que engole a falha dentro do teste
  sem-rastreio      (--trace) feature de features.json sem nenhum teste marcado com o id
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTER_OWNED = re.compile(r"^tests/(acceptance/|e2e/|properties/|conftest\.py$|test_api_contract\.py$)")
STRONG_CALL = re.compile(r"^(assert_\w*|check_\w+|verify_\w+|expect\w*|call_and_validate|to_(have|be|contain|match)\w*)$")
FIX = {
    "sem-verificacao": "afirme o resultado observável (valor, estado persistido, resposta).",
    "assert-constante": "remova; afirme algo sobre o comportamento.",
    "tautologia": "compare com o valor esperado calculado à mão.",
    "so-verificacao-fraca": "verifique o conteúdo (valores do corpo, estado após reload), não só que existe ou que deu 200.",
    "skip-escondido": "se o teste falha, é bug: reporte. skip só com reason explicando dependência externa.",
    "sleep": "espere por condição (expect(...).to_be_visible(), polling com timeout).",
    "except-engolido": "use pytest.raises(Erro, match=...) ou deixe a exceção falhar o teste.",
    "sem-rastreio": "escreva testes de aceitação com @pytest.mark.feature(\"<id>\") cobrindo cada critério.",
}


@dataclass
class Problem:
    path: str
    line: int
    rule: str
    owner: str
    test: str
    message: str


def owner_of(rel: str) -> str:
    return "tester" if TESTER_OWNED.match(rel) else "builder"


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def is_weak(test: ast.expr) -> bool:
    """Asserts que passam com quase qualquer implementação."""
    if isinstance(test, (ast.Name, ast.Attribute)):
        return True  # assert resp.ok / assert data
    if isinstance(test, ast.Call) and call_name(test) in {"isinstance", "callable", "hasattr", "bool"}:
        return True
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op, right, left = test.ops[0], test.comparators[0], test.left
        if isinstance(op, ast.IsNot) and isinstance(right, ast.Constant) and right.value is None:
            return True
        if isinstance(op, ast.NotEq) and isinstance(right, ast.Constant) and right.value in (None, ""):
            return True
        left_name = ast.unparse(left)
        if left_name.endswith("status_code"):
            if isinstance(op, ast.Eq) and isinstance(right, ast.Constant) and right.value in (200, 201, 204):
                return True
            if isinstance(op, ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
                return True
            if isinstance(op, ast.Lt) and isinstance(right, ast.Constant) and right.value in (300, 400, 500):
                return True
        if (isinstance(left, ast.Call) and call_name(left) == "len" and isinstance(op, (ast.GtE, ast.Gt))
                and isinstance(right, ast.Constant) and right.value in (0, -1)):
            return True
    return False


def decorator_problems(fn: ast.FunctionDef) -> list[tuple[int, str, str]]:
    out = []
    for dec in fn.decorator_list:
        name = call_name(dec)
        if name.endswith(("mark.skip", "mark.xfail", "mark.skipif")):
            has_reason = isinstance(dec, ast.Call) and any(k.arg == "reason" for k in dec.keywords)
            if not has_reason:
                out.append((dec.lineno, "skip-escondido", f"{name} sem reason"))
    return out


def check_test(fn: ast.FunctionDef, rel: str, owner: str) -> list[Problem]:
    problems: list[Problem] = []

    def add(line: int, rule: str, msg: str) -> None:
        problems.append(Problem(rel, line, rule, owner, fn.name, f"{msg} — {FIX[rule]}"))

    for line, rule, msg in decorator_problems(fn):
        add(line, rule, msg)

    asserts: list[ast.Assert] = []
    strong_other = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            asserts.append(node)
        elif isinstance(node, ast.With):
            for item in node.items:
                if call_name(item.context_expr).endswith(("raises", "warns", "deprecated_call")):
                    strong_other = True
        elif isinstance(node, ast.Call):
            name = call_name(node)
            if STRONG_CALL.match(name.split(".")[-1]):
                strong_other = True
            if name in {"time.sleep", "sleep"}:
                add(node.lineno, "sleep", "time.sleep no teste")
            if name in {"pytest.skip", "pytest.xfail"}:
                add(node.lineno, "skip-escondido", f"{name}() no corpo do teste")
        elif isinstance(node, ast.ExceptHandler):
            body_is_silent = all(isinstance(b, (ast.Pass, ast.Continue)) or
                                 (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)) for b in node.body)
            if body_is_silent:
                add(node.lineno, "except-engolido", "exceção capturada e ignorada")

    for a in asserts:
        if isinstance(a.test, ast.Constant):
            add(a.lineno, "assert-constante", f"assert {ast.unparse(a.test)}")
        elif (isinstance(a.test, ast.Compare) and len(a.test.ops) == 1 and isinstance(a.test.ops[0], ast.Eq)
              and ast.unparse(a.test.left) == ast.unparse(a.test.comparators[0])):
            add(a.lineno, "tautologia", f"assert {ast.unparse(a.test)}")

    if not asserts and not strong_other:
        add(fn.lineno, "sem-verificacao", f"{fn.name} não verifica nada")
    elif asserts and not strong_other and all(is_weak(a.test) for a in asserts):
        add(fn.lineno, "so-verificacao-fraca", f"{fn.name} só tem verificações fracas")
    return problems


def marker_ids(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    """Ids de feature em `pytest.mark.feature("F01")`, num alias (`f01 = pytest.mark.feature(...)`)
    ou numa lista (`pytestmark = [pytest.mark.feature("F01"), ...]`)."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return set().union(*(marker_ids(e, aliases) for e in node.elts)) if node.elts else set()
    if isinstance(node, ast.Name):
        return aliases.get(node.id, set())
    if isinstance(node, ast.Call) and call_name(node).endswith("mark.feature"):
        return {a.value.upper() for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return set()


def module_markers(tree: ast.Module) -> tuple[dict[str, set[str]], set[str]]:
    aliases: dict[str, set[str]] = {}
    module_ids: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            ids = marker_ids(stmt.value, aliases)
            if ids:
                if stmt.targets[0].id == "pytestmark":
                    module_ids |= ids
                else:
                    aliases[stmt.targets[0].id] = ids
    return aliases, module_ids


def feature_ids_in(fn: ast.FunctionDef, aliases: dict[str, set[str]], inherited: set[str]) -> set[str]:
    ids = {i.upper() for i in re.findall(r"(?i)(?:^|_)(f\d{2,3})(?:_|$)", fn.name)} | inherited
    for dec in fn.decorator_list:
        ids |= marker_ids(dec, aliases)
    return ids


def is_test_fn(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def scan(owner_filter: str) -> tuple[list[Problem], set[str]]:
    problems: list[Problem] = []
    traced: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("test*.py")):
        rel = path.relative_to(ROOT).as_posix()
        owner = owner_of(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            problems.append(Problem(rel, e.lineno or 0, "sem-verificacao", owner, "-", f"erro de sintaxe: {e.msg}"))
            continue
        aliases, module_ids = module_markers(tree)
        class_ids: dict[int, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                ids = set().union(*(marker_ids(d, aliases) for d in node.decorator_list)) if node.decorator_list else set()
                for item in node.body:
                    class_ids[id(item)] = ids
            if is_test_fn(node):
                if owner == "tester":
                    traced |= feature_ids_in(node, aliases, module_ids | class_ids.get(id(node), set()))
                if owner_filter in ("all", owner):
                    problems += check_test(node, rel, owner)
    return problems, traced


def untraced_features(traced: set[str]) -> list[str]:
    f = ROOT / ".harness" / "features.json"
    if not f.exists():
        return []
    feats = json.loads(f.read_text(encoding="utf-8")).get("features", [])
    return [x["id"] for x in feats if x.get("id", "").upper() not in traced]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", choices=["all", "builder", "tester"], default="all")
    ap.add_argument("--trace", action="store_true", help="exige teste do test-engineer para cada feature")
    ap.add_argument("--json", help="grava o relatório em JSON")
    args = ap.parse_args()

    problems, traced = scan(args.owner)
    untraced = untraced_features(traced) if args.trace else []
    for p in problems:
        print(f"{p.path}:{p.line} [{p.rule}] ({p.owner}) {p.message}")
    for fid in untraced:
        print(f".harness/features.json [sem-rastreio] (tester) {fid} não tem teste de aceitação — {FIX['sem-rastreio']}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"problems": [asdict(p) for p in problems], "untraced": untraced},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    if problems or untraced:
        print(f"test_quality: {len(problems)} problema(s), {len(untraced)} feature(s) sem teste")
        return 1
    print("test_quality: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
