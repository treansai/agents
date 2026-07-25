# Agenomic demo agents

Trois systèmes d’agents gouvernés, conçus pour tester audit, replay, conformité, détection de boucle, handoffs et preuves :

| Système | Framework | Contrôle critique |
|---|---|---|
| AI Claims Reviewer | Google Agent Development Kit (ADK) | La politique déterministe garde la décision finale et impose la revue humaine |
| Autonomous DevOps Incident Responder | CrewAI | Aucune remédiation n’est exécutée; commandes dangereuses et boucles sont bloquées |
| AI Research & Compliance Team | LangGraph | Sources vérifiées, conformité obligatoire, révision bornée et paquet de preuves |

Chaque exécution produit des logs JSON, des traces Langfuse + LangSmith, une enveloppe Agenomic et un événement ATEP signé Ed25519, en plus de la chaîne d’audit SQLite signée par HMAC. Les entrées et sorties métier ne sont jamais copiées dans Agenomic : seules les corrélations, métadonnées et empreintes BLAKE3 sont persistées.

## Architecture

```text
FastAPI + API key
├── /v1/claims/review       Google ADK: intake → documents → risk → policy → explanation
├── /v1/incidents/respond   CrewAI: logs → metrics → root cause → safe plan → notification
├── /v1/research/report     LangGraph: research + verify → analysis → compliance ↻ → evidence → editor
├── /v1/audit/runs/{id}     Signed append-only audit events
├── /v1/agenomic/runs/{id}  Redacted Agenomic trace correlated to the domain run
└── /v1/agenomic/verify     ATEP signatures, Merkle roots and causal-chain verification
```

Les LLM produisent analyse et rédaction. Les invariants de sécurité restent dans du code déterministe : seuil de revue humaine, contrôles fraude, budget d’outils, denylist de commandes, limite de révision, identifiants de sources et couverture des preuves.

## Démarrage local

Prérequis : Python 3.11–3.13 (3.12 recommandé), une clé Gemini pour ADK et une clé OpenAI pour CrewAI/LangGraph.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Renseigner au minimum dans `.env` :

```dotenv
APP_API_KEY=<secret-de-32-caracteres-ou-plus>
APP_LEDGER_HMAC_KEY=<autre-secret-de-32-caracteres-ou-plus>
GOOGLE_API_KEY=<cle-google>
OPENAI_API_KEY=<cle-openai>
```

Puis lancer :

```bash
make test
make run
```

Swagger est disponible sur `http://localhost:8080/docs` hors production. Tous les endpoints métier exigent l’en-tête `X-API-Key`.

La console web locale est servie directement sur `http://localhost:8080/`. Elle fournit les trois scénarios JSON, suit les étapes d’exécution et permet de charger les événements d’audit d’un run. En développement, elle utilise la clé locale non secrète par défaut; cette valeur est refusée lorsque `APP_ENV=production`.

## Observabilité Langfuse et LangSmith

Ajouter les credentials à `.env` :

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com

LANGSMITH_TRACING=true
LANGSMITH_OTEL_ENABLED=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=agenomic-demo-agents
```

- ADK : instrumentation OpenInference vers Langfuse et intégration `configure_google_adk` vers LangSmith.
- CrewAI : instrumentation OpenTelemetry partagée, avec export Langfuse et processeur LangSmith.
- LangGraph : callback Langfuse et tracing LangSmith natif.
- Les scripts courts appellent `flush()` pour ne pas perdre les spans en file d’attente.

En production, garder `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` tant qu’une revue confidentialité/PII n’a pas explicitement autorisé la capture du contenu.

## Agenomic et preuves ATEP

Le SDK `agenomic` 0.1 est utilisé directement par les trois systèmes, en mode local/offline ; aucune clé Agenomic ou OpenAI n’est nécessaire pour écrire et vérifier les traces :

- Google ADK : run agent et policy gate enregistrés comme étapes hashées ;
- CrewAI : kickoff, fallback et contrôle des commandes enregistrés comme étapes hashées ;
- LangGraph : chaque nœud exécuté est instrumenté, y compris via l’adaptateur de compatibilité requis par les `Runnable` LangGraph 1.x ;
- toutes les enveloppes sont corrélées au `run_id` métier et portent leur propre `agenomic_run_id` et `agenomic_trace_id` ;
- les fichiers JSONL redacted et segments ATEP sont placés sous `APP_AGENOMIC_DATA_PATH` ;
- la clé privée Ed25519 locale est créée avec le mode `0600`; sa clé publique est exportée à côté pour la vérification.

La console « Voir l’audit » charge ensemble le ledger applicatif et l’enveloppe Agenomic. La readiness vérifie également les signatures, racines Merkle et liens causaux ATEP.

## Exemples API

### 1. Claims Reviewer — Google ADK

```bash
curl -sS http://localhost:8080/v1/claims/review \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $APP_API_KEY" \
  -d '{
    "claim_id":"CLM-1001",
    "claimant_id":"USR-42",
    "category":"medical",
    "amount":1250,
    "description":"Emergency medical treatment reimbursement request.",
    "documents":[
      {"document_id":"doc-1","kind":"invoice","verified":true},
      {"document_id":"doc-2","kind":"medical_report","verified":true}
    ]
  }'
