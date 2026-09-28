#!/usr/bin/env bash
# Actualización diaria de fuentes (ejecutada por botcentro-actualizar.timer). Cada paso es
# independiente: si uno falla, los demás corren y el servicio termina con error para que se vea.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
today=$(date +%F)
from=$(date -d '14 days ago' +%F)   # ventana con solape: las fuentes publican con retraso
status=0
step() { echo "== $*"; "$PY" -m botcentro.cli "$@" || { echo "!! falló: $*"; status=1; }; }

step ingest SRC-01 --from "$from" --to "$today" --window-days 7
step normalize SRC-01
step ingest SRC-06 --from "$from" --to "$today"
step normalize SRC-06
step index-fichas --batch 50
exit $status
