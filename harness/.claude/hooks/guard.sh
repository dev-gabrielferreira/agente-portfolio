#!/usr/bin/env bash
# Guarda computacional: bloqueia comandos destrutivos ou fora do escopo do agente.
# A mensagem de negação é escrita para o modelo: diz o que fazer no lugar.
source "$(dirname "$0")/_lib.sh"
cmd="$(jget '.tool_input.command')"
[[ -z "$cmd" ]] && exit 0

check() { # $1 = regex estendida, $2 = mensagem
  if printf '%s' "$cmd" | grep -Eqi -- "$1"; then deny "Bloqueado pelo harness: $2"; fi
}

check '(^|[;&|[:space:]])sudo([[:space:]]|$)'            "sem sudo. Tudo que você precisa roda como seu usuário."
check '(^|[;&|[:space:]])(docker|podman|kubectl)([[:space:]]|$)' "você não tem acesso a containers. O orquestrador faz build e deploy; teste localmente com uvicorn/pytest."
check 'git[[:space:]]+push'                               "não faça push. O orquestrador publica depois da aprovação."
check 'git[[:space:]]+(remote|config[[:space:]]+--global)' "configuração de remoto/identidade é do orquestrador."
check 'git[[:space:]]+(reset[[:space:]]+--hard|clean[[:space:]]+-[a-z]*f|checkout[[:space:]]+--[[:space:]]+\.)' "comando git destrutivo. Use 'git revert' ou 'git stash' se precisar desfazer algo."
check 'rm[[:space:]]+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)[a-z]*[[:space:]]+(/|~|\$HOME|\.\.|\*)([[:space:]]|$)' "remoção recursiva ampla. Apague arquivos específicos dentro do projeto."
check '(mkfs|dd[[:space:]]+if=|:\(\)[[:space:]]*\{|shutdown|reboot|chmod[[:space:]]+-R[[:space:]]+777)' "comando de sistema perigoso."
check '(ssh|scp|rsync|nc|ncat|telnet)[[:space:]]'         "sem conexões remotas. Use só a rede para instalar pacotes e ler documentação."
check '169\.254\.169\.254|metadata\.google'                "acesso a metadados de nuvem não é permitido."
check '(cat|less|more|head|tail|grep|cp|base64)[^|;&]*(\.env([[:space:]]|$)|/run/secrets|secrets/)' "arquivos de segredo não são lidos pelo agente. Use .env.example para documentar variáveis."
check '(^|[;&|[:space:]])(printenv|env|set)([[:space:]]*$|[[:space:]]*\|)'       "não liste o ambiente inteiro; consulte só a variável que precisa."
check '(curl|wget)[^|;&]*\|[[:space:]]*(ba|z)?sh'          "não execute scripts baixados da internet. Instale pacotes pelo pip/npm."
check 'mutmut[[:space:]]+apply'                             "mutmut apply grava o mutante no código-fonte. Use 'mutmut show' para ver o diff."
check 'pip[[:space:]]+install[^|;&]*--break-system-packages' "use o ambiente virtual do projeto (.venv), não o Python do sistema."
exit 0
