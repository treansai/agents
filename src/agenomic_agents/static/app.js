const agents = {
  claims: {
    title: "AI Claims Reviewer",
    framework: "Google Agent Development Kit",
    badge: "Policy gate active",
    endpoint: "/v1/claims/review",
    action: "Analyser la demande",
    payload: {
      claim_id: "CLM-1001",
      claimant_id: "USR-42",
      category: "medical",
      amount: 1250,
      currency: "EUR",
      description: "Emergency medical treatment reimbursement request.",
      documents: [
        { document_id: "doc-1", kind: "invoice", verified: true },
        { document_id: "doc-2", kind: "medical_report", verified: true },
      ],
      signals: {
        identity_match: true,
        amount_anomaly: false,
        duplicate_claim: false,
        provider_mismatch: false,
        prior_claims_12m: 0,
      },
    },
  },
  devops: {
    title: "Autonomous Incident Responder",
    framework: "CrewAI",
    badge: "Execution disabled",
    endpoint: "/v1/incidents/respond",
    action: "Diagnostiquer l’incident",
    payload: {
      incident_id: "INC-2026-07",
      service: "checkout-api",
      environment: "production",
      alert: "p95 latency above 3 seconds for 10 minutes",
      logs: ["ERROR pool exhausted waiting for postgres connection"],
      metrics: [
        { name: "http.p95", value: 3.4, unit: "s" },
        { name: "db.pool.used", value: 100, unit: "percent" },
      ],
    },
  },
  research: {
    title: "Research & Compliance Team",
    framework: "LangGraph",
    badge: "Compliance required",
    endpoint: "/v1/research/report",
    action: "Produire le rapport",
    payload: {
      report_id: "RPT-001",
      topic: "Impact du règlement européen sur les systèmes IA à haut risque",
      jurisdiction: "EU",
      policy_requirements: ["Toute affirmation réglementaire doit avoir une source"],
      sources: [
        {
          source_id: "eu-ai-act",
          title: "Texte officiel fourni par le client",
          url: "https://eur-lex.europa.eu/",
          content:
            "Contenu documentaire préalablement collecté et approuvé, suffisamment long pour analyse.",
        },
      ],
    },
  },
};

const elements = {
  apiKey: document.querySelector("#api-key"),
  payload: document.querySelector("#payload"),
  payloadHelp: document.querySelector("#payload-help"),
  runButton: document.querySelector("#run-agent"),
  runLabel: document.querySelector("#run-label"),
  requestState: document.querySelector("#request-state"),
  resultPanel: document.querySelector("#result-panel"),
  resultOutput: document.querySelector("#result-output"),
  emptyResult: document.querySelector("#empty-result"),
  auditButton: document.querySelector("#load-audit"),
  statusDot: document.querySelector("#status-dot"),
  runtimeLabel: document.querySelector("#runtime-label"),
};

