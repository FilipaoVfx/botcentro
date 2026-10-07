#!/usr/bin/env bash
# Videos oficiales de sesiones (SRC-26). El feed de YouTube solo trae los últimos 15 videos por canal:
# se lee cinco veces al día (botcentro-videos.timer) para no perder sesiones entre lecturas.
set -uo pipefail
cd "$(dirname "$0")/.."
today=$(date +%F)
.venv/bin/python -m botcentro.cli ingest SRC-26 --from "$today" --to "$today" && .venv/bin/python -m botcentro.cli normalize SRC-26
