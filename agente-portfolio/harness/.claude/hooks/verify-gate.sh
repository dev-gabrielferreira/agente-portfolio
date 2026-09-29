#!/usr/bin/env bash
# Contrato default-FAIL: só deixa editar features.json depois de ler alguma evidência.
# A cada escrita liberada, o registro de leituras é zerado (uma evidência não vale para sempre).
source "$(dirname "$0")/_lib.sh"
path="$(jget '.tool_input.file_path')"
[[ -z "$path" ]] && exit 0
rel="$(relpath "$path")"
[[ "$rel" != ".harness/features.json" ]] && exit 0
# Só o builder precisa de evidência; o planner cria o contrato com tudo em false.
[[ "$ROLE" != "builder" ]] && exit 0

reads="$HARNESS_DIR/.evidence-reads"
if [[ ! -s "$reads" ]]; then
  deny "Antes de alterar .harness/features.json, gere evidência (ex.: 'pytest --junitxml=.harness/evidence/junit.xml' ou um screenshot em .harness/evidence/) e abra o arquivo com a ferramenta Read. Marcar feature como pronta sem ter observado o resultado não é permitido."
fi
: > "$reads"
exit 0
