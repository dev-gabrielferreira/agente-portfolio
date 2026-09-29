"""Sensor de integridade: o que os hooks não veem, o git vê.

Os hooks de PreToolUse só enxergam as ferramentas Write/Edit. Um agente pode escrever pelo shell
(`sed -i`, `git checkout`, `git stash`, `cat >`), criar um conftest que desmarca testes ou mexer na
configuração do pytest/mutmut. Numa execução real, o builder rodou `git stash` sobre os scripts de
gate para "testar sem as mudanças" — inofensivo daquela vez, mas mostrou a brecha.

Este módulo fecha a brecha de forma determinística, no orquestrador (fora do alcance do agente):

1. `enforce`         — depois de cada sessão, compara a árvore com o commit de antes e desfaz toda
                       alteração fora da área do papel (vale para escrita por qualquer caminho).
2. `restore_gates`   — os scripts de gate são sempre os do harness no momento de rodar.
3. `config_problems` — configuração de teste que desativaria testes (addopts, conftest que desmarca,
                       pytest.ini paralelo, alvos do mutmut esvaziados, comandos do frontend no
                       manifesto trocados por algo que sempre passa).
4. `unexecuted_tester_tests` — todo teste do test-engineer precisa aparecer EXECUTADO no junit.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from orchestrator import safefs
from orchestrator import stack as stack_manifest

ALWAYS_PASSES = re.compile(r"\|\|\s*(true\b|:|exit\s+0\b)|;\s*(true|exit\s+0)\s*$|^\s*(true\b|:|echo\b|exit\s+0\b)")
GATE_SCRIPTS = ("scripts/check.sh", "scripts/test_quality.py", "scripts/mutation.sh")

# Mantenha em sincronia com harness/.claude/hooks/protect-files.sh (o hook avisa; aqui se garante).
PROTECTED_ALL = re.compile(
    r"^(\.claude/|CLAUDE\.md$|scripts/(check\.sh|test_quality\.py|mutation\.sh)$|\.mcp\.json$"
    r"|\.env$|\.env\.(?!example$)|pytest\.ini$|tox\.ini$)"
)
TESTER_OWNED = re.compile(r"^tests/(acceptance/|e2e/|properties/|conftest\.py$|test_api_contract\.py$)")
TESTER_DIRS = ("tests/acceptance", "tests/e2e", "tests/properties", "tests/conftest.py", "tests/test_api_contract.py")
ONLY_HARNESS = re.compile(r"^\.harness/")
ALLOWED = {
    "tester": re.compile(r"^(tests/(?!unit/)|\.harness/)"),
    "designer": re.compile(r"^(design/|\.harness/)"),
    "planner": re.compile(r"^(SPEC\.md$|CONTEXT\.md$|docs/|\.harness/)"),
    "evaluator": ONLY_HARNESS,
    "security": ONLY_HARNESS,
    "reviewer": ONLY_HARNESS,
    "retro": ONLY_HARNESS,
}
NOISE = re.compile(
    r"(^|/)(__pycache__|\.pytest_cache|\.hypothesis|\.ruff_cache|\.mypy_cache|node_modules|[^/]+\.egg-info)(/|$)"
    r"|\.pyc$|^\.coverage(\..*)?$"
    r"|^mutants/|^\.venv/|^\.harness/(evidence/|TASK\.md$|\.evidence-reads$|mcp\.json$)|^AGENT_STOP$|^STEER\.md$"
)
COLLECTION_HOOKS = {
    "pytest_collection_modifyitems",
    "pytest_ignore_collect",
    "pytest_deselected",
    "pytest_collect_file",
    "pytest_pycollect_makeitem",
    "pytest_runtest_setup",
    "pytest_runtest_call",
    "pytest_runtest_makereport",
    "pytest_report_teststatus",
    "collect_ignore",
    "collect_ignore_glob",
}
ALLOWED_MARK_TERMS = {"not", "and", "or", "e2e", "fuzz", "(", ")"}


def disabling_args(args: list[str]) -> list[str]:
    """Argumentos de pytest que tirariam testes da execução (seleção por nome, deselect, ignore de
    pastas de teste, -m que exclui algo além de e2e/fuzz, reexecução só das falhas)."""
    bad: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
        consumes = "=" not in a and a in {"-k", "-m", "--deselect", "--ignore", "--ignore-glob"}
        if a.startswith(("-k", "--deselect", "--lf", "--last-failed", "--co", "--collect-only", "--sw")):
            bad.append(a if "=" in a or not consumes else f"{a} {value}")
        elif a.startswith(("--ignore", "--ignore-glob")) and value.strip("./").startswith("tests"):
            bad.append(f"{a} {value}" if consumes else a)
        elif a.startswith("-m") and set(value.replace("(", " ( ").replace(")", " ) ").split()) - ALLOWED_MARK_TERMS:
            bad.append(f"-m {value}")
        i += 2 if consumes else 1
    return bad


@dataclass
class Violation:
    path: str
    reason: str

    def __str__(self) -> str:
        return f"`{self.path}` ({self.reason})"


def violation_reason(role: str, path: str) -> str | None:
    if NOISE.search(path):
        return None
    if PROTECTED_ALL.match(path):
        return "arquivo do harness/gate"
    if role == "builder":
        if TESTER_OWNED.match(path):
            return "teste do test-engineer"
        if path.startswith("design/"):
            return "design aprovado"
        return None
    allowed = ALLOWED.get(role)
    if allowed is not None and not allowed.match(path):
        return f"fora da área do {role}"
    return None


async def _dirty(ws, pdir: Path, ref: str) -> set[str]:
    """Caminhos diferentes de `ref` no working tree: alterados, apagados, novos e novos-ignorados."""
    changed = set((await ws.git(pdir, "diff", "--name-only", "--no-renames", ref, check=False)).splitlines())
    changed |= set((await ws.git(pdir, "ls-files", "--others", "--exclude-standard", check=False)).splitlines())
    # arquivos novos escondidos por um .gitignore também contam (ex.: conftest ignorado em tests/)
    hidden = await ws.git(
        pdir,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--",
        "tests",
        "scripts",
        ".claude",
        check=False,
    )
    changed |= set(hidden.splitlines())
    return {c for c in changed if c and not NOISE.search(c)}


async def snapshot(ws, pdir: Path) -> tuple[str, dict[str, bytes | None]]:
    """Estado antes da sessão: o commit atual e o conteúdo dos arquivos que já estavam sujos (escritos
    pelo orquestrador ou por você), para não confundi-los com escrita do agente."""
    if not (pdir / ".git").exists():
        return "", {}
    ref = await ws.git(pdir, "rev-parse", "HEAD", check=False)
    base: dict[str, bytes | None] = {}
    for rel in await _dirty(ws, pdir, ref):
        base[rel] = _bytes(pdir, rel)
    return ref, base


async def enforce(
    ws, pdir: Path, role: str, pre_ref: str, baseline: dict[str, bytes | None] | None = None
) -> list[Violation]:
    """Desfaz, no working tree, toda alteração feita durante a sessão fora da área do papel."""
    if not pre_ref or not (pdir / ".git").exists():
        return []
    baseline = baseline or {}
    violations = []
    for rel in sorted(await _dirty(ws, pdir, pre_ref)):
        reason = violation_reason(role, rel)
        if not reason:
            continue
        f = pdir / rel
        current = _bytes(pdir, rel)
        if rel in baseline and baseline[rel] == current:
            continue  # já estava assim antes da sessão
        violations.append(Violation(rel, reason))
        if rel in baseline and baseline[rel] is not None:
            safefs.write_bytes(pdir, rel, baseline[rel])  # troca um link plantado por arquivo de verdade
            continue
        code, _ = await ws.sh("git", "cat-file", "-e", f"{pre_ref}:{rel}", cwd=pdir, agent=True, check=False)
        if code == 0:
            await ws.git(pdir, "checkout", pre_ref, "--", rel, check=False)
        else:
            if f.is_symlink() or f.is_file():
                f.unlink()
            await ws.git(pdir, "rm", "-q", "--cached", "--ignore-unmatch", "--", rel, check=False)
    return violations


def restore_gates(template: Path, pdir: Path) -> list[str]:
    """Garante que os gates executados são os do harness. Retorna os que tinham sido alterados."""
    changed = []
    for rel in GATE_SCRIPTS:
        src, dst = template / rel, pdir / rel
        if not src.is_file():
            continue
        if _bytes(pdir, rel) != src.read_bytes():
            if os.path.lexists(dst):
                changed.append(rel)
            safefs.copy_file(src, pdir, rel, mode=0o755)
    return changed


def _bytes(pdir: Path, rel: str) -> bytes | None:
    """Conteúdo de um arquivo comum do projeto; links simbólicos contam como "sem conteúdo"."""
    try:
        path = safefs.contained(pdir, rel)
    except safefs.UnsafePath:
        return None
    if not path.is_file() or path.stat().st_size >= 2_000_000:
        return None
    return path.read_bytes()


def _is_tester_file(rel: str) -> bool:
    return bool(TESTER_OWNED.match(rel))


def config_problems(pdir: Path, locked: dict | None = None) -> list[str]:
    """Configurações que desligariam testes sem apagar nenhum arquivo de teste. `locked` é a parte
    do manifesto que liga sensores, travada quando o plano foi validado."""
    problems: list[str] = []
    if locked:
        for change in stack_manifest.weakened(locked, stack_manifest.gate_keys(stack_manifest.load(pdir))):
            problems.append(
                f"`.harness/stack.json` mudou o que foi aprovado no plano ({change}): isso desligaria sensores. "
                "Volte ao valor do plano; se a arquitetura realmente mudou, registre em PROGRESS.md para replanejar."
            )
    for name in ("pytest.ini", "tox.ini"):
        if (pdir / name).exists():
            problems.append(f"`{name}` não é permitido: a configuração do pytest fica no pyproject.toml.")
    setup_cfg = pdir / "setup.cfg"
    if setup_cfg.exists() and re.search(r"^\[(tool:pytest|mutmut)\]", setup_cfg.read_text(encoding="utf-8"), re.M):
        problems.append("`setup.cfg` com seção [tool:pytest]/[mutmut] não é permitido; use o pyproject.toml.")

    try:
        data = tomllib.loads((pdir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [*problems, f"pyproject.toml ilegível: {e}"]
    tool = data.get("tool", {})
    addopts = tool.get("pytest", {}).get("ini_options", {}).get("addopts", "")
    addopts = [str(a) for a in addopts] if isinstance(addopts, list) else shlex.split(str(addopts))
    if bad := disabling_args(addopts):
        problems.append(f"`addopts` do pytest desativa testes ({', '.join(bad)}). Remova.")

    mutmut = tool.get("mutmut", {})
    manifest = stack_manifest.load(pdir)
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    domain = str(backend.get("domain") or "app/services").rstrip("/")
    domain_dir = pdir / domain
    has_domain = domain_dir.is_dir() and any(
        p.name != "__init__.py" and p.stat().st_size > 0 for p in domain_dir.rglob("*.py")
    )
    if has_domain and domain not in [str(p).rstrip("/") for p in mutmut.get("source_paths", [])]:
        problems.append(
            f"`[tool.mutmut] source_paths` precisa incluir `{domain}` (a regra de negócio declarada em "
            "backend.domain do .harness/stack.json; é onde o mutation testing mede)."
        )
    frontend = manifest.get("frontend") if isinstance(manifest.get("frontend"), dict) else {}
    for key in ("lint", "typecheck", "test", "build"):
        cmd = str(frontend.get(key) or "")
        if cmd and ALWAYS_PASSES.search(cmd):
            problems.append(
                f"`frontend.{key}` no .harness/stack.json não pode ser um comando que sempre passa: `{cmd}`."
            )
    if mutmut.get("do_not_mutate"):
        problems.append(
            "`[tool.mutmut] do_not_mutate` não é permitido: esconder código do mutation testing é fraude de teste."
        )
    if bad := disabling_args([str(a) for a in mutmut.get("pytest_add_cli_args", [])]):
        problems.append(f"`[tool.mutmut] pytest_add_cli_args` desativa testes ({', '.join(bad)}).")

    for conftest in [pdir / "conftest.py", *sorted((pdir / "tests").rglob("conftest.py"))]:
        if not conftest.is_file():
            continue
        rel = conftest.relative_to(pdir).as_posix()
        if _is_tester_file(rel) or rel.startswith(".claude/"):
            continue
        try:
            tree = ast.parse(conftest.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # o check.sh já reprova
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        names |= {
            t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
        }
        bad = sorted(names & COLLECTION_HOOKS)
        if bad:
            problems.append(
                f"`{rel}` define {', '.join(bad)}: hooks de coleta/execução só nos conftests do test-engineer. "
                "Fixtures do builder ficam em tests/unit/conftest.py."
            )
    return problems


def _declared_tester_tests(pdir: Path) -> set[tuple[str, str]]:
    """(classname do junit, nome) de cada teste do test-engineer, exceto os que ele mesmo marcou skip."""
    declared: set[tuple[str, str]] = set()
    files: list[Path] = []
    for rel in TESTER_DIRS:
        p = pdir / rel
        if p.is_dir():
            files += [f for f in p.rglob("*.py") if f.name.startswith("test_") or f.name.endswith("_test.py")]
        elif p.is_file() and p.name.startswith("test"):
            files.append(p)
    for f in files:
        rel = f.relative_to(pdir).as_posix()
        module = rel.removesuffix(".py").replace("/", ".")
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
                if not _skipped(node):
                    declared.add((module, node.name))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test") and not _skipped(node):
                declared |= {
                    (f"{module}.{node.name}", item.name)
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name.startswith("test")
                    and not _skipped(item)
                }
    return declared


def _skipped(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    return any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in node.decorator_list)


def _executed(junit_files: list[Path]) -> set[tuple[str, str]]:
    ran: set[tuple[str, str]] = set()
    for jf in junit_files:
        if not jf.is_file():
            continue
        try:
            root = ET.parse(jf).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                continue
            name = re.sub(r"\[.*\]$", "", case.get("name", ""))
            ran.add((case.get("classname", ""), name))
    return ran


def unexecuted_tester_tests(pdir: Path) -> list[str]:
    """Testes do test-engineer que existem no código mas não rodaram (desmarcados, ignorados, pulados
    por alguém que não é o dono). Só faz sentido logo após um check.sh completo e verde."""
    evidence = pdir / ".harness" / "evidence"
    junit = [evidence / "junit.xml", evidence / "junit-e2e.xml"]
    if not junit[0].is_file():
        return []
    missing = _declared_tester_tests(pdir) - _executed(junit)
    return sorted(f"{cls}::{name}" for cls, name in missing)
