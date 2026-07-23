import json
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agenomic_agents.claims.agent import build_claims_agent
from agenomic_agents.claims.models import ClaimRequest, ClaimReview
from agenomic_agents.claims.policy import evaluate_claim
from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.common.observability import configure_adk_observability, observation


class ClaimsService:
    def __init__(self, settings: Settings, ledger: SignedLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    async def review(self, claim: ClaimRequest, *, use_llm: bool = True) -> ClaimReview:
        run_id = str(uuid4())
        self.ledger.append(
            run_id=run_id,
            agent="claims.intake",
            action="claim_received",
            payload={"claim_id": claim.claim_id, "category": claim.category},
        )
        model_analysis: str | None = None
        with observation("claims-review"):
            if use_llm:
                model_analysis = await self._run_adk(claim, run_id)
            evaluation = evaluate_claim(claim, self.settings.claims_human_review_threshold)
        event = self.ledger.append(
            run_id=run_id,
            agent="claims.policy",
            action="decision_enforced",
            payload={
                "claim_id": claim.claim_id,
                "decision": evaluation.decision.value,
                "risk_score": evaluation.risk_score,
                "human_approval_required": evaluation.human_approval_required,
            },
        )
        return ClaimReview(
            run_id=run_id,
            claim_id=claim.claim_id,
            decision=evaluation.decision,
            risk_score=evaluation.risk_score,
            reasons=evaluation.reasons,
            missing_documents=evaluation.missing_documents,
            human_approval_required=evaluation.human_approval_required,
            model_analysis=model_analysis,
            audit_signature=event.signature,
        )

    async def _run_adk(self, claim: ClaimRequest, run_id: str) -> str | None:
        configure_adk_observability()
        session_service = InMemorySessionService()
        session = await session_service.create_session(
            app_name="claims_reviewer", user_id=claim.claimant_id, session_id=run_id
        )
        runner = Runner(
            agent=build_claims_agent(self.settings.adk_model),
            app_name="claims_reviewer",
            session_service=session_service,
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=claim.model_dump_json(exclude={"human_approval"}))],
        )
        final_text: str | None = None
        async for event in runner.run_async(
            user_id=claim.claimant_id, session_id=session.id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                text = getattr(event.content.parts[0], "text", None)
                if text:
                    final_text = str(text)[:10_000]
        self.ledger.append(
            run_id=run_id,
            agent="claims.adk",
            action="analysis_completed",
            payload={"has_output": final_text is not None, "input_sha": _stable_hash(claim)},
        )
        return final_text


def _stable_hash(claim: ClaimRequest) -> str:
    import hashlib

    canonical = json.dumps(claim.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
