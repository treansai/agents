#!/usr/bin/env bash
# Démo Agenomic — applique le drift intentionnel « hotfix silencieux ».
# Le seuil de revue humaine passe de 50 000 à 500 000 dans le code ET dans le
# genome du bundle, mais le behavior contract (l'engagement) reste inchangé.
set -euo pipefail
cd "$(dirname "$0")/.."

POLICY=src/agenomic_agents/claims/policy.py
GENOME=agent-bundle/genome.yaml
GATE=agent-bundle/policies/claims_gate.yaml
PROMPT=agent-bundle/prompts/system.md

mkdir -p demo/.baseline
cp -n "$POLICY" demo/.baseline/policy.py || true
cp -n "$GENOME" demo/.baseline/genome.yaml || true
cp -n "$GATE" demo/.baseline/claims_gate.yaml || true
cp -n "$PROMPT" demo/.baseline/system.md || true

# 1. Le code : la gate montant >= 50 000 devient >= 500 000 (la ligne de scoring
#    reste à 50 000 — hotfix pressé, incohérence réaliste).
python3 - "$POLICY" <<'EOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
target = "if score >= human_review_threshold or claim.amount >= 50_000:"
drifted = "if score >= human_review_threshold or claim.amount >= 500_000:"
if drifted in src:
    print("drift déjà appliqué:", p)
elif target in src:
    p.write_text(src.replace(target, drifted, 1))
    print("drift appliqué:", p)
else:
    sys.exit(f"motif introuvable dans {p}")
EOF

# 2. Le bundle : paramètre de policy et prompt « mis à jour » pour que la
#    release driftée paraisse cohérente au push — le contrat, lui, n'a pas
#    bougé : c'est lui qui fait foi.
python3 - "$GATE" <<'EOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
p.write_text(src.replace("human_review_amount_threshold: 50000",
                         "human_review_amount_threshold: 500000"))
print("drift appliqué:", p)
EOF

python3 - "$PROMPT" <<'EOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
p.write_text(src.replace("amount >= 50 000 EUR", "amount >= 500 000 EUR"))
print("drift appliqué:", p)
EOF

echo
echo "Drift en place. Rebuild du bundle drifté :"
echo "  agm build agent-bundle -o agent-bundle/dist/claims-reviewer-v1.1.0.bundle.tar.zst"
