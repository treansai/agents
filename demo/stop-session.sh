#!/usr/bin/env bash
# Démo Agenomic — clôt la session de tracking partagée et retire la variable
# de .env. Affiche le rapport final (pass / warn / fail).
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a

SESSION="${1:-${APP_AGENOMIC_TRACKING_SESSION:?pas de session active}}"
curl -sS -X POST -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  -H 'Content-Type: application/json' -d '{}' \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/tracking/sessions/${SESSION}/stop" >/dev/null
curl -sS -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/tracking/sessions/${SESSION}/report" | python3 -m json.tool

grep -v '^APP_AGENOMIC_TRACKING_SESSION=' .env > .env.tmp || true
mv .env.tmp .env
echo "session ${SESSION} arrêtée." >&2
