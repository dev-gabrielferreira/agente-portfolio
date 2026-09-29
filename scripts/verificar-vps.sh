#!/usr/bin/env bash
# Confere, de ponta a ponta, se o agente e o Jarvis estão funcionando no VPS.
#
#   sudo ./scripts/verificar-vps.sh            # completo (faz 1 chamada mínima ao modelo)
#   sudo ./scripts/verificar-vps.sh --rapido   # sem chamar o modelo
#
# Só lê e testa: não muda nada no VPS. Rode na pasta do repositório, depois do install-vps.sh e do
# setup-jarvis.sh, e sempre que algo parecer estranho. Sai com código 1 se houver alguma falha (✖).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

RAPIDO=0
[[ "${1:-}" == "--rapido" ]] && RAPIDO=1
N_OK=0 N_FALHA=0 N_AVISO=0
ok() { printf '  \033[32m✔\033[0m %s\n' "$*"; N_OK=$((N_OK + 1)); }
fail() { printf '  \033[31m✖\033[0m %s\n' "$*"; N_FALHA=$((N_FALHA + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; N_AVISO=$((N_AVISO + 1)); }
info() { printf '    %s\n' "$*"; }
sec() { printf '\n\033[1m%s\033[0m\n' "$*"; }
envget() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
running() { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == "true" ]]; }
http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-15}" "$1" 2>/dev/null || true; }
first_ip() { getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1 {print $1}'; }
# domínio de cada bloco de site num arquivo .caddy do agente (comentários e arquivo vazio não contam)
site_hosts() { grep -E '^[^[:space:]#(][^{]*\{[[:space:]]*$' "$1" 2>/dev/null | awk '{print $1}' | sed -E 's#^https?://##; s#[:/].*$##'; }
PATH_MIN=/usr/local/bin:/usr/bin:/bin
# login do Claude Code de um usuário do container jarvis → "sim|nao <método>"
auth_of() {
  docker exec -u "$1" jarvis env -i HOME="$2" PATH="$PATH_MIN" python3 -c '
import json, subprocess
try:
    out = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=40).stdout
    d = json.loads(out or "{}")
except Exception:
    d = {}
print("sim" if d.get("loggedIn") else "nao", d.get("authMethod") or "desconhecido")' 2>/dev/null || echo "nao erro"
}

[[ $EUID -eq 0 ]] || { echo "rode com sudo"; exit 1; }
[[ -f docker-compose.yml ]] || { echo "rode na pasta do repositório do agente"; exit 1; }

DOMAIN="$(envget PORTFOLIO_DOMAIN)"; DOMAIN="${DOMAIN:-gabrielfdev.com}"
PANEL="$(envget PANEL_URL)"; PANEL="${PANEL:-https://agent.$DOMAIN}"; PANEL="${PANEL%/}"
CADDY="$(envget CADDY_CONTAINER)"; CADDY="${CADDY:-caddy}"
CFG="$(envget CADDY_CONFIG_PATH)"; CFG="${CFG:-/etc/caddy/Caddyfile}"
MODEL="$(envget AGENT_MODEL)"; MODEL="${MODEL:-claude-opus-5-5}"
JMODEL="$(envget JARVIS_MODEL)"; JMODEL="${JMODEL:-claude-opus-5-5}"
JNAME="$(envget JARVIS_NAME)"; JNAME="${JNAME:-Jarvis}"
SRV="${AGENTE_SRV:-/srv/agente}"
SITES="$SRV/caddy-sites"

# ------------------------------------------------------------------------------------------------
sec "1. Base do VPS"
if command -v docker >/dev/null; then
  ok "Docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,)"
else
  fail "Docker não encontrado"; exit 1
fi
docker compose version >/dev/null 2>&1 && ok "Docker Compose" || fail "plugin Docker Compose não encontrado"
if [[ -f .env ]]; then
  perm="$(stat -c %a .env)"
  [[ "$perm" == 600 ]] && ok ".env existe (permissão 600)" || warn ".env com permissão $perm: rode chmod 600 .env"
