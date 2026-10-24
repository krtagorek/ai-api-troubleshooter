"""REST authentication, bounds, redaction and OpenAPI checks without external calls."""
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.auth import DemoAuthenticator, authentication_from_env



def test_health_openapi_and_authentication():
    with TestClient(create_app(runtime=object())) as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.get("/api/v1/incidents")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        document = client.get("/openapi.json").json()
        assert "/api/v1/incidents" in document["paths"]
        assert "HTTPBearer" in document["components"]["securitySchemes"]
        assert client.get("/api/v1/health/ready").status_code == 503


def test_correlation_bounds_validation_and_logs(caplog):
    auth = DemoAuthenticator(uuid4(), "x" * 32)
    with TestClient(create_app(runtime=object(), authenticator=auth)) as client:
        identifier = str(uuid4())
        response = client.post("/api/v1/incidents", json={"password": "never-log-this-secret"},
            headers={"Authorization": "Bearer " + "x" * 32, "X-Correlation-ID": identifier})
        assert response.status_code == 422
        assert response.headers["x-correlation-id"] == identifier
        assert "never-log-this-secret" not in response.text + caplog.text
        assert client.post("/api/v1/incidents", content="x" * 1_048_577).status_code == 413
        response = client.get("/health", headers={"X-Correlation-ID": "Bearer fake-secret"})
        assert response.headers["x-correlation-id"] != "Bearer fake-secret"
        assert response.headers["cache-control"] == "no-store"


def test_demo_is_explicit_and_production_disabled(monkeypatch):
    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="development"):
        authentication_from_env()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_SUBJECT_ID", str(uuid4()))
    monkeypatch.setenv("DEMO_AUTH_TOKEN", "x" * 32)
    assert isinstance(authentication_from_env(), DemoAuthenticator)


def test_cors_allowlist():
    with pytest.raises(ValueError): create_app(runtime=object(), cors_origins=["*"])
    with TestClient(create_app(runtime=object(), cors_origins=["http://localhost:5173"])) as client:
        headers = {"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"}
        assert client.options("/api/v1/incidents", headers=headers).status_code == 400
        headers["Origin"] = "http://localhost:5173"
        assert client.options("/api/v1/incidents", headers=headers).status_code == 200


def test_unexpected_errors_do_not_escape_or_log_secrets(caplog):
    app = create_app(runtime=object())
    @app.get("/failure")
    async def failure(): raise RuntimeError("password=do-not-disclose")
    with TestClient(app) as client:
        response = client.get("/failure")
        assert response.status_code == 500
        assert "do-not-disclose" not in response.text + caplog.text
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
