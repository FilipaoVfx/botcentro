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
# Investigaciones: contratos SECOP II de los municipios piloto y planes de adquisiciones de sus entidades.
PILOTO='{"territories":[{"departamento":"Caquetá","municipio":"Florencia"},{"departamento":"Valle del Cauca","municipio":"Buenaventura"},{"departamento":"Arauca","municipio":"Arauca"}]}'
step ingest SRC-15 --from "$from" --to "$today" --max-pages 50 --filters "$PILOTO"
step normalize SRC-15
codes=$("$PY" -m botcentro.db.entity_codes) && step ingest SRC-25 --from 2000-01-01 --to "$today" --max-pages 50 --filters "{\"entity_codes\": $codes}"
step normalize SRC-25
exit $status