```

Une demande incomplète retourne `request_documents`; une fraude dure retourne `reject`; une demande à risque ou ≥ 50 000 retourne `human_review` tant qu’un humain n’a pas statué.

### 2. Incident Responder — CrewAI

```bash
curl -sS http://localhost:8080/v1/incidents/respond \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $APP_API_KEY" \
  -d '{
    "incident_id":"INC-2026-07",
    "service":"checkout-api",
    "alert":"p95 latency above 3 seconds for 10 minutes",
    "logs":["ERROR pool exhausted waiting for postgres connection"],
    "metrics":[
      {"name":"http.p95","value":3.4,"unit":"s"},
      {"name":"db.pool.used","value":100,"unit":"percent"}
    ]
  }'
```

Le service ne lance jamais une commande. Il produit un plan, retire les commandes dangereuses, puis écrit des événements durables `notification_queued` et `ticket_queued`. Un worker externe peut ensuite livrer ces outbox events à Slack/PagerDuty/Jira avec ses propres credentials.

### 3. Research & Compliance — LangGraph

```bash
curl -sS http://localhost:8080/v1/research/report \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $APP_API_KEY" \
  -d '{
    "report_id":"RPT-001",
    "topic":"Impact du règlement européen sur les systèmes IA à haut risque",
    "jurisdiction":"EU",
    "policy_requirements":["Toute affirmation réglementaire doit avoir une source"],
    "sources":[{
      "source_id":"eu-ai-act",
      "title":"Texte officiel fourni par le client",
      "url":"https://eur-lex.europa.eu/",
      "content":"Contenu documentaire préalablement collecté et approuvé, suffisamment long pour analyse."
    }]
  }'
```

Le service ne télécharge pas arbitrairement les URL fournies : le contenu entre via un pipeline d’ingestion de confiance, ce qui évite SSRF, surprise réseau et dérive de corpus. L’éditeur ne peut citer que les `source_id` fournis et validés.

## CLI et mode déterministe

```bash
agenomic-agents claims examples/claim.json --no-llm
agenomic-agents devops examples/incident.json --no-llm
agenomic-agents research examples/research.json
agenomic-agents verify-ledger
```

`--no-llm` permet de tester les règles claims et le chemin d’escalade DevOps sans coût modèle. Le workflow recherche nécessite un LLM.

## Production

```bash
cp .env.example .env
docker compose up --build -d
```

Le conteneur tourne sans root, avec système de fichiers en lecture seule, aucune capability Linux et un volume dédié au ledger. Pour une vraie charge de production :

1. placer l’API derrière un gateway avec TLS, authentification forte, rate limiting et limite de corps ;
2. remplacer SQLite par un ledger/queue transactionnel géré si plusieurs réplicas écrivent en parallèle ;
3. placer les documents dans un stockage chiffré avec politiques de rétention et redaction PII ;
4. utiliser des identités de workload et un gestionnaire de secrets, jamais des clés dans l’image ;
5. monter `APP_AGENOMIC_DATA_PATH` sur un volume persistant à écrivain unique, ou remplacer l’export local par un backend Agenomic partagé avant d’activer plusieurs workers ;
6. exécuter evals, replay et tests de drift avant chaque promotion de prompt/modèle/policy.

## Limites explicites

- Aucun agent n’effectue un paiement, n’exécute une commande ou ne publie un rapport non conforme.
- Les notifications et tickets sont une outbox durable, pas des appels SaaS cachés.
- Le ledger est tamper-evident, pas un substitut à un journal WORM ou à un service de signature HSM.
- Les décisions d’assurance de démonstration ne constituent pas un système réglementaire complet.
- Agenomic 0.1 utilise un écrivain ATEP local sérialisé par processus ; le déploiement fourni démarre donc un seul worker.

## Références d’intégration

- [Agenomic Python SDK](https://github.com/treansai/agenomic-python/)
- [Google ADK : tracing Langfuse](https://langfuse.com/integrations/frameworks/google-adk)
- [CrewAI : tracing Langfuse](https://langfuse.com/integrations/frameworks/crewai)
- [LangGraph : tracing Langfuse](https://langfuse.com/integrations/frameworks/langchain)
- [Google ADK : tracing LangSmith](https://docs.langchain.com/langsmith/trace-with-google-adk)
- [CrewAI : tracing LangSmith](https://docs.langchain.com/langsmith/trace-with-crewai)
- [LangGraph : tracing LangSmith](https://docs.langchain.com/langsmith/trace-with-langgraph)
