#!/usr/bin/env bash
# Configura o Jarvis no VPS (rode na pasta do repositório, com sudo). Interativo: ~5 minutos.
#   1. gera o token da API do Jarvis e o segredo do segundo fator (TOTP) no .env
#   2. sobe/atualiza os containers
#   3. login da SUA assinatura no Claude Code do Jarvis (login completo — o Remote Control exige)
#   4. abre a sessão para você aceitar a confiança na pasta e ativar o Remote Control
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
pause() { read -rp "$(printf '\033[1;36m%s\033[0m ' "${1:-Enter para continuar…}")" _; }
[[ $EUID -eq 0 ]] || { echo "rode com sudo"; exit 1; }
[[ -f .env ]] || { echo "crie o .env antes (./scripts/install-vps.sh)"; exit 1; }
mkdir -p /srv/jarvis /srv/agente/knowledge

say "1/5 Token da API do Jarvis"
if ! grep -qE '^JARVIS_API_TOKEN=.+' .env; then
  sed -i '/^JARVIS_API_TOKEN=/d' .env
  echo "JARVIS_API_TOKEN=$(openssl rand -hex 32)" >> .env
  echo "gerado e salvo no .env"
else
  echo "já existe"
fi

say "2/5 Segundo fator para produção (TOTP)"
if ! grep -qE '^ADMIN_TOTP_SECRET=.+' .env; then
  docker compose up -d agente >/dev/null
  SECRET="$(docker exec agente python -c 'from orchestrator.totp import new_secret; print(new_secret())')"
  sed -i '/^ADMIN_TOTP_SECRET=/d' .env
  echo "ADMIN_TOTP_SECRET=$SECRET" >> .env
  URI="$(docker exec agente python -c "from orchestrator.totp import uri; print(uri('$SECRET'))")"
  echo "Escaneie com o app autenticador (Google Authenticator, Aegis, 1Password…):"
  docker exec agente qrencode -t ansiutf8 "$URI" || true
  echo "Sem câmera? Use 'inserir chave' com o segredo: $SECRET"
  pause "Adicionou no app? Enter…"
else
  echo "já configurado (para trocar: apague ADMIN_TOTP_SECRET do .env e rode de novo)"
fi
chmod 600 .env

say "3/5 Subindo os containers"
docker compose up -d --build agente jarvis
sleep 5

say "4/5 Login da sua assinatura no Claude Code do Jarvis"
if docker exec -u jarvis jarvis env HOME=/srv/jarvis/home claude auth status --json 2>/dev/null | grep -q '"loggedIn": true'; then
  echo "já logado"
else
  echo "Escolha a conta do Claude (assinatura). Abra o link no celular ou no computador e cole o código aqui."
  docker exec -it -u jarvis -w /srv/jarvis/central jarvis env HOME=/srv/jarvis/home claude auth login --claudeai
fi
echo
echo "Laboratório (opcional): para abrir sessões novas direto do app, o usuário 'lab' precisa do próprio"
echo "login completo. As sessões que o Jarvis cria em segundo plano já funcionam com o token do .env."
read -rp "Fazer o login do laboratório agora? [s/N] " yn
if [[ "${yn,,}" == s* ]]; then
  docker exec -it -u lab -w /srv/jarvis/lab jarvis env -i HOME=/srv/jarvis/lab-home PATH=/usr/local/bin:/usr/bin:/bin TERM=xterm-256color claude auth login --claudeai
fi

say "5/5 Primeira abertura (aceitar a pasta e ativar o Remote Control)"
echo "Vou abrir a sessão do Jarvis no terminal (tmux). Lá:"
echo "  - confirme a confiança na pasta e o 'Enable Remote Control? (y/n)' com y;"
echo "  - digite /config e ligue 'Push when actions required' e 'Push when Claude decides';"
echo "  - saia SEM fechar a sessão com Ctrl+b e depois d."
pause
for _ in $(seq 1 12); do docker exec -u jarvis jarvis tmux has-session -t jarvis 2>/dev/null && break; sleep 5; done
docker exec -it -u jarvis jarvis tmux attach -t jarvis || echo "sessão ainda não subiu: veja 'docker logs jarvis'"
sleep 25  # o supervisor sobe o laboratório se houver login do lab
if docker exec -u lab jarvis env -i HOME=/srv/jarvis/lab-home PATH=/usr/local/bin:/usr/bin:/bin tmux has-session -t lab 2>/dev/null; then
  echo "Agora o Laboratório (servidor para abrir sessões pelo celular): responda y se perguntar, depois Ctrl+b d."
  pause
  docker exec -it -u lab jarvis env -i HOME=/srv/jarvis/lab-home PATH=/usr/local/bin:/usr/bin:/bin TERM=xterm-256color tmux attach -t lab || true
fi

# conversas por projeto: se o modo servidor pedir a confirmação do Remote Control, responda uma vez
for w in $(docker exec -u jarvis jarvis tmux list-windows -t projetos -F '#W' 2>/dev/null | grep -vx _ || true); do
  if docker exec -u jarvis jarvis tmux capture-pane -p -t "projetos:$w" 2>/dev/null | grep -qi "enable remote control"; then
    echo "A conversa do projeto '$w' pede a confirmação do Remote Control: responda y e saia com Ctrl+b d."
    pause
    docker exec -it -u jarvis jarvis tmux attach -t "projetos:$w" || true
  fi
done

cat <<'TXT'

Pronto. No celular:
  1. App do Claude → Code → sessão "Jarvis" (ícone de computador com bolinha verde).
  2. Teste: "Jarvis, como estão os projetos?"
  3. Cada projeto tem a conversa própria: "Jarvis · <Projeto>" (aparece ~1 minuto depois do projeto
     existir). Digite / para ver os comandos (/status, /aprovar, /mudar, /caddy…).
  4. "Laboratório" aparece como ambiente para abrir sessões novas (cada uma na sua worktree).

Deploy em produção pelo Jarvis pede o código do autenticador. Logs: docker logs -f jarvis
TXT
