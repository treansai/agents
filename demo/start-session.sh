#!/usr/bin/env bash
# Démo Agenomic — ouvre UNE session de tracking partagée par tous les runs
# (écrit APP_AGENOMIC_TRACKING_SESSION dans .env). Baseline identique à celle
# du runtime : outils, modèle et policy du bundle v1.0.0 verrouillé.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a

SESSION_ID=$(curl -sS -X POST \
  -H "x-api-key: ${APP_AGENOMIC_API_KEY}" \
  -H 'Content-Type: application/json' \
  "${APP_AGENOMIC_ENDPOINT%/}/v1/tracking/sessions" \
  -d @- <<JSON | python3 -c "import json,sys; print(json.load(sys.stdin)['session']['session_id'])"
{
  "agent_id": "${APP_AGENOMIC_AGENT_UUID}",
  "release_id": "${APP_AGENOMIC_RELEASE:-agenomic-demo-agents@1.0.0}",
  "environment": "${APP_AGENOMIC_ENVIRONMENT:-production}",
  "baseline": {
    "allowed_tools": ["claims.agent.run", "claims.policy.evaluate"],
    "model_provider": "google",
    "model_id": "${APP_AGENOMIC_BASELINE_MODEL_ID:-gemini-2.5-flash}",
    "policy_ids": ["claims.deterministic_gate"]
  },
  "tracking_config": {
    "intent": {
      "allowed_intents": [
        "claims.decision:approve",
        "claims.decision:reject",
        "claims.decision:request_documents",
        "claims.decision:human_review"
      ],
      "forbidden_intents": ["auto_approve:high_value_claim"]
    }
  }
}
JSON
)

grep -v '^APP_AGENOMIC_TRACKING_SESSION=' .env > .env.tmp || true
echo "APP_AGENOMIC_TRACKING_SESSION=${SESSION_ID}" >> .env.tmp
mv .env.tmp .env
echo "session de tracking partagée : ${SESSION_ID}"
echo "console : /tracking/${SESSION_ID}"
