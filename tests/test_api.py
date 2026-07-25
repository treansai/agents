from fastapi.testclient import TestClient

from agenomic_agents.api import create_app
from agenomic_agents.common.config import Settings


def test_health_is_public(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_frontend_is_served_at_root(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Agent Flight Recorder" in response.text
        assert client.get("/assets/app.js").status_code == 200


def test_protected_endpoint_requires_key(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/audit/runs/unknown")
        assert response.status_code == 401


def test_audit_endpoint_accepts_valid_key(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/audit/runs/unknown", headers={"X-API-Key": "test-api-key"})
        assert response.status_code == 200
        assert response.json() == []


def test_readiness_verifies_ledger(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "ledger_valid": True,
            "agenomic_valid": True,
        }


def test_request_size_limit(settings: Settings) -> None:
    constrained = settings.model_copy(update={"max_request_bytes": 1024})
    with TestClient(create_app(constrained)) as client:
        response = client.post(
            "/v1/claims/review",
            headers={"X-API-Key": "test-api-key"},
            content="x" * 1025,
        )
        assert response.status_code == 413
