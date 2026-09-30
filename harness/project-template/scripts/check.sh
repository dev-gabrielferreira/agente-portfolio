#!/usr/bin/env bash
# Gate de qualidade do projeto. Gerenciado pelo harness — agentes não editam este arquivo.
#   ./scripts/check.sh          gate completo: lint, formato, tipos, qualidade dos testes,
#                               unidade+aceitação+propriedades+fuzz com cobertura, e2e, auditoria,
#                               frontend (lint, tipos, testes, build) e detector de anti-padrões de design
#   ./scripts/check.sh --fast   gate rápido (hook de encerramento do builder)
# Mutation testing roda à parte: ./scripts/mutation.sh
#
# O que rodar vem do manifesto da arquitetura, .harness/stack.json (backend, frontend, ui_paths).
# Os testes do test-engineer são pytest em qualquer stack, então todo projeto tem pyproject.toml.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
FAST=0; [[ "${1:-}" == "--fast" ]] && FAST=1
MIN_COVERAGE="${MIN_COVERAGE:-70}"
REPORT=".harness/evidence/check-report.txt"
mkdir -p .harness/evidence

# lê um campo do manifesto (a.b.c); listas saem uma por linha; ausente/false/null → vazio
m() {
  python3 - "$1" <<'PY' 2>/dev/null
import json, sys
try:
    v = json.load(open(".harness/stack.json", encoding="utf-8"))
except Exception:
    v = {}
for k in sys.argv[1].split("."):
    v = v.get(k) if isinstance(v, dict) else None
if isinstance(v, list):
    print("\n".join(str(x) for x in v))
elif v not in (None, False, ""):
    print(v)
PY
}

if [[ ! -f pyproject.toml ]]; then
  echo "FALHA: pyproject.toml ausente. Todo projeto precisa dele: é onde ficam as ferramentas de teste (extra dev)" | tee "$REPORT"
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv >/dev/null && .venv/bin/pip install -q --upgrade pip >/dev/null
fi
if [[ pyproject.toml -nt .venv/.installed || ! -f .venv/.installed ]]; then
  .venv/bin/pip install -q -e '.[dev]' && touch .venv/.installed || { echo "FALHA: pip install -e '.[dev]'"; exit 1; }
fi
B=.venv/bin
# arquivos do harness (skills de terceiros, gates) nunca entram no lint nem na coleta de testes do projeto
RUFF_EXCLUDE=(--extend-exclude ".claude,mutants,scripts/test_quality.py,node_modules")
# a coleta e a cobertura seguem a configuração do harness: addopts, padrões de coleta e omit do
# projeto não conseguem esconder testes nem código (o orquestrador ainda confere no junit)
COVRC="$(mktemp)"; trap 'rm -f "$COVRC"' EXIT
PYTEST_IGNORE=(--ignore=.claude --ignore=mutants --ignore=node_modules -o "addopts=-p no:cacheprovider" -o testpaths=tests
  -o "python_files=test_*.py *_test.py" -o python_classes=Test -o python_functions=test
  -W "ignore::DeprecationWarning")
COV_PACKAGE="$(m backend.package)"
[[ -z "$COV_PACKAGE" && -d app ]] && COV_PACKAGE="app"
COV_ARGS=()
if [[ -n "$COV_PACKAGE" ]]; then
  COV_ARGS=(--cov="$COV_PACKAGE" --cov-config="$COVRC" --cov-report=term-missing:skip-covered --cov-fail-under="$MIN_COVERAGE")
fi
FRONT="$(m frontend.dir)"
PM="$(m frontend.package_manager)"; PM="${PM:-npm}"
has_tests() { [[ -d "$1" ]] && find "$1" -name 'test*.py' | grep -q .; }
status=0

front() {  # front <rótulo> <comando do manifesto> <script padrão>
  local label=$1 cmd=$2 fallback=$3
  [[ -z "$cmd" ]] && cmd="$PM run --if-present $fallback"
  echo "-- frontend: $label ($cmd)"
  (cd "$FRONT" && bash -c "$cmd") || status=1
}

