import json
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from mock_api.server import app

AUTH = {"Authorization": "Bearer demo-token"}
ORDER = {"inventory_id": "1", "customer_id": "1", "quantity": 2}
ENDPOINTS = [("GET", "/inventory/1"), ("GET", "/customers/1"),
             ("POST", "/orders"), ("GET", "/health")]


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def send(client, method, path, **kwargs):
    if method == "POST":
        kwargs.setdefault("json", ORDER)
    kwargs.setdefault("headers", AUTH)
    return client.request(method, path, **kwargs)


def metadata(response):
    body = response.json()
    assert str(UUID(body["request_id"])) == response.headers["X-Request-ID"]
    assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None


@pytest.mark.parametrize("method,path", ENDPOINTS)
@pytest.mark.parametrize("scenario,status", [
    ("success", 200), ("unauthorized", 401), ("forbidden", 403),
    ("not_found", 404), ("invalid_payload", 422), ("rate_limit", 429),
    ("internal_error", 500), ("service_unavailable", 503), ("timeout", 504),
    ("schema_mismatch", 200),
])
def test_scenarios(client, method, path, scenario, status):
    response = send(client, method, path, params={"demo_scenario": scenario})
    assert response.status_code == status
    metadata(response)
    if status != 200:
        assert response.json()["error"]["message"]
    elif scenario == "schema_mismatch":
        assert response.json()["data"] == {"unexpected_field": "demo schema mismatch"}
    if status == 429:
        assert response.headers["Retry-After"] == "30"


@pytest.mark.parametrize("method,path", ENDPOINTS[:-1])
@pytest.mark.parametrize("token,status", [(None, 401), ("demo-expired-token", 401),
                                        ("demo-readonly-token", 403)])
def test_auth(client, method, path, token, status):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = send(client, method, path, headers=headers)
    assert response.status_code == status
    metadata(response)


@pytest.mark.parametrize("path", ["/inventory/missing", "/customers/missing"])
def test_missing_resources(client, path):
    response = client.get(path, headers=AUTH)
    assert response.status_code == 404
    metadata(response)


@pytest.mark.parametrize("field", ["inventory_id", "customer_id"])
def test_missing_order_resource(client, field):
    response = client.post("/orders", headers=AUTH, json={**ORDER, field: "missing"})
    assert response.status_code == 404
    metadata(response)


@pytest.mark.parametrize("payload", [{}, {**ORDER, "quantity": 0},
    {**ORDER, "quantity": "2"}, {**ORDER, "quantity": True},
    {**ORDER, "password": "secret-password"}])
def test_invalid_payload(client, payload):
    response = client.post("/orders", headers=AUTH, json=payload)
    assert response.status_code == 422
    assert "secret-password" not in response.text
    metadata(response)


def test_invalid_json_and_scenario(client):
    response = client.post("/orders", headers={**AUTH, "Content-Type": "application/json"},
                           content='{"password":"secret-password",')
    assert response.status_code == 422
    assert "secret-password" not in response.text
    metadata(response)
    response = client.get("/health?demo_scenario=unknown")
    assert response.status_code == 422
    metadata(response)


def test_success_fixtures_and_no_mutation(client):
    assert client.get("/health").json()["data"] == {"status": "ok"}
    before = client.get("/inventory/1", headers=AUTH).json()["data"]
    assert before == {"id": "1", "name": "Demo keyboard", "quantity": 12}
    assert client.get("/customers/1", headers=AUTH).json()["data"]["id"] == "1"
    for _ in range(2):
        assert client.post("/orders", headers=AUTH, json=ORDER).json()["data"] == {
            "id": "order-demo-001", **ORDER, "status": "accepted"}
    assert client.get("/inventory/1", headers=AUTH).json()["data"] == before


def test_correlation_and_safe_logs(client, caplog):
    request_id = "12345678-1234-5678-1234-567812345678"
    response = client.post("/orders?password=secret-query", headers={
        "Authorization": "Bearer secret-access-token", "X-Request-ID": request_id},
        json={"password": "secret-password"})
    assert response.status_code == 401
    assert response.json()["request_id"] == request_id
    records = [json.loads(r.message) for r in caplog.records if r.name == "mock_api.requests"]
    assert records[-1]["correlation_id"] == request_id
    assert records[-1]["route"] == "/orders"
    assert records[-1]["status_code"] == 401
    for secret in ("secret-access-token", "secret-password", "secret-query"):
        assert secret not in json.dumps(records)


def test_untrusted_correlation_id_and_unmatched_route(client, caplog):
    response = client.get("/secret-path", headers={"X-Request-ID": "secret-token"})
    assert response.status_code == 404
    metadata(response)
    records = [json.loads(r.message) for r in caplog.records if r.name == "mock_api.requests"]
    assert records[-1]["route"] == "unmatched"
    assert "secret-token" not in json.dumps(records)
