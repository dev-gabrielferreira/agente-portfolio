#!/usr/bin/env bash
# Rede de segurança do handoff: commita o que ficou pendente quando uma sessão que escreve termina.
source "$(dirname "$0")/_lib.sh"
case "$ROLE" in builder|tester|designer|planner) ;; *) exit 0 ;; esac
cd "$PROJECT_DIR" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A >/dev/null 2>&1
  git commit -q -m "chore($ROLE): checkpoint automático ao fim da sessão" >/dev/null 2>&1 || true
fi
exit 0