{
  echo "== check $(date -u +%FT%TZ) fast=$FAST stack=$(m summary)"
  echo "-- ruff check";  $B/ruff check "${RUFF_EXCLUDE[@]}" . || status=1
  echo "-- ruff format"; $B/ruff format --check "${RUFF_EXCLUDE[@]}" . || status=1
  echo "-- qualidade dos testes"; $B/python scripts/test_quality.py --owner all || status=1

  if [[ -n "$FRONT" ]]; then
    if [[ ! -f "$FRONT/package.json" ]]; then
      echo "FALHA: o manifesto declara frontend em '$FRONT', mas não há $FRONT/package.json"; status=1
    else
      STAMP="$FRONT/node_modules/.agente-installed"
      if [[ ! -f $STAMP || $FRONT/package.json -nt $STAMP || ( -f $FRONT/package-lock.json && $FRONT/package-lock.json -nt $STAMP )
            || ( -f $FRONT/pnpm-lock.yaml && $FRONT/pnpm-lock.yaml -nt $STAMP ) ]]; then
        echo "-- frontend ($FRONT, $PM): dependências"
        if (cd "$FRONT" && if [[ $PM == pnpm ]]; then pnpm install --frozen-lockfile || pnpm install;
              elif [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi) >/dev/null 2>&1; then
          touch "$STAMP"
        else
          echo "FALHA: não consegui instalar as dependências do frontend"; status=1
        fi
      fi
      front lint "$(m frontend.lint)" lint
      front tipos "$(m frontend.typecheck)" typecheck
      front testes "$(m frontend.test)" test
      echo "-- frontend: testes focados ou desligados (.only/.skip/.todo/xit)"
      if grep -rEn --include='*.test.*' --include='*.spec.*' --exclude-dir=node_modules --exclude-dir=dist \
           '\b(it|test|describe)\.(only|skip|todo)\(|\bx(it|test|describe)\(' "$FRONT"; then
        echo "FALHA: há testes do frontend focados ou desligados"; status=1
      fi
      [[ $FAST -eq 0 ]] && front build "$(m frontend.build)" build
    fi
  fi

  if [[ $FAST -eq 1 ]]; then
    echo "-- pytest (rápido)"
    $B/pytest -q -x -m "not e2e and not fuzz" "${PYTEST_IGNORE[@]}" || status=1
  else
    if [[ -x $B/pyright && -n "$COV_PACKAGE" ]]; then echo "-- pyright (tipos)"; $B/pyright --level error || status=1; fi
    echo "-- pytest: unidade, aceitação, propriedades e fuzz de contrato${COV_PACKAGE:+ (cobertura mínima ${MIN_COVERAGE}% em $COV_PACKAGE)}"
    $B/pytest -q -m "not e2e" "${PYTEST_IGNORE[@]}" --junitxml=.harness/evidence/junit.xml "${COV_ARGS[@]}" || status=1
    if has_tests tests/e2e; then
      echo "-- pytest e2e (navegador)"
      $B/playwright install chromium >/dev/null 2>&1 || echo "aviso: não consegui instalar o Chromium do Playwright"
      $B/pytest -q -m e2e "${PYTEST_IGNORE[@]}" --junitxml=.harness/evidence/junit-e2e.xml || status=1
    fi
    if [[ -x $B/pip-audit ]]; then echo "-- pip-audit"; $B/pip-audit --progress-spinner off . || status=1; fi

    UI_TARGETS=()
    while IFS= read -r p; do [[ -n "$p" && -e "$p" ]] && UI_TARGETS+=("$p"); done <<< "$(m ui_paths)"
    if (( ${#UI_TARGETS[@]} )) && command -v impeccable >/dev/null 2>&1; then
      echo "-- design: detector de anti-padrões (impeccable) em ${UI_TARGETS[*]}"
      impeccable detect "${UI_TARGETS[@]}" 2> .harness/evidence/design-lint.txt >/dev/null; rc=$?
      head -60 .harness/evidence/design-lint.txt
      if [[ $rc -eq 2 ]]; then
        if [[ "${DESIGN_LINT_STRICT:-0}" == "1" ]]; then
          echo "FALHA: anti-padrões de design (DESIGN_LINT_STRICT=1)"; status=1
        else
          echo "aviso: anti-padrões de design encontrados (relatório em .harness/evidence/design-lint.txt; o avaliador também lê)"
        fi
      fi
    fi
  fi
  echo "== resultado: $([[ $status -eq 0 ]] && echo VERDE || echo VERMELHO)"
  exit "$status"   # o bloco roda num subshell por causa do pipe: o status sai por aqui
} 2>&1 | tee "$REPORT"
exit "${PIPESTATUS[0]}"
