#!/usr/bin/env bash
# Supervisor (root) do container do Jarvis. Mantém no ar, e reinicia em ~20 s se algo cair:
#   - o broker de sessões do laboratório (root, socket /run/jarvis/broker.sock);
#   - a sessão "Jarvis" (usuário jarvis, tmux "jarvis"): Claude Code interativo com Remote Control;
#   - o servidor "Laboratório" (usuário lab, tmux "lab"): sessões novas abertas pelo celular;
#   - uma conversa por projeto (usuário jarvis, tmux "projetos", uma janela por projeto): Remote Control
#     em modo servidor na pasta do projeto gerada por jarvis.conversas ("Jarvis · <Projeto>" no app).
#
# Os scripts que o tmux executa ficam em /srv/jarvis/run (do root): o jarvis e o lab rodam, mas não
# editam — e o root nunca escreve em pasta deles (sem risco de link simbólico plantado).
#
#   docker exec -it -u jarvis jarvis tmux attach -t jarvis    # ver/usar a sessão (Ctrl+b d sai)
#   docker exec -it -u lab jarvis tmux attach -t lab
#   docker exec -it -u jarvis jarvis tmux attach -t projetos  # Ctrl+b w lista as conversas por projeto
set -uo pipefail
J=/srv/jarvis
MODEL="${JARVIS_MODEL:-claude-opus-5-5}"
NAME="${JARVIS_NAME:-Jarvis}"
JHOME="$J/home"
LHOME="$J/lab-home"
RUN="$J/run"
PATH_MIN="/usr/local/bin:/usr/bin:/bin"

log() { echo "[supervisor $(date -u +%FT%TZ)] $*"; }
# o jarvis herda o ambiente do container (token da API para o MCP); o lab recebe ambiente limpo
as_jarvis() { runuser -u jarvis -- env HOME="$JHOME" "$@"; }
as_lab() { runuser -u lab -- env -i HOME="$LHOME" PATH="$PATH_MIN" LANG=C.UTF-8 TERM=xterm-256color TZ="${TZ:-UTC}" "$@"; }

full_login() {  # login completo da assinatura (o Remote Control não aceita token só-modelo nem API key)
  "$@" claude auth status --json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d.get("loggedIn") and d.get("authMethod") not in ("api_key", "oauth_token", "apiKey") else 1)'
}

# Os comandos vão para scripts (bash) que o tmux executa: nada de aspas aninhadas.
write_scripts() {
  local dir="$1" lab_dir="${2:-$1}"
  mkdir -p "$dir" "$lab_dir"
  local flags=(--remote-control "$NAME" --model "$MODEL")
  if [[ "${JARVIS_CHANNEL:-0}" == "1" ]]; then
    flags+=(--dangerously-load-development-channels server:agente)
  fi
  if [[ -n "${JARVIS_CHANNELS:-}" ]]; then  # ex.: plugin:telegram@claude-plugins-official
    flags+=(--channels "$JARVIS_CHANNELS")
  fi
  {
    echo '#!/usr/bin/env bash'
    echo "cd $(printf %q "$J/central")"
    # continua a conversa anterior (memória de trabalho); na primeira vez não há o que continuar
    echo "claude --continue $(printf '%q ' "${flags[@]}")|| claude $(printf '%q ' "${flags[@]}")"
    echo 'sleep 5'
  } > "$dir/run-jarvis.sh"
  {
    echo '#!/usr/bin/env bash'
    echo "cd $(printf %q "$J/lab")"
    # modo servidor: o modelo das conversas vem do .claude/settings.json do laboratório (--model não é repassado)
    echo "claude remote-control --name $(printf %q "${JARVIS_LAB_NAME:-Laboratório}") --spawn worktree" \
      "--capacity $(printf %q "${JARVIS_LAB_CAPACITY:-4}")" \
      "--permission-mode $(printf %q "${JARVIS_LAB_PERMISSION_MODE:-acceptEdits}")"
    echo 'sleep 5'
  } > "$lab_dir/run-lab.sh"
  chmod 755 "$dir/run-jarvis.sh" "$lab_dir/run-lab.sh"
}

