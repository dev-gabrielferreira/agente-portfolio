#!/usr/bin/env bash
# Operador: se STEER.md existir, entrega o conteúdo ao agente uma única vez e apaga o arquivo.
source "$(dirname "$0")/_lib.sh"
f="$PROJECT_DIR/STEER.md"
[[ -s "$f" ]] || exit 0
msg="$(cat "$f")"
rm -f "$f"
add_context "MENSAGEM DO OPERADOR (Gabriel) — tem prioridade sobre o plano atual. Ajuste o que estiver fazendo:
$msg"
