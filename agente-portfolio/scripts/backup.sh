#!/usr/bin/env bash
# Backup do agente e dos dados dos projetos em produção. Agende no cron do host, ex.:
#   15 3 * * * /opt/agente-portfolio/scripts/backup.sh /var/backups/agente >> /var/log/agente-backup.log 2>&1
set -euo pipefail
DEST="${1:-/var/backups/agente}"; KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M)"; mkdir -p "$DEST"
# estado do agente (sem venvs, que são recriados)
tar --exclude='*/.venv' --exclude='*/node_modules' -czf "$DEST/agente-$STAMP.tgz" -C /srv agente
# memória do Jarvis (preferências e decisões que ele anotou); o login do Claude fica de fora de propósito
[[ -d /srv/jarvis/central/memoria ]] && tar -czf "$DEST/jarvis-memoria-$STAMP.tgz" -C /srv/jarvis/central memoria
# volume /data de cada container de produção criado pelo agente
for vol in $(docker volume ls --format '{{.Name}}' | grep -E -- '-production-data$' || true); do
  docker run --rm -v "$vol":/data:ro -v "$DEST":/backup alpine tar -czf "/backup/$vol-$STAMP.tgz" -C /data .
done
find "$DEST" -name '*.tgz' -mtime +"$KEEP_DAYS" -delete
echo "backup ok em $DEST ($STAMP)"
