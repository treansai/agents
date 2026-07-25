#!/usr/bin/env bash
# Démo Agenomic — suit en direct la session de tracking (SSE).
# Usage: demo/watch-tracking.sh <session_id>
# Requiert APP_AGENOMIC_ENDPOINT et APP_AGENOMIC_API_KEY dans l'environnement
# (ou dans agents/.env, chargé automatiquement).
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a

SESSION="${1:?usage: watch-tracking.sh <session_id>}"
exec curl -N -sS \
  -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/tracking/sessions/${SESSION}/stream"
