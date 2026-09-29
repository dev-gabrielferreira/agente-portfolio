#!/usr/bin/env bash
# Sensor do contrato default-FAIL: registra quando o agente abre uma evidência.
source "$(dirname "$0")/_lib.sh"
path="$(jget '.tool_input.file_path')"
[[ -z "$path" ]] && exit 0
rel="$(relpath "$path")"
case "$rel" in
  .harness/evidence/*|*.png|*.jpg|*junit*.xml|*report*.json|*report*.txt|*coverage*.txt)
    mkdir -p "$HARNESS_DIR"
    printf '%s %s\n' "$(date -u +%FT%TZ)" "$rel" >> "$HARNESS_DIR/.evidence-reads" ;;
esac
exit 0
