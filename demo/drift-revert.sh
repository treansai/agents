#!/usr/bin/env bash
# Démo Agenomic — retire le drift : restaure la policy, le genome et le
# paramètre de gate depuis la sauvegarde baseline.
set -euo pipefail
cd "$(dirname "$0")/.."

for pair in \
  "demo/.baseline/policy.py:src/agenomic_agents/claims/policy.py" \
  "demo/.baseline/genome.yaml:agent-bundle/genome.yaml" \
  "demo/.baseline/claims_gate.yaml:agent-bundle/policies/claims_gate.yaml" \
  "demo/.baseline/system.md:agent-bundle/prompts/system.md"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  if [[ -f "$src" ]]; then
    cp "$src" "$dst"
    echo "restauré: $dst"
  else
    echo "pas de sauvegarde pour $dst (drift jamais appliqué ?)" >&2
  fi
done

echo
echo "Correctif en place. Rebuild du bundle corrigé :"
echo "  agm build agent-bundle -o agent-bundle/dist/claims-reviewer-v1.0.1.bundle.tar.zst"
