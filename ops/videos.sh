#!/usr/bin/env bash
# Sesiones del día, cinco veces al día (botcentro-videos.timer):
#  * SRC-01 Senado (agenda, votos, asistencias) de los últimos 3 días; la agenda pide además 21 días por
#    delante, que es lo que permite mostrar la «próxima sesión» en cuanto el Senado la publica.
#  * SRC-26 videos oficiales de sesiones: el feed de YouTube solo trae los últimos 15 por canal.
# Cada paso es independiente: si uno falla, el otro corre y el servicio termina con error para que se vea.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
today=$(date +%F)
from=$(date -d '3 days ago' +%F)
status=0
step() { echo "== $*"; "$PY" -m botcentro.cli "$@" || { echo "!! falló: $*"; status=1; }; }
step ingest SRC-01 --from "$from" --to "$today" --window-days 7
step normalize SRC-01
step ingest SRC-26 --from "$today" --to "$today"
step normalize SRC-26
exit $status
