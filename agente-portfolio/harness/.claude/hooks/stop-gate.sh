#!/usr/bin/env bash
# Sensor de encerramento (por papel):
#   builder -> não termina com ./scripts/check.sh --fast vermelho
#   tester  -> não termina com testes próprios mal formatados ou com "cheiro" de teste fraco
# Na segunda tentativa de parar (stop_hook_active=true) libera, para não criar loop infinito;
# o orquestrador roda os gates completos de qualquer forma.
source "$(dirname "$0")/_lib.sh"
[[ "$(jget '.stop_hook_active')" == "true" ]] && exit 0
[[ -f "$PROJECT_DIR/AGENT_STOP" ]] && exit 0
cd "$PROJECT_DIR" || exit 0

block() {
  jq -n --arg r "$1
$(printf '%s' "$2" | tail -60)" '{decision:"block", reason:$r}'
  exit 0
}

case "$ROLE" in
  builder)
    [[ -x scripts/check.sh ]] || exit 0
    out="$(timeout 540 ./scripts/check.sh --fast 2>&1)" \
      || block "Você tentou encerrar com ./scripts/check.sh --fast falhando. Corrija antes de parar (ou registre em .harness/PROGRESS.md por que não é possível). Saída (final):" "$out" ;;
  tester)
    PY=.venv/bin/python; [[ -x $PY ]] || PY=python3
    dirs=(); for d in tests/acceptance tests/e2e tests/properties; do [[ -d $d ]] && dirs+=("$d"); done
    [[ ${#dirs[@]} -eq 0 ]] && exit 0
    RUFF=.venv/bin/ruff; [[ -x $RUFF ]] || RUFF=ruff
    out="$($RUFF format --quiet "${dirs[@]}" 2>&1; $RUFF check --fix --quiet "${dirs[@]}" 2>&1)" \
      || block "Seus testes têm problemas de lint. Corrija antes de encerrar:" "$out"
    if [[ -f scripts/test_quality.py ]]; then
      out="$($PY scripts/test_quality.py --owner tester 2>&1)" \
        || block "Seus testes têm padrões de teste fraco (feitos para passar). Corrija antes de encerrar:" "$out"
    fi ;;
esac
exit 0
