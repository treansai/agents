import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agenomic_agents.claims.models import ClaimRequest, ClaimReview
from agenomic_agents.claims.service import ClaimsService
from agenomic_agents.common.config import Settings, get_settings
from agenomic_agents.common.ledger import AuditEvent, SignedLedger
from agenomic_agents.common.logging import configure_logging, get_logger, request_id_context
from agenomic_agents.common.observability import flush_observability
from agenomic_agents.devops.models import IncidentRequest, IncidentResponse
from agenomic_agents.devops.service import IncidentService
from agenomic_agents.research.models import ResearchRequest, ResearchResponse
from agenomic_agents.research.service import ResearchService

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    ledger = SignedLedger(
        runtime_settings.database_path, runtime_settings.ledger_hmac_key.get_secret_value()
    )
    claims = ClaimsService(runtime_settings, ledger)
    incidents = IncidentService(runtime_settings, ledger)
    research = ResearchService(runtime_settings, ledger)
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        logger.info("application_started", environment=runtime_settings.env)
        yield
        flush_observability()
        logger.info("application_stopped")

    api = FastAPI(
        title="Agenomic Demo Agents",
        version="1.0.0",
        description="Governed Google ADK, CrewAI, and LangGraph agent systems",
        docs_url="/docs" if runtime_settings.env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    api.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    async def require_api_key(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> None:
        expected = runtime_settings.api_key.get_secret_value()
        if x_api_key is None or not hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    protected = Depends(require_api_key)

    @api.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        from uuid import uuid4

        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:128]
        token = request_id_context.set(request_id)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > runtime_settings.max_request_bytes:
            request_id_context.reset(token)
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)

    @api.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        logger.warning("request_rejected", error_type=type(exc).__name__)
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @api.get("/healthz", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @api.get("/readyz", tags=["operations"])
    async def ready() -> dict[str, Any]:
        valid = await run_in_threadpool(ledger.verify)
        if not valid:
            raise HTTPException(status_code=503, detail="Audit ledger verification failed")
        return {"status": "ready", "ledger_valid": True}

    @api.post(
        "/v1/claims/review",
        response_model=ClaimReview,
        dependencies=[protected],
        tags=["google-adk"],
    )
    async def review_claim(payload: ClaimRequest) -> ClaimReview:
        return await claims.review(payload)

    @api.post(
        "/v1/incidents/respond",
        response_model=IncidentResponse,
        dependencies=[protected],
        tags=["crewai"],
    )
    async def respond_to_incident(payload: IncidentRequest) -> IncidentResponse:
        return await run_in_threadpool(incidents.respond, payload)

    @api.post(
        "/v1/research/report",
        response_model=ResearchResponse,
        dependencies=[protected],
        tags=["langgraph"],
    )
    async def create_research_report(payload: ResearchRequest) -> ResearchResponse:
        return await run_in_threadpool(research.create_report, payload)

    @api.get(
        "/v1/audit/runs/{run_id}",
        response_model=list[AuditEvent],
        dependencies=[protected],
        tags=["audit"],
    )
    async def audit_run(run_id: str) -> list[AuditEvent]:
        return await run_in_threadpool(ledger.list_run, run_id)

    return api


app = create_app()
