#!/usr/bin/env bash
# Redis local para el contexto breve del bot (DEC-18): solo 127.0.0.1, contraseña en .env,
# persistencia AOF y memoria acotada. El estado es temporal (TTL ≤ 24 h); no guarda hechos.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
PASS="${BOTCENTRO_REDIS_URL#redis://:}"; PASS="${PASS%@*}"
NAME="botcentro-redis"
case "${1:-status}" in
  up)
    if docker inspect "$NAME" >/dev/null 2>&1; then docker start "$NAME" >/dev/null; else
      mkdir -p /var/lib/botcentro/redis
      docker run -d --name "$NAME" --restart unless-stopped -p 127.0.0.1:6379:6379 \
        -v /var/lib/botcentro/redis:/data --memory 512m redis:8.2-alpine \
        redis-server --requirepass "$PASS" --appendonly yes --maxmemory 256mb \
        --maxmemory-policy volatile-ttl --save "" >/dev/null
    fi ;;
  status)
    docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
    docker exec "$NAME" redis-cli --no-auth-warning -a "$PASS" info keyspace | tail -n +2 ;;
  *) echo "uso: $0 {up|status}" >&2; exit 2 ;;
esac
