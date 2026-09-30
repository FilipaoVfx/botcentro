#!/usr/bin/env bash
# PostgreSQL autoalojado en el servidor del bot (DEC-21): reemplaza a InsForge como base de datos.
# Solo escucha en 127.0.0.1:5434 y exige contraseña.
#   ops/postgres.sh up        crea o arranca el contenedor (reinicio automático)
#   ops/postgres.sh status    estado, tamaño de la base y conexiones
#   ops/postgres.sh psql      consola como superusuario
#   ops/postgres.sh backup    pg_dump comprimido en $PG_BACKUPS (conserva 14); copiar fuera del servidor
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
IMAGE="pgvector/pgvector:0.8.1-pg16"
NAME="botcentro-postgres"
PG_DATA="${PG_DATA:-/var/lib/botcentro/postgres}"
PG_BACKUPS="${PG_BACKUPS:-/var/lib/botcentro/backups}"
DB="botcentro"
: "${BOTCENTRO_PG_SUPERPASSWORD:?falta BOTCENTRO_PG_SUPERPASSWORD en .env}"

case "${1:-status}" in
  up)
    if docker inspect "$NAME" >/dev/null 2>&1; then
      docker start "$NAME" >/dev/null
    else
      mkdir -p "$PG_DATA" && chmod 700 "$(dirname "$PG_DATA")"
      docker run -d --name "$NAME" --restart unless-stopped \
        -p 127.0.0.1:5434:5432 \
        -v "$PG_DATA":/var/lib/postgresql/data \
        -e POSTGRES_PASSWORD="$BOTCENTRO_PG_SUPERPASSWORD" -e POSTGRES_DB="$DB" \
        -e POSTGRES_INITDB_ARGS="--auth-host=scram-sha-256 --locale=C.UTF-8 --encoding=UTF8" \
        --shm-size 256m --memory 1536m "$IMAGE" \
        -c shared_buffers=384MB -c effective_cache_size=1GB -c work_mem=16MB \
        -c maintenance_work_mem=128MB -c max_connections=60 -c idle_in_transaction_session_timeout=60s \
        -c timezone=UTC -c log_min_duration_statement=5000 >/dev/null
    fi
    until docker exec "$NAME" pg_isready -U postgres -d "$DB" >/dev/null 2>&1; do sleep 1; done
    echo "listo"
    ;;
  status)
    docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
    docker exec "$NAME" psql -U postgres -d "$DB" -Atc \
      "select pg_size_pretty(pg_database_size('$DB')) || ' · ' || count(*) || ' conexiones' from pg_stat_activity where datname = '$DB'"
    ;;
  psql)
    docker exec -it "$NAME" psql -U postgres -d "$DB"
    ;;
  backup)
    mkdir -p "$PG_BACKUPS" && chmod 700 "$PG_BACKUPS"
    file="$PG_BACKUPS/botcentro-$(date -u +%Y%m%dT%H%MZ).dump"
    docker exec "$NAME" pg_dump -U postgres -d "$DB" -Fc -Z 6 > "$file.tmp" && mv "$file.tmp" "$file"
    ls -1t "$PG_BACKUPS"/botcentro-*.dump | tail -n +15 | xargs -r rm --
    echo "$file $(du -h "$file" | cut -f1)"
    ;;
  *) echo "uso: $0 up|status|psql|backup" >&2; exit 2 ;;
esac
