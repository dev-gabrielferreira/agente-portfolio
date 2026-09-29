#!/usr/bin/env bash
# Instala o agente no VPS (Ubuntu 24.04 com Docker + Caddy já rodando).
# Uso: sudo ./scripts/install-vps.sh     (a partir da pasta do repositório clonado)
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mAVISO: %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "rode com sudo"; exit 1; }
command -v docker >/dev/null || { echo "Docker não encontrado"; exit 1; }

say "1/6 Pastas em /srv/agente"
mkdir -p /srv/agente/{projects,deploy,secrets,logs,caddy-sites,lessons}
chmod 711 /srv/agente; chmod 700 /srv/agente/{secrets,deploy,logs}

say "2/6 Redes Docker"
docker network inspect interna >/dev/null 2>&1 || { warn "rede 'interna' não existe; criando"; docker network create interna; }
docker network inspect agente-painel >/dev/null 2>&1 || docker network create agente-painel
CADDY="$(grep -E '^CADDY_CONTAINER=' .env 2>/dev/null | cut -d= -f2 || true)"; CADDY="${CADDY:-caddy}"
if docker inspect "$CADDY" >/dev/null 2>&1; then
  docker network connect agente-painel "$CADDY" 2>/dev/null || true
  echo "Caddy ($CADDY) conectado à rede agente-painel (faça isso também no compose dele — ver deploy/Caddyfile.snippet)"
else
  warn "container do Caddy '$CADDY' não encontrado. Ajuste CADDY_CONTAINER no .env."
fi

say "3/6 Arquivo .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$(openssl rand -hex 32)|" .env
  PASS="$(openssl rand -base64 18 | tr -d '/+=')"
  sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$PASS|" .env
  chmod 600 .env
  echo "Senha do painel gerada: $PASS   (guarde; está no .env)"
fi
# .env criado à mão a partir do exemplo: troca a senha de exemplo e gera o segredo da sessão
if grep -qE '^ADMIN_PASSWORD=(troque-por-uma-senha-longa)?$' .env; then
  PASS="$(openssl rand -base64 18 | tr -d '/+=')"
  sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$PASS|" .env
  echo "Senha do painel gerada: $PASS   (guarde; está no .env)"
fi
grep -qE '^SESSION_SECRET=.+' .env || sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$(openssl rand -hex 32)|" .env
chmod 600 .env
grep -qE '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=.+' .env || warn "preencha CLAUDE_CODE_OAUTH_TOKEN no .env (veja README, passo 'Token do Claude')"
grep -qE '^GITHUB_TOKEN=.+' .env || warn "GITHUB_TOKEN vazio: os projetos não serão publicados no GitHub"

say "4/6 Caddy"
CFG="$(grep -E '^CADDY_CONFIG_PATH=' .env 2>/dev/null | cut -d= -f2 || true)"; CFG="${CFG:-/etc/caddy/Caddyfile}"
if ! docker exec "$CADDY" grep -q "sites-agente" "$CFG" 2>/dev/null; then
  warn "seu Caddyfile ainda não importa os sites do agente. Siga deploy/Caddyfile.snippet e recarregue o Caddy."
fi

say "5/6 Build e start"
docker compose build
docker compose up -d
sleep 8
docker compose logs --tail 20 agente

say "6/6 Verificações"
docker exec agente runuser -u agent -- docker ps >/dev/null 2>&1 && { echo "FALHA: agent acessa o Docker"; exit 1; } || echo "ok: usuário agent isolado do Docker"
# o painel leva alguns segundos para subir (sincroniza skills e a base de conhecimento antes)
for _ in $(seq 1 30); do
  docker exec agente curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break
  sleep 3
done
if docker exec agente curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
  echo "ok: painel respondendo"
else
  warn "o painel não respondeu em 90 s: veja 'docker logs --tail 60 agente'"
fi
echo
echo "Pronto. Configure o DNS (*.gabrielfdev.com e agent.gabrielfdev.com → IP do VPS) e acesse https://agent.gabrielfdev.com"
echo "Depois do Caddy e do Jarvis (docs/VPS.md), confira tudo com: sudo ./scripts/verificar-vps.sh"