else
  fail ".env não existe: rode sudo ./scripts/install-vps.sh"
fi
[[ -n "$(envget CLAUDE_CODE_OAUTH_TOKEN)" ]] && ok "token da assinatura dos workers (CLAUDE_CODE_OAUTH_TOKEN)" \
  || fail "CLAUDE_CODE_OAUTH_TOKEN vazio (README, passo 2: claude setup-token)"
if [[ -z "$(envget ANTHROPIC_API_KEY)" ]]; then
  ok "ANTHROPIC_API_KEY vazia: tudo sai da assinatura, nada é cobrado por uso"
else
  warn "ANTHROPIC_API_KEY preenchida: ela tem prioridade e é COBRADA por uso. Apague se quer só a assinatura"
fi
[[ "$MODEL" == *opus* ]] && ok "modelo do pipeline: $MODEL" || warn "AGENT_MODEL=$MODEL (o combinado é Opus)"
[[ "$JMODEL" == *opus* ]] && ok "modelo do Jarvis e das sessões: $JMODEL" || warn "JARVIS_MODEL=$JMODEL (o combinado é Opus)"
[[ -n "$(envget GITHUB_TOKEN)" ]] && ok "GITHUB_TOKEN preenchido" || warn "GITHUB_TOKEN vazio: os projetos não vão para o GitHub"
[[ -n "$(envget JARVIS_API_TOKEN)" && -n "$(envget ADMIN_TOTP_SECRET)" ]] \
  && ok "token da API do Jarvis e segundo fator (TOTP) configurados" \
  || fail "JARVIS_API_TOKEN ou ADMIN_TOTP_SECRET vazio: rode sudo ./scripts/setup-jarvis.sh"
for net in interna agente-painel; do
  docker network inspect "$net" >/dev/null 2>&1 && ok "rede Docker $net" || fail "rede Docker $net não existe (install-vps.sh cria)"
done
[[ -d "$SRV/projects" && -d "$SITES" ]] && ok "$SRV (projetos e sites do Caddy)" \
  || fail "$SRV incompleto: rode sudo ./scripts/install-vps.sh"
avail="$(free -m 2>/dev/null | awk '/^Mem:/ {print $7}')"
if [[ -n "$avail" ]]; then
  (( avail >= 3000 )) && ok "memória disponível: ${avail} MB" \
    || warn "memória disponível: ${avail} MB (recomendado 3000+; builds e o navegador do avaliador pesam)"
fi
use="$(df -P /srv 2>/dev/null | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [[ -n "$use" ]]; then
  (( use < 85 )) && ok "disco de /srv: ${use}% usado" || warn "disco de /srv: ${use}% usado (limpe imagens antigas: docker image prune)"
fi

# ------------------------------------------------------------------------------------------------
sec "2. Orquestrador (container agente)"
if running agente; then
  ok "container agente rodando"
  # logo depois de um "docker compose up" o painel ainda está subindo: espera até 90 s antes de acusar
  up=0
  for _ in $(seq 1 30); do
    docker exec agente curl -fsS --max-time 5 http://127.0.0.1:8080/healthz >/dev/null 2>&1 && { up=1; break; }
    sleep 3
  done
  (( up )) && ok "painel responde dentro do container" || fail "painel não responde há 90 s: docker logs --tail 60 agente"
  if docker exec agente runuser -u agent -- docker ps >/dev/null 2>&1; then
    fail "o usuário agent (quem escreve código) consegue usar o Docker: não deveria"
  else
    ok "usuário agent (quem escreve código) sem acesso ao Docker"
  fi
  ver="$(docker exec agente claude --version 2>/dev/null | head -1)"; ver="${ver%% (*}"
  [[ -n "$ver" ]] && ok "Claude Code $ver" || fail "Claude Code não encontrado na imagem (docker compose build)"
  if (( RAPIDO )); then
    info "(--rapido: pulei a chamada ao modelo)"
  else
    out="$(timeout 180 docker exec agente runuser -u agent -- claude -p "Responda apenas: ok" \
      --model "$MODEL" --output-format json --max-turns 1 2>&1)"
    if grep -Eq '"is_error": ?false' <<<"$out"; then
      ok "worker chamou o $MODEL pela assinatura e recebeu resposta"
    elif grep -qiE 'usage limit|rate limit|limit reached' <<<"$out"; then
      warn "limite de uso da assinatura atingido agora (normal); rode de novo mais tarde"
    else
      fail "worker não conseguiu chamar o modelo: ${out:0:300}"
    fi
  fi
