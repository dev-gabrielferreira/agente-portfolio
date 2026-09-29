#!/usr/bin/env bash
# Prepara pastas e permissões a cada start do container e inicia o orquestrador.
set -euo pipefail
H="${AGENT_HOME:-/srv/agente}"
mkdir -p "$H"/{projects,deploy,secrets,logs,caddy-sites,lessons,vendor,agent-home,knowledge}

chmod 711 "$H"
chmod 700 "$H"/{deploy,secrets,logs,lessons}
chmod 755 "$H/vendor"
chown -R agent:agent "$H/projects"            # código: do agente
chmod 755 "$H/caddy-sites"                    # lido pelo Caddy
chmod 755 "$H/knowledge"                      # lido pelo Jarvis (montado só leitura)
[[ -f "$H/agente.db" ]] && chmod 600 "$H/agente.db"
[[ -f "$H/caddy-sites/00-agente.caddy" ]] || echo "# sites gerados pelo agente entram nesta pasta" > "$H/caddy-sites/00-agente.caddy"

# Home do usuário agent persistente (tokens OAuth do Figma, config dos MCPs de escopo user)
if [[ ! -L /home/agent && -d /home/agent ]]; then
  cp -an /home/agent/. "$H/agent-home/" 2>/dev/null || true
  rm -rf /home/agent && ln -s "$H/agent-home" /home/agent
fi
chown -R agent:agent "$H/agent-home"
chmod 700 "$H/agent-home"
# Configuração de usuário do agent: conectores do claude.ai desligados (o agente não enxerga Gmail,
# Drive etc. da sua conta) e telemetria desligada
runuser -u agent -- mkdir -p /home/agent/.claude
cat > /home/agent/.claude/settings.json <<'JSON'
{
  "disableClaudeAiConnectors": true,
  "env": { "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1" }
}
JSON
chown agent:agent /home/agent/.claude/settings.json

# Skills e plugins externos: usa a cópia da imagem e completa pelo catálogo se faltar algo
if [[ -d /opt/agente/seed/vendor ]]; then cp -an /opt/agente/seed/vendor/. "$H/vendor/"; fi
python -m orchestrator.skills sync | sed 's/^/skills: /' || echo "AVISO: não consegui baixar skills externas" >&2
chmod -R a+rX "$H/vendor"

# MCPs de escopo user (Context7; Figma se habilitado depois do login) registrados no agent
runuser -u agent -- env HOME=/home/agent PATH="$PATH" CONTEXT7_API_KEY="${CONTEXT7_API_KEY:-}" \
  python -m orchestrator.skills mcp-setup || echo "AVISO: registro de MCPs falhou" >&2

# Checagens que evitam surpresa no primeiro job
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "AVISO: defina CLAUDE_CODE_OAUTH_TOKEN (assinatura, via 'claude setup-token') ou ANTHROPIC_API_KEY no .env" >&2
fi
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "AVISO: CLAUDE_CODE_OAUTH_TOKEN e ANTHROPIC_API_KEY definidos; a chave da API tem prioridade e será cobrada por uso" >&2
fi
docker version --format 'Docker {{.Server.Version}} acessível' || echo "AVISO: sem acesso ao Docker (socket montado?)" >&2
if runuser -u agent -- docker ps >/dev/null 2>&1; then
  echo "ERRO DE ISOLAMENTO: o usuário agent consegue usar o Docker. Corrija antes de continuar." >&2
  exit 1
fi
runuser -u agent -- claude --version >/dev/null 2>&1 && echo "Claude Code $(runuser -u agent -- claude --version)" || echo "AVISO: claude não encontrado" >&2

exec "$@"