let activeAgent = "claims";
let activeRunId = null;
let activeResult = null;

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function selectAgent(name) {
  activeAgent = name;
  activeRunId = null;
  activeResult = null;
  const config = agents[name];
  document.querySelector("#agent-title").textContent = config.title;
  document.querySelector("#framework-label").textContent = config.framework;
  document.querySelector("#policy-badge").textContent = config.badge;
  elements.runLabel.textContent = config.action;
  elements.payload.value = formatJson(config.payload);
  elements.auditButton.disabled = true;
  elements.resultOutput.hidden = true;
  elements.emptyResult.hidden = false;
  setFieldMessage("Modifiez l’exemple, puis lancez une exécution.");
  resetTrace();

  document.querySelectorAll(".agent-tab").forEach((button) => {
    const selected = button.dataset.agent === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function setFieldMessage(message, error = false) {
  elements.payloadHelp.textContent = message;
  elements.payloadHelp.classList.toggle("error", error);
}

function setTrace(stage) {
  const order = ["input", "control", "evidence"];
  const current = order.indexOf(stage);
  document.querySelectorAll(".trace-step").forEach((step) => {
    const index = order.indexOf(step.dataset.step);
    step.classList.toggle("current", index === current);
    step.classList.toggle("complete", index < current);
  });
}

function resetTrace() {
  setTrace("input");
}

async function runAgent() {
  let payload;
  try {
    payload = JSON.parse(elements.payload.value);
    elements.payload.value = formatJson(payload);
    setFieldMessage("JSON valide. Exécution en cours.");
  } catch (error) {
    setFieldMessage(`JSON invalide : ${error.message}`, true);
    elements.payload.focus();
    return;
  }

  const apiKey = elements.apiKey.value.trim();
  if (!apiKey) {
    elements.requestState.textContent = "Ajoutez APP_API_KEY.";
    elements.apiKey.focus();
    return;
  }

  sessionStorage.setItem("agent-console-api-key", apiKey);
  elements.runButton.disabled = true;
  elements.requestState.textContent = "Agent en cours…";
  elements.auditButton.disabled = true;
  setTrace("control");

  try {
    const response = await fetch(agents[activeAgent].endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({ detail: "Réponse non JSON" }));
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : formatJson(body));
    }
    activeResult = body;
    activeRunId = body.run_id || null;
    showResult(body);
    elements.requestState.textContent = "Exécution terminée.";
    elements.auditButton.disabled = !activeRunId;
    setTrace("evidence");
  } catch (error) {
    activeResult = { error: error.message };
    showResult(activeResult);
    elements.requestState.textContent = "Exécution interrompue.";
    setTrace("input");
  } finally {
    elements.runButton.disabled = false;
  }
}

function showResult(result) {
  elements.resultOutput.querySelector("code").textContent = formatJson(result);
  elements.emptyResult.hidden = true;
  elements.resultOutput.hidden = false;
  elements.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadAudit() {
  if (!activeRunId) return;
  elements.auditButton.disabled = true;
  elements.requestState.textContent = "Lecture du ledger…";
  try {
    const response = await fetch(`/v1/audit/runs/${encodeURIComponent(activeRunId)}`, {
      headers: { "X-API-Key": elements.apiKey.value.trim() },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Audit indisponible");
    showResult({ execution: activeResult, audit_events: body });
    elements.requestState.textContent = `${body.length} événement(s) d’audit chargé(s).`;
  } catch (error) {
    elements.requestState.textContent = error.message;
  } finally {
    elements.auditButton.disabled = false;
  }
}

async function checkRuntime() {
  try {
    const response = await fetch("/healthz", { cache: "no-store" });
    if (!response.ok) throw new Error();
    elements.statusDot.className = "status-dot online";
    elements.runtimeLabel.textContent = "Runtime local prêt";
  } catch {
    elements.statusDot.className = "status-dot offline";
    elements.runtimeLabel.textContent = "Runtime indisponible";
  }
}

document.querySelectorAll(".agent-tab").forEach((button) => {
  button.addEventListener("click", () => selectAgent(button.dataset.agent));
});

document.querySelector("#reset-payload").addEventListener("click", () => selectAgent(activeAgent));
document.querySelector("#format-payload").addEventListener("click", () => {
  try {
    elements.payload.value = formatJson(JSON.parse(elements.payload.value));
    setFieldMessage("JSON formaté et valide.");
  } catch (error) {
    setFieldMessage(`JSON invalide : ${error.message}`, true);
  }
});
document.querySelector("#toggle-secret").addEventListener("click", (event) => {
  const visible = elements.apiKey.type === "text";
  elements.apiKey.type = visible ? "password" : "text";
  event.currentTarget.textContent = visible ? "Afficher" : "Masquer";
  event.currentTarget.setAttribute("aria-label", visible ? "Afficher la clé" : "Masquer la clé");
});
document.querySelector("#copy-result").addEventListener("click", async () => {
  if (!activeResult) return;
  await navigator.clipboard.writeText(elements.resultOutput.textContent);
  elements.requestState.textContent = "Résultat copié.";
});
elements.runButton.addEventListener("click", runAgent);
elements.auditButton.addEventListener("click", loadAudit);

// This non-secret development fallback matches Settings. Production rejects it.
elements.apiKey.value =
  sessionStorage.getItem("agent-console-api-key") || "development-only-key-change-me";
selectAgent("claims");
checkRuntime();
