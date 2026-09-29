#!/usr/bin/env bash
# Funções compartilhadas pelos hooks. Cada hook recebe o evento em JSON no stdin.
# shellcheck disable=SC2034  # variáveis usadas pelos hooks que fazem source deste arquivo
set -uo pipefail

HOOK_INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
HARNESS_DIR="$PROJECT_DIR/.harness"
ROLE="${HARNESS_ROLE:-builder}"

jget() { printf '%s' "$HOOK_INPUT" | jq -r "$1 // empty" 2>/dev/null; }

# Nega uma chamada de ferramenta (PreToolUse). A razão volta para o modelo como instrução.
deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Acrescenta contexto ao resultado de uma ferramenta (PostToolUse).
add_context() {
  jq -n --arg c "$1" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$c}}'
  exit 0
}

# Caminho relativo à raiz do projeto (para comparar com padrões).
relpath() {
  local p="$1"
  case "$p" in
    "$PROJECT_DIR"/*) printf '%s' "${p#"$PROJECT_DIR"/}" ;;
    /*) printf '%s' "$p" ;;
    ./*) printf '%s' "${p#./}" ;;
    *) printf '%s' "$p" ;;
  esac
}
