#!/usr/bin/env bash
# Qdrant autoalojado en el servidor del bot (DEC-15). Solo escucha en 127.0.0.1 y exige API key.
#   ops/qdrant.sh up        crea o arranca el contenedor (reinicio automático)
#   ops/qdrant.sh status    estado y puntos de la colección activa
#   ops/qdrant.sh snapshot  snapshot de la colección en $QDRANT_DATA/snapshots (copiar fuera del servidor)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
IMAGE="qdrant/qdrant:v1.19.1"
NAME="botcentro-qdrant"
QDRANT_DATA="${QDRANT_DATA:-/var/lib/botcentro/qdrant}"
COLLECTION="botcentro-docs-e5-small-v1-v2"
auth=(-H "api-key: ${BOTCENTRO_QDRANT_API_KEY:?falta BOTCENTRO_QDRANT_API_KEY en .env}")

case "${1:-status}" in
  up)
    if docker inspect "$NAME" >/dev/null 2>&1; then
      docker start "$NAME" >/dev/null
    else
      mkdir -p "$QDRANT_DATA" && chmod 700 "$(dirname "$QDRANT_DATA")"
      docker run -d --name "$NAME" --restart unless-stopped \
        -p 127.0.0.1:6333:6333 -p 127.0.0.1:6334:6334 \
        -v "$QDRANT_DATA":/qdrant/storage \
        -e QDRANT__SERVICE__API_KEY="$BOTCENTRO_QDRANT_API_KEY" \
        -e QDRANT__TELEMETRY_DISABLED=true \
        --memory 3g "$IMAGE" >/dev/null
    fi
    ;;
  status)
    docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
    curl -fsS "${auth[@]}" "http://127.0.0.1:6333/collections/$COLLECTION" \
      | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["status"], r["points_count"], "puntos")'
    ;;
  snapshot)
    curl -fsS -X POST "${auth[@]}" "http://127.0.0.1:6333/collections/$COLLECTION/snapshots?wait=true"
    echo
    ;;
  *)
    echo "uso: $0 {up|status|snapshot}" >&2; exit 2 ;;
esac
