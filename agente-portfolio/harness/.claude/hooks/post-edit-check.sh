#!/usr/bin/env bash
# Sensor rápido: formata e checa cada arquivo Python logo após a edição e devolve
# os problemas já como instrução de correção (feedback otimizado para o modelo).
source "$(dirname "$0")/_lib.sh"
path="$(jget '.tool_input.file_path')"
[[ "$path" != *.py || ! -f "$path" ]] && exit 0

RUFF="ruff"
[[ -x "$PROJECT_DIR/.venv/bin/ruff" ]] && RUFF="$PROJECT_DIR/.venv/bin/ruff"
command -v "$RUFF" >/dev/null 2>&1 || exit 0

"$RUFF" format --quiet "$path" >/dev/null 2>&1
out="$("$RUFF" check --fix --quiet --output-format concise "$path" 2>&1)"
if [[ -n "$out" ]]; then
  add_context "O lint encontrou problemas em $(relpath "$path") que não puderam ser corrigidos automaticamente. Corrija-os agora, antes de seguir:
$(printf '%s' "$out" | head -40)"
fi
exit 0