else
  fail "container agente não está rodando: docker compose up -d && docker logs agente"
fi

# ------------------------------------------------------------------------------------------------
sec "3. Caddy e domínios"
if running "$CADDY"; then
  ok "container do Caddy ($CADDY) rodando"
  nets=" $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$CADDY") "
  for net in interna agente-painel; do
    [[ "$nets" == *" $net "* ]] && ok "Caddy na rede $net" || fail "Caddy fora da rede $net (deploy/Caddyfile.snippet)"
  done
  mounts=" $(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$CADDY") "
  [[ "$mounts" == *" $SITES "* ]] && ok "Caddy monta $SITES (onde o agente escreve os sites)" \
    || fail "Caddy não monta $SITES: acrescente no compose do Caddy (deploy/Caddyfile.snippet)"
  main_cfg="$(docker exec "$CADDY" cat "$CFG" 2>/dev/null)"
  if grep -q "sites-agente" <<<"$main_cfg"; then
    ok "seu Caddyfile importa os sites do agente"
  else
    fail "seu Caddyfile ($CFG) não tem 'import /etc/caddy/sites-agente/*.caddy'"
  fi
  if docker exec agente docker exec "$CADDY" caddy validate --config "$CFG" --adapter caddyfile >/dev/null 2>&1; then
    ok "o orquestrador alcança o Caddy e a configuração atual é válida (o reload do deploy vai funcionar)"
  else
    fail "caddy validate falhou a partir do orquestrador. Veja: docker exec $CADDY caddy validate --config $CFG --adapter caddyfile"
  fi
  # shellcheck disable=SC2016  # $1 é do sh dentro do container
  if docker exec "$CADDY" sh -c 'test -w "$1"' sh "$CFG" 2>/dev/null; then
    ok "Caddyfile principal gravável: o agente aplica as mudanças que você aprovar com o código"
  else
    warn "Caddyfile principal só leitura no Caddy: o Jarvis propõe, mas aplicar falha (tire o ':ro' do volume, PRODUCAO.md)"
  fi
  # sites do SEU Caddyfile (fora do agente) e conflito com os que o agente gerencia
  mapfile -t own < <(grep -E '^[^[:space:]#(][^{]*\{[[:space:]]*$' <<<"$main_cfg" | sed -E 's/[[:space:]]*\{[[:space:]]*$//')
  managed=()
  shopt -s nullglob
  for f in "$SITES"/*.caddy; do while read -r h; do [[ -n "$h" ]] && managed+=("$h"); done < <(site_hosts "$f"); done
  shopt -u nullglob
  for host in "${managed[@]}"; do
    for line in "${own[@]}"; do
      for addr in ${line//,/ }; do
        [[ "${addr#*://}" == "$host" ]] && fail "domínio $host está no seu Caddyfile E nos sites do agente: remova o bloco antigo do seu Caddyfile"
      done
    done
  done
  others=()
  for line in "${own[@]}"; do [[ "$line" == *"agent.$DOMAIN"* ]] || others+=("$line"); done
  if (( ${#others[@]} )); then
    info "Sites do seu Caddyfile que o agente NÃO administra (continuam com você; para ele cuidar, use 'Adotar projeto'):"
    for line in "${others[@]}"; do info "  - $line"; done
  fi
else
  fail "container do Caddy '$CADDY' não está rodando (CADDY_CONTAINER no .env)"
fi
code="$(http_code "$PANEL/healthz")"
[[ "$code" == 200 ]] && ok "painel público $PANEL (HTTPS)" || fail "$PANEL/healthz respondeu '$code' (DNS de agent.$DOMAIN? bloco do painel no Caddyfile?)"
code="$(http_code "$PANEL/api/v1/resumo")"
[[ "$code" == 404 ]] && ok "API do Jarvis fechada para a internet (404)" \
  || fail "a API respondeu '$code' pela internet: confira o bloco '@api' do painel (deploy/Caddyfile.snippet)"
probe="verificacao-$RANDOM.$DOMAIN"
ip_probe="$(first_ip "$probe")"; ip_panel="$(first_ip "agent.$DOMAIN")"
if [[ -n "$ip_probe" && "$ip_probe" == "$ip_panel" ]]; then
  ok "DNS curinga *.$DOMAIN → $ip_probe (projetos novos ganham endereço sozinhos)"
else
  warn "DNS curinga *.$DOMAIN não aponta para o VPS ($probe → ${ip_probe:-nada}); crie o registro A '*'"
fi

# ------------------------------------------------------------------------------------------------
sec "4. Jarvis"
if running jarvis; then
  ok "container jarvis rodando"
  bad="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' jarvis \
    | grep -E '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC|DISABLE_GROWTHBOOK)=.' \
    | cut -d= -f1 | tr '\n' ' ')"
  [[ -z "$bad" ]] && ok "ambiente do Jarvis sem variáveis que desligam o Remote Control" || fail "remova do container jarvis: $bad"
  read -r logged method <<<"$(auth_of jarvis /srv/jarvis/home)"
  if [[ "$logged" != sim ]]; then
    fail "Jarvis sem login da assinatura: sudo ./scripts/setup-jarvis.sh"
  elif [[ "$method" =~ ^(api_key|apiKey|oauth_token)$ ]]; then
    fail "Jarvis logado com '$method': o Remote Control exige o login completo (claude auth login)"
  else
    ok "Jarvis com login completo da assinatura ($method)"
  fi
  if docker exec -u jarvis jarvis tmux has-session -t jarvis 2>/dev/null; then
    ok "sessão $JNAME rodando (tmux)"
    pane="$(docker exec -u jarvis jarvis tmux capture-pane -p -t jarvis -S -300 2>/dev/null)"
    if grep -qi "enable remote control" <<<"$pane"; then
      fail "a sessão espera você responder 'Enable Remote Control?': docker exec -it -u jarvis jarvis tmux attach -t jarvis"
    elif grep -qiE "do you trust|trust this folder|trust the files" <<<"$pane"; then
      fail "a sessão espera a confiança na pasta: docker exec -it -u jarvis jarvis tmux attach -t jarvis"
    elif grep -qiE "remote control|/rc active|rc active" <<<"$pane"; then
      ok "Remote Control ativo: no app, Code → $JNAME"
    else
      warn "não vi o Remote Control na tela da sessão; confira no app (Code → $JNAME, bolinha verde) ou com tmux attach"
    fi
  else
    fail "sessão $JNAME não está rodando: docker logs jarvis"
  fi
  docker exec jarvis test -S /run/jarvis/broker.sock && ok "broker do laboratório no ar" \
    || fail "broker do laboratório fora do ar (o supervisor reinicia em ~20 s; veja docker logs jarvis)"
  # shellcheck disable=SC2016  # as variáveis são do container, expandidas lá dentro
  code="$(docker exec -u jarvis jarvis sh -c 'curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer $JARVIS_API_TOKEN" "$AGENTE_API_URL/resumo"' 2>/dev/null)"
  [[ "$code" == 200 ]] && ok "Jarvis conversa com o orquestrador (API interna, com token)" \
    || fail "API interna respondeu '$code' ao Jarvis (JARVIS_API_TOKEN mudou? rode docker compose up -d)"
  if docker exec jarvis test -S /var/run/docker.sock 2>/dev/null; then
    fail "o Jarvis enxerga o socket do Docker: não deveria"
  else
    ok "Jarvis sem acesso ao Docker (não mexe em containers nem no Caddy direto)"
  fi
  if docker exec -u jarvis jarvis touch /srv/projetos/.verificacao 2>/dev/null; then
    docker exec jarvis rm -f /srv/projetos/.verificacao
    fail "o Jarvis consegue escrever no código dos projetos: deveria ser só leitura"
  else
    ok "código dos projetos só leitura para o Jarvis (mudança passa pelo pipeline e pela sua aprovação)"
  fi
  if docker exec -u lab jarvis ls /srv/jarvis/home >/dev/null 2>&1; then
    fail "o usuário lab consegue ler a pasta do Jarvis: não deveria"
  else
    ok "laboratório (usuário lab) não lê os arquivos nem o login do Jarvis"
  fi
  if [[ "$(envget JARVIS_CONVERSAS)" != 0 ]]; then
    mapfile -t wins < <(docker exec -u jarvis jarvis tmux list-windows -t projetos -F '#W' 2>/dev/null | grep -vx _)
    if (( ${#wins[@]} )); then
      ok "conversas por projeto no app: ${wins[*]}"
      for w in "${wins[@]}"; do
        pane="$(docker exec -u jarvis jarvis tmux capture-pane -p -t "projetos:$w" -S -100 2>/dev/null)"
        if grep -qi "enable remote control" <<<"$pane"; then
          fail "a conversa do projeto $w espera o 'Enable Remote Control?': docker exec -it -u jarvis jarvis tmux attach -t projetos:$w"
        fi
      done
    else
      info "conversas por projeto: nenhuma ainda (aparecem com o primeiro projeto)"
    fi
  fi
  read -r lab_logged lab_method <<<"$(auth_of lab /srv/jarvis/lab-home)"
  if [[ "$lab_logged" == sim && ! "$lab_method" =~ ^(api_key|apiKey|oauth_token)$ ]]; then
    docker exec -u lab jarvis tmux has-session -t lab 2>/dev/null \
      && ok "Laboratório no app (abrir sessões novas pelo celular)" \
      || warn "lab tem login, mas o servidor do Laboratório não está rodando (JARVIS_LAB_SERVER=1? docker logs jarvis)"
  else
    info "Laboratório pelo app: desligado (opcional; faça o login do lab no setup-jarvis.sh se quiser)"
  fi
else
  fail "container jarvis não está rodando: sudo ./scripts/setup-jarvis.sh"
fi

# ------------------------------------------------------------------------------------------------
sec "5. Projetos que o agente administra"
count=0
while IFS=$'\t' read -r name status; do
  [[ -z "$name" ]] && continue
  count=$((count + 1))
  [[ "$status" == Up* ]] && ok "$name ($status)" || fail "$name: $status"
done < <(docker ps -a --filter label=agente.project --format '{{.Names}}\t{{.Status}}' 2>/dev/null)
(( count )) || info "nenhum projeto ainda (o primeiro deploy cria o container e o site)"
shopt -s nullglob
for f in "$SITES"/*.caddy; do
  while read -r host; do
    [[ -n "$host" ]] || continue
    code="$(http_code "https://$host/health" 10)"
    if [[ "$host" == *-staging.* ]]; then
      [[ "$code" == 200 || "$code" == 401 ]] && ok "https://$host (staging) responde" || warn "https://$host (staging) respondeu '$code'"
    else
      [[ "$code" == 200 ]] && ok "https://$host/health 200" || fail "https://$host/health respondeu '$code'"
    fi
  done < <(site_hosts "$f")
done
shopt -u nullglob

# ------------------------------------------------------------------------------------------------
sec "Resumo"
printf '  %d ok · %d aviso(s) · %d falha(s)\n' "$N_OK" "$N_AVISO" "$N_FALHA"
cat <<TXT

  O que o agente administra sozinho: os containers dos projetos dele (<projeto>-staging e
  <projeto>-production), os arquivos de site em $SITES e o reload do Caddy.
  O que continua com você: o seu Caddyfile principal, os outros sites e pastas do VPS.

  Teste final pelo celular: app do Claude → Code → $JNAME → "como estão os projetos?"
TXT
(( N_FALHA == 0 ))