ensure_broker() {
  if [[ -z "${BROKER_PID:-}" ]] || ! kill -0 "$BROKER_PID" 2>/dev/null; then
    log "iniciando o broker de sessões do laboratório"
    python3 -m jarvis.broker &
    BROKER_PID=$!
  fi
}

# Uma janela do tmux "projetos" por projeto escolhido por jarvis.conversas (os mais recentes + fixos).
# Janelas são fechadas só quando o sync terminou inteiro (última linha __ok__): um erro nunca derruba
# as conversas que já estavam no ar.
ensure_projects() {
  [[ "${JARVIS_CONVERSAS:-1}" == "1" ]] || return 0
  local -a lines=() wanted=()
  mapfile -t lines < <(python3 -m jarvis.conversas sync)
  local complete=0 line
  for line in "${lines[@]}"; do
    if [[ "$line" == __ok__ ]]; then complete=1
    elif [[ "$line" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then wanted+=("$line"); fi
  done
  if ! as_jarvis tmux has-session -t projetos 2>/dev/null; then
    (( ${#wanted[@]} )) || return 0
    as_jarvis tmux new-session -d -s projetos -n _ -x 220 -y 60 -c "$J/projetos" "while sleep 3600; do :; done"
  fi
  local -a running=()
  mapfile -t running < <(as_jarvis tmux list-windows -t projetos -F '#{window_id} #W' 2>/dev/null)
  local names=" " slug id name entry
  for entry in "${running[@]}"; do names+="${entry#* } "; done
  for slug in "${wanted[@]}"; do
    if [[ "$names" != *" $slug "* ]]; then
      # a pasta exata precisa estar marcada como confiável (a pasta-mãe não basta para as permissões);
      # o trust só grava quando falta, então não disputa o ~/.claude.json nas voltas seguintes
      as_jarvis python3 -P -m jarvis.trust "$J/projetos/$slug" >/dev/null || log "AVISO: não marquei $slug como confiável"
      log "abrindo a conversa do projeto $slug"
      as_jarvis tmux new-window -d -t projetos: -n "$slug" -c "$J/projetos/$slug" "bash $RUN/projetos/$slug.sh"
    fi
  done
  (( complete )) || { log "conversas: sync incompleto; nenhuma conversa fechada nesta volta"; return 0; }
  for entry in "${running[@]}"; do
    id="${entry%% *}"; name="${entry#* }"
    [[ "$name" == _ ]] && continue
    if [[ " ${wanted[*]} " != *" $name "* ]]; then
      log "fechando a conversa do projeto $name (fora dos JARVIS_CONVERSAS_MAX mais recentes)"
      as_jarvis tmux kill-window -t "$id" 2>/dev/null || true   # pelo id: nome numérico não vira índice
    fi
  done
}

main() {
  mkdir -p "$RUN"; chown root:root "$RUN"; chmod 755 "$RUN"
  write_scripts "$RUN" "$RUN"
  local warned=0
  while true; do
    ensure_broker
    if full_login as_jarvis; then
      if ! as_jarvis tmux has-session -t jarvis 2>/dev/null; then
        log "iniciando a sessão $NAME (modelo $MODEL)"
        as_jarvis tmux new-session -d -s jarvis -x 220 -y 60 -c "$J/central" "bash $RUN/run-jarvis.sh"
      fi
      ensure_projects
    elif (( warned % 10 == 0 )); then
      log "Jarvis sem login completo da assinatura. Rode no VPS: sudo ./scripts/setup-jarvis.sh"
    fi
    if [[ "${JARVIS_LAB_SERVER:-1}" == "1" ]] && full_login as_lab; then
      if ! as_lab tmux has-session -t lab 2>/dev/null; then
        log "iniciando o servidor do laboratório"
        as_lab tmux new-session -d -s lab -x 220 -y 60 -c "$J/lab" "bash $RUN/run-lab.sh"
      fi
    fi
    warned=$((warned + 1))
    sleep 20
  done
}

[[ "${1:-}" == "--only-functions" ]] || main
