"""Deterministic, demo-only API. Never use its authentication in production."""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from tools.redaction import install_redaction_filters
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Demo-only Troubleshooter Mock API")
install_redaction_filters()
logger = logging.getLogger("mock_api.requests")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = True

INVENTORY = {"1": {"id": "1", "name": "Demo keyboard", "quantity": 12}}
CUSTOMERS = {"1": {"id": "1", "name": "Demo Customer"}}


class Scenario(str, Enum):
    success = "success"
    unauthorized = "unauthorized"
    forbidden = "forbidden"
    not_found = "not_found"
    invalid_payload = "invalid_payload"
    rate_limit = "rate_limit"
    internal_error = "internal_error"
    service_unavailable = "service_unavailable"
    timeout = "timeout"
    schema_mismatch = "schema_mismatch"


FAILURES = {
    Scenario.unauthorized: (401, "Token missing or expired"),
    Scenario.forbidden: (403, "Insufficient permissions"),
    Scenario.not_found: (404, "Requested resource not found"),
    Scenario.invalid_payload: (422, "Invalid request payload (demo scenario)"),
    Scenario.rate_limit: (429, "Rate limit exceeded"),
    Scenario.internal_error: (500, "Internal server error (demo scenario)"),
    Scenario.service_unavailable: (503, "Dependent service unavailable"),
    Scenario.timeout: (504, "Simulated upstream timeout"),
}


def envelope(request: Request, **payload: object) -> dict:
    return {"request_id": request.state.request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(), **payload}


@app.middleware("http")
async def correlate_and_log(request: Request, call_next):
    # Only UUIDs may enter logs from caller-supplied headers.
    try:
        request_id = str(UUID(request.headers.get("X-Request-ID", "")))
    except ValueError:
        request_id = str(uuid4())
    request.state.request_id = request_id
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    route = request.scope.get("route")
    logger.info(json.dumps({
        "event": "http_request", "request_id": request_id,
        "correlation_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "route": getattr(route, "path", "unmatched"),
        "status_code": response.status_code,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }))
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content=envelope(request, error={"message": exc.detail}),
                        headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo validation inputs: payloads may contain secrets.
    return JSONResponse(status_code=422, content=envelope(
        request, error={"message": "Invalid request payload or parameters"}))


def select_scenario(
    demo_scenario: Annotated[Scenario, Query(
        description="DEMO ONLY: force a deterministic response; never use in production."
    )] = Scenario.success,
) -> Scenario:
    if demo_scenario in FAILURES:
        status, message = FAILURES[demo_scenario]
        headers = {"Retry-After": "30"} if status == 429 else None
        if status == 401:
            headers = {"WWW-Authenticate": "Bearer"}
        raise HTTPException(status, message, headers=headers)
    return demo_scenario


def demo_auth(authorization: Annotated[str | None, Header()] = None):
    """Fixed public demo credentials; no real token verification."""
    if authorization == "Bearer demo-readonly-token":
        raise HTTPException(403, "Insufficient permissions")
    if authorization != "Bearer demo-token":
        raise HTTPException(401, "Token missing or expired",
                            headers={"WWW-Authenticate": "Bearer"})


SelectedScenario = Annotated[Scenario, Depends(select_scenario)]


def success(request: Request, scenario: Scenario, data: dict) -> dict:
    if scenario == Scenario.schema_mismatch:
        # Intentionally violates the documented data shape while returning 200.
        return envelope(request, data={"unexpected_field": "demo schema mismatch"})
    return envelope(request, data=data)


@app.get("/health")
def health(request: Request, scenario: SelectedScenario):
    return success(request, scenario, {"status": "ok"})


@app.get("/inventory/{id}", dependencies=[Depends(demo_auth)])
def inventory(id: str, request: Request, scenario: SelectedScenario):
    if id not in INVENTORY:
        raise HTTPException(404, "Inventory item not found")
    return success(request, scenario, INVENTORY[id])


@app.get("/customers/{id}", dependencies=[Depends(demo_auth)])
def customer(id: str, request: Request, scenario: SelectedScenario):
    if id not in CUSTOMERS:
        raise HTTPException(404, "Customer not found")
    return success(request, scenario, CUSTOMERS[id])


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inventory_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


@app.post("/orders", dependencies=[Depends(demo_auth)])
def order(payload: Order, request: Request, scenario: SelectedScenario):
    if payload.inventory_id not in INVENTORY:
        raise HTTPException(404, "Inventory item not found")
    if payload.customer_id not in CUSTOMERS:
        raise HTTPException(404, "Customer not found")
    return success(request, scenario, {"id": "order-demo-001",
                                      **payload.model_dump(), "status": "accepted"})
