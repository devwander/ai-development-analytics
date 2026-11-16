#!/usr/bin/env bash
# Backup do Postgres do projeto (container ai-analytics-db) em formato custom
# do pg_dump (comprimido, restaurável com pg_restore).
#
# Uso:   scripts/db-backup.sh            # gera backups/ai_analytics_<data>.dump
# Restaurar em um banco vazio:
#   pg_restore -h 127.0.0.1 -p 5434 -U analytics -d ai_analytics --clean --if-exists backups/<arquivo>.dump
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${POSTGRES_PORT:-5434}"
PGUSER="${POSTGRES_USER:-analytics}"
PGDATABASE="${POSTGRES_DB:-ai_analytics}"
export PGPASSWORD="${POSTGRES_PASSWORD:-analytics}"

OUT_DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-10}"   # quantos backups manter
mkdir -p "$OUT_DIR"

# Sobe o container se estiver parado (usa o daemon nativo do Docker).
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
if ! docker ps --filter name=ai-analytics-db --filter status=running -q | grep -q .; then
  echo ">> Subindo o container postgres..."
  docker compose up -d postgres >/dev/null
  for _ in $(seq 1 30); do
    docker exec ai-analytics-db pg_isready -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1 && break
    sleep 1
  done
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
FILE="$OUT_DIR/${PGDATABASE}_${STAMP}.dump"

echo ">> pg_dump $PGDATABASE@$PGHOST:$PGPORT -> $FILE"
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  --format=custom --compress=zstd --no-owner --no-privileges -f "$FILE"

# Valida que o arquivo é legível pelo pg_restore.
pg_restore --list "$FILE" >/dev/null
echo ">> OK: $(du -h "$FILE" | cut -f1) $FILE"

# Retenção: mantém apenas os N mais recentes.
ls -1t "$OUT_DIR"/${PGDATABASE}_*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -v
