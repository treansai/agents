import json
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agenomic_agents.claims.agent import build_claims_agent
from agenomic_agents.claims.models import ClaimDecision, ClaimRequest, ClaimReview
from agenomic_agents.claims.policy import PolicyEvaluation, evaluate_claim
from agenomic_agents.common.agenomic import (
    AgenomicRuntime,
    correlate_run,
    label_decision,
    trace_step,
)
from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.common.observability import configure_adk_observability, observation


class ClaimsService:
    def __init__(
        self,
        settings: Settings,
        ledger: SignedLedger,
        agenomic: AgenomicRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.agenomic = agenomic or AgenomicRuntime(settings)
        self._traced_review = self.agenomic.traced("claims", self._review)

    async def review(self, claim: ClaimRequest, *, use_llm: bool = True) -> ClaimReview:
        return await self._traced_review(claim, use_llm=use_llm)

    async def _review(self, claim: ClaimRequest, *, use_llm: bool = True) -> ClaimReview:
        run_id = str(uuid4())
        agenomic_run_id, agenomic_trace_id = correlate_run(
            run_id, framework="google-adk", workflow="claims-review"
        )
        self.ledger.append(
            run_id=run_id,
            agent="claims.intake",
            action="claim_received",
            payload={"claim_id": claim.claim_id, "category": claim.category},
        )
        model_analysis: str | None = None
        with observation("claims-review"):
            if use_llm:
                with trace_step("claims.agent.run", server="google-adk", input_value=claim):
                    model_analysis = await self._run_adk(claim, run_id)
            with trace_step(
                "claims.policy.evaluate",
                server="deterministic-policy",
                input_value={"claim_id": claim.claim_id, "category": claim.category},
            ):
                evaluation = evaluate_claim(
                    claim, self.settings.claims_human_review_threshold
                )
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
        label_decision(evaluation.decision.value)
        self._emit_tracking(claim, evaluation, run_id, use_llm=use_llm)
        return ClaimReview(
            run_id=run_id,
            agenomic_run_id=agenomic_run_id,
            agenomic_trace_id=agenomic_trace_id,
            claim_id=claim.claim_id,
            decision=evaluation.decision,
            risk_score=evaluation.risk_score,
            reasons=evaluation.reasons,
            missing_documents=evaluation.missing_documents,
            human_approval_required=evaluation.human_approval_required,
            model_analysis=model_analysis,
            audit_signature=event.signature,
        )

    def _emit_tracking(
        self,
        claim: ClaimRequest,
        evaluation: PolicyEvaluation,
        run_id: str,
        *,
        use_llm: bool,
    ) -> None:
        """Report this run's observable behavior to the cloud tracking session.

        Facts only: the tools actually exercised, the model actually configured,
        and the enforced decision expressed as an intent. The high-value bound
        mirrors the locked behavior contract, on purpose independent from the
        policy code under surveillance.
        """
        cloud = self.agenomic.cloud
        if not cloud.enabled:
            return
        policy_input_hash = _stable_hash(claim)
        if use_llm:
            cloud.emit(
                {
                    "type": "model.call.completed",
                    "model": {"provider": "google", "model": self.settings.adk_model},
                    "run_id": run_id,
                }
            )
            cloud.emit(
                {
                    "type": "tool.call.completed",
                    "tool": {"name": "claims.agent.run"},
                    "input_hash": policy_input_hash,
                    "run_id": run_id,
                }
            )
        cloud.emit(
            {
                "type": "tool.call.completed",
                "tool": {"name": "claims.policy.evaluate"},
                "input_hash": policy_input_hash,
                "run_id": run_id,
            }
        )
        intent = f"claims.decision:{evaluation.decision.value}"
        if (
            evaluation.decision is ClaimDecision.APPROVE
            and claim.human_approval is None
            and claim.amount >= self.settings.claims_high_value_amount
        ):
            intent = "auto_approve:high_value_claim"
        cloud.emit({"type": "intent.detected", "intent": intent, "run_id": run_id})
        cloud.emit(
            {
                "type": "policy.evaluated",
                "policy_result": {
                    "policy_id": "claims.deterministic_gate",
                    "outcome": "allow",
                    "decision": evaluation.decision.value,
                    "risk_score": evaluation.risk_score,
                    "human_approval_required": evaluation.human_approval_required,
                },
                "run_id": run_id,
            }
        )

    async def _run_adk(self, claim: ClaimRequest, run_id: str) -> str | None:
        configure_adk_observability()
        session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
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
