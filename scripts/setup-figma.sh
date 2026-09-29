#!/usr/bin/env bash
# Conecta o Figma oficial (MCP remoto com escrita no canvas) ao usuário `agent` do container.
# Precisa ser feito uma vez, de um terminal interativo:   ssh -t seu-vps 'cd /opt/agente-portfolio && sudo ./scripts/setup-figma.sh'
# 1. registra o servidor https://mcp.figma.com/mcp no escopo user do agent
# 2. inicia o login OAuth sem navegador: abra a URL impressa no SEU computador, autorize,
#    e cole de volta a URL completa para a qual o navegador foi redirecionado
# 3. depois, ponha FIGMA_ENABLED=true no .env e rode: docker compose up -d
set -euo pipefail
C="${CONTAINER:-agente}"
as_agent() { docker exec -it -u agent -e HOME=/home/agent "$C" "$@"; }

if ! as_agent claude mcp get figma >/dev/null 2>&1; then
  as_agent claude mcp add-json --scope user figma '{"type":"http","url":"https://mcp.figma.com/mcp"}'
fi
echo
echo "Abra a URL abaixo no navegador do seu computador, autorize e cole a URL de retorno:"
as_agent claude mcp login figma --no-browser
echo
as_agent claude mcp get figma || true
echo
echo "Pronto. Agora defina FIGMA_ENABLED=true no .env e rode: docker compose up -d"
