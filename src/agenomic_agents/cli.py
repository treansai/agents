import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agenomic_agents.claims.models import ClaimRequest
from agenomic_agents.claims.service import ClaimsService
from agenomic_agents.common.config import get_settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.common.logging import configure_logging
from agenomic_agents.common.observability import flush_observability
from agenomic_agents.devops.models import IncidentRequest
from agenomic_agents.devops.service import IncidentService
from agenomic_agents.research.models import ResearchRequest
from agenomic_agents.research.service import ResearchService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run governed demo agents from JSON payloads")
    parser.add_argument("agent", choices=["claims", "devops", "research", "verify-ledger"])
    parser.add_argument("payload", nargs="?", type=Path)
    parser.add_argument(
        "--no-llm", action="store_true", help="Use deterministic test mode where supported"
    )
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    ledger = SignedLedger(settings.database_path, settings.ledger_hmac_key.get_secret_value())
    if args.agent == "verify-ledger":
        print(json.dumps({"valid": ledger.verify()}))
        return
    if args.payload is None:
        parser.error("payload is required for this agent")
    payload: dict[str, Any] = json.loads(args.payload.read_text(encoding="utf-8"))
    try:
        if args.agent == "claims":
            result = asyncio.run(
                ClaimsService(settings, ledger).review(
                    ClaimRequest.model_validate(payload), use_llm=not args.no_llm
                )
            )
        elif args.agent == "devops":
            result = IncidentService(settings, ledger).respond(
                IncidentRequest.model_validate(payload), use_llm=not args.no_llm
            )
        else:
            result = ResearchService(settings, ledger).create_report(
                ResearchRequest.model_validate(payload)
            )
        print(result.model_dump_json(indent=2))
    finally:
        flush_observability()


if __name__ == "__main__":
    main()
