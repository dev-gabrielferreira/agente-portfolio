#!/usr/bin/env bash
# Operador: enquanto existir AGENT_STOP na raiz do projeto, nenhuma ferramenta roda.
source "$(dirname "$0")/_lib.sh"
if [[ -f "$PROJECT_DIR/AGENT_STOP" ]]; then
  deny "O operador pausou o agente (AGENT_STOP). Não tente mais nenhuma ação: registre o estado atual em .harness/PROGRESS.md mentalmente e encerre a resposta agora."
fi
exit 0
