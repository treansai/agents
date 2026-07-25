#!/usr/bin/env bash
# Démo Agenomic — calcule l'observation de drift de l'agent sur une fenêtre.
# La plateforme coupe [from, to] en deux moitiés (baseline vs courant) et
# compare les distributions de traces (divergence Jensen-Shannon + CUSUM).
# Usage: demo/compute-drift.sh [minutes_en_arriere]      (défaut: 60)
#        demo/compute-drift.sh <from_rfc3339> <to_rfc3339>
# La plateforme coupe la fenêtre en son MILIEU : caler le point médian entre
# la phase baseline et la phase driftée pour un signal net.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a

if [[ $# -eq 2 ]]; then
  FROM="$1"; TO="$2"
else
  MINUTES="${1:-60}"
  FROM=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(minutes=${MINUTES})).strftime('%Y-%m-%dT%H:%M:%SZ'))")
  TO=$(python3 -c "from datetime import datetime,timezone;print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")
fi

echo "Fenêtre analysée : ${FROM} → ${TO}" >&2
curl -sS -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/agents/${APP_AGENOMIC_AGENT_UUID}/drift?from=${FROM}&to=${TO}" \
  | python3 -m json.tool
