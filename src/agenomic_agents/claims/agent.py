from google.adk.agents import Agent, SequentialAgent

from agenomic_agents.common.config import get_settings


def build_claims_agent(model: str | None = None) -> SequentialAgent:
    """Build the review pipeline. State is passed between stages via output_key."""
    selected_model = model or get_settings().adk_model
    intake = Agent(
        name="claim_intake_agent",
        model=selected_model,
        instruction=(
            "Extract the claim facts from the user JSON. Do not infer absent facts. "
            "Return a compact factual summary and explicitly list unknowns."
        ),
        output_key="intake_summary",
    )
    verification = Agent(
        name="document_verification_agent",
        model=selected_model,
        instruction=(
            "Review the original claim and {intake_summary}. Treat verified=false as unverified. "
            "Never claim that a document is authentic merely because it is present."
        ),
        output_key="verification_summary",
    )
    risk = Agent(
        name="risk_scoring_agent",
        model=selected_model,
        instruction=(
            "Analyze fraud and operational risk using only the original claim, "
            "{intake_summary}, and {verification_summary}. Explain risk indicators; do not decide."
        ),
        output_key="risk_summary",
    )
    policy = Agent(
        name="policy_agent",
        model=selected_model,
        instruction=(
            "Check the evidence against the stated constraints. High-value or high-risk claims "
            "must be sent to a human. Never override deterministic controls. Context: "
            "{verification_summary} {risk_summary}"
        ),
        output_key="policy_summary",
    )
    decision = Agent(
        name="decision_explanation_agent",
        model=selected_model,
        instruction=(
            "Produce a concise advisory explanation from {policy_summary}. The application policy "
            "engine, not you, makes the final decision. Do not include secrets or personal data."
        ),
        output_key="decision_explanation",
    )
    return SequentialAgent(
        name="ai_claims_reviewer",
        description="Auditable multi-stage claims review pipeline",
        sub_agents=[intake, verification, risk, policy, decision],
    )


root_agent = build_claims_agent()
