#!/usr/bin/env bash
# Démo Agenomic — état d'une session de tracking : alertes puis rapport.
# Usage: demo/tracking-status.sh <session_id> [alerts|report|events]
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a

SESSION="${1:?usage: tracking-status.sh <session_id> [alerts|report|events]}"
WHAT="${2:-alerts}"
curl -sS -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/tracking/sessions/${SESSION}/${WHAT}" | python3 -m json.tool
