"""Versioned REST boundary over existing persistence and registry services."""
import asyncio
import logging
import os
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from typing import Literal

from fastapi import FastAPI, Depends, HTTPException, Query, Request, Path
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, text

from backend.auth import identity, authentication_from_env
from backend.streaming import router as streaming_router
from worker.queue import JobQueue, JobConflict
from backend.schemas import IncidentDetail, IncidentPage, RegistrationResponse
from models.diagnosis import FinalReport
from registry.models import ApiRegistration
from backend.execution import ExecutionReceipt, ExecutionUnavailable, UnavailableExecutionService
from graph.state import initialize_state
from models.incident import Incident
from persistence.config import DatabaseSettings
from persistence.database import PersistenceError
from persistence.repository import InvestigationRepository
from persistence.runtime import open_persistence
from persistence.tables import IncidentRow, InvestigationRow, ReportRow
from registry.authorization import AdminAuthorization
from registry.models import ApiPolicy
from registry.service import RegistryService, RegistryDenied, RegistryTransitionError

logger = logging.getLogger("troubleshooter.http")


class IncidentSubmission(Incident):
    # Server generates the identity; clients cannot claim another incident.
    incident_id: Literal[""] = ""
    service: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=2048)
    expected_behavior: str = Field(min_length=1, max_length=10000)
    actual_behavior: str = Field(min_length=1, max_length=10000)
    logs: list[dict] = Field(default_factory=list, max_length=1000)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    challenge: SecretStr = Field(min_length=1, max_length=256)


class SafeBoundary:
    """Bound bodies before JSON parsing, and log only controlled metadata."""
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            correlation = str(UUID(headers.get(b"x-correlation-id", b"").decode()))
        except Exception:
            correlation = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = correlation
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect": return
            size += len(message.get("body", b""))
            if size > (262144 if scope.get('path','').startswith('/api/v1/diagnostic-admin') else 1_048_576):
                return await JSONResponse({"detail": "Request body exceeds configured size limit", "correlation_id": correlation}, 413,
                    headers={"X-Correlation-ID": correlation})(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False): break
        async def replay(): return messages.pop(0) if messages else await receive()
        started = False
        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [(key, value) for key, value in message.get("headers", [])
                    if key.lower() not in {b"x-correlation-id", b"cache-control", b"x-content-type-options"}]
                message["headers"].extend([(b"x-correlation-id", correlation.encode()),
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")])
                logger.info(json.dumps({"event": "http_response", "correlation_id": correlation,
                    "status_code": message["status"], "timestamp": datetime.now(timezone.utc).isoformat()}))
            await send(message)
        try:
            await self.app(scope, replay, safe_send)
        except Exception:
            # Never let arbitrary driver/provider exceptions reach server logs.
            if started:
                raise RuntimeError("HTTP response interrupted") from None
            await JSONResponse({"detail": "Internal server error", "correlation_id": correlation}, 500)(scope, replay, safe_send)


def create_app(*, settings=None, runtime=None, authenticator=None, registry_factory=None,
               execution=None, admins=None, cors_origins=None, durable_queue=None, diagnostic_admin_factory=None):
    enabled = durable_queue if durable_queue is not None else os.getenv("DURABLE_WORKER_ENABLED", "false").lower() == "true"
    def configure_execution(app, connected):
        if enabled and execution is None:
            app.state.execution = JobQueue(connected.database, admins=app.state.admins)

    @asynccontextmanager
    async def lifespan(app):
        if runtime is not None:
            app.state.runtime = runtime
            configure_execution(app, runtime)
            yield
        else:
            async with open_persistence(settings or DatabaseSettings.from_env()) as connected:
                app.state.runtime = connected
                configure_execution(app, connected)
                yield

    app = FastAPI(title="AI API Integration Troubleshooter", version="1.0.0", lifespan=lifespan)
    app.state.diagnostic_admin_factory = diagnostic_admin_factory
    app.state.authenticator = authenticator if authenticator is not None else authentication_from_env()
    app.state.execution = execution or UnavailableExecutionService()
    app.state.admins = admins or AdminAuthorization.from_env()
    origins = cors_origins if cors_origins is not None else [v.strip() for v in os.getenv("CORS_ORIGINS", "").split(",") if v.strip()]
    if any(origin == "*" or not origin.startswith(("http://", "https://")) for origin in origins):
        raise ValueError("CORS_ORIGINS must contain explicit HTTP origins")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "Last-Event-ID"],
                       expose_headers=["X-Correlation-ID"])
    app.add_middleware(SafeBoundary)

    def database(request: Request): return request.app.state.runtime.database
    def registry(request: Request):
        db = database(request)
        return registry_factory(db) if registry_factory else RegistryService(db, admins=app.state.admins,
            environment=os.getenv("APP_ENV", "production"),
            development_mock_origin=os.getenv("REGISTRY_DEVELOPMENT_MOCK_ORIGIN", "http://127.0.0.1:8001"))

    async def error(request, status, detail):
        return JSONResponse({"detail": detail, "correlation_id": getattr(request.state, "correlation_id", None)}, status,
            headers={"X-Correlation-ID": getattr(request.state, "correlation_id", ""), "Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc): return await error(request, 422, "Invalid request payload or parameters")
    @app.exception_handler(RegistryDenied)
    async def denied(request, exc): return await error(request, 403, "Registry operation denied")
    @app.exception_handler(RegistryTransitionError)
    async def conflict(request, exc): return await error(request, 409, "Registry lifecycle or ownership verification requirements not satisfied")
    @app.exception_handler(PersistenceError)
    async def unavailable(request, exc): return await error(request, 503, "PostgreSQL unavailable; check connection configuration and migrations")
    @app.exception_handler(ExecutionUnavailable)
    async def no_runner(request, exc): return await error(request, 503, "No investigation runner is configured; execution has not started")
    @app.exception_handler(LookupError)
    async def missing(request, exc): return await error(request, 404, "Resource not found")
    @app.exception_handler(JobConflict)
    async def job_conflict(request, exc): return await error(request, 409, "Investigation lifecycle conflict")
    @app.exception_handler(ValueError)
    async def bad_value(request, exc): return await error(request, 422, "Request violates service policy")
    @app.exception_handler(Exception)
    async def unexpected(request, exc): return await error(request, 500, "Internal server error")

    @app.get("/health", tags=["health"])
    @app.get("/api/v1/health", tags=["health"])
    async def health(): return {"status": "ok", "service": "ai-api-troubleshooter"}

    @app.get("/api/v1/health/ready", tags=["health"])
    async def ready(request: Request):
        try:
            async with database(request).transaction() as session:
                await session.execute(text("SELECT owner_id FROM app.incidents LIMIT 0"))
                await session.execute(text("SELECT api_id FROM app.api_registry LIMIT 0"))
                if enabled:
                    await session.execute(text("SELECT job_id FROM app.jobs LIMIT 0"))
                    await session.execute(text("SELECT sequence FROM app.progress_events LIMIT 0"))
            return {"status": "ready"}
        except Exception:
            return await error(request, 503, "PostgreSQL unavailable or migrations missing; run alembic upgrade head")

    async def owned(request, actor, incident_id):
        async with database(request).transaction() as session:
            row = await session.get(IncidentRow, incident_id)
            if row is None or (row.owner_id != actor.subject_id and not app.state.admins.is_admin(actor)):
                raise HTTPException(404, "Incident not found")
            return row

    async def detail(request, actor, incident_id):
        from tools.output_safety import protect_incident_view
        row = await owned(request, actor, incident_id)
        investigations = await InvestigationRepository(database(request)).list_investigations(incident_id)
        return {"incident": protect_incident_view(row.payload).model_dump(mode="json"),
                "investigations": [{"investigation_id": item.investigation_id, "thread_id": item.thread_id,
                                    "status": item.status} for item in investigations]}

    @app.post("/api/v1/incidents", response_model=IncidentDetail, status_code=201, tags=["incidents"])
    async def create_incident(body: IncidentSubmission, request: Request, actor=Depends(identity)):
        record = body.model_dump(mode="json")
        record["incident_id"] = str(uuid4())
        state = initialize_state(record, str(uuid4()))
        from tools.output_safety import protect_incident_view
        protect_incident_view(state['sanitized_inputs'])
        await InvestigationRepository(database(request)).create(state, owner_id=actor.subject_id)
        return await detail(request, actor, record["incident_id"])

    @app.get("/api/v1/incidents", response_model=IncidentPage, tags=["incidents"])
    async def list_incidents(request: Request, actor=Depends(identity), limit: int = Query(50, ge=1, le=100),
                             offset: int = Query(0, ge=0, le=10000)):
        query = select(IncidentRow).order_by(IncidentRow.created_at.desc(), IncidentRow.incident_id).limit(limit).offset(offset)
        if not app.state.admins.is_admin(actor): query = query.where(IncidentRow.owner_id == actor.subject_id)
        async with database(request).transaction() as session:
            rows = await session.scalars(query)
            from tools.output_safety import protect_incident_view
            return {"items": [protect_incident_view(row.payload).model_dump(mode="json") for row in rows],
                    "limit": limit, "offset": offset}

    @app.get("/api/v1/incidents/{incident_id}", response_model=IncidentDetail, tags=["incidents"])
    async def get_incident(request: Request, incident_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"), actor=Depends(identity)):
        return await detail(request, actor, str(incident_id))

    @app.get("/api/v1/incidents/{incident_id}/report", response_model=FinalReport, tags=["incidents"])
    async def report(request: Request, incident_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"), actor=Depends(identity)):
        await owned(request, actor, str(incident_id))
        async with database(request).transaction() as session:
            row = await session.scalar(select(ReportRow).join(InvestigationRow).where(
                InvestigationRow.incident_id == str(incident_id)).order_by(ReportRow.created_at.desc(), ReportRow.report_id).limit(1))
            if row is None: raise HTTPException(409, "Report is not available yet")
            from tools.output_safety import protect_report
            return protect_report(row.payload)

    @app.post("/api/v1/investigations/{investigation_id}/execute", response_model=ExecutionReceipt,
              status_code=202, tags=["investigations"], responses={503: {"description": "No runner configured; nothing queued"}})
    async def execute(investigation_id: UUID, request: Request, actor=Depends(identity)):
        item = await InvestigationRepository(database(request)).get(investigation_id)
        await owned(request, actor, item.incident_id)
        if item.status not in {"pending", "interrupted", "awaiting_evidence"}:
            if not isinstance(app.state.execution, JobQueue):
                raise HTTPException(409, "Investigation cannot be submitted in its current state")
        try:
            return await asyncio.wait_for(app.state.execution.submit(investigation_id, principal=actor,
                correlation_id=request.state.correlation_id), timeout=3)
        except TimeoutError:
            raise HTTPException(503, "Runner acceptance could not be confirmed; check investigation status before retrying") from None

    @app.post("/api/v1/registry", response_model=RegistrationResponse, status_code=201, tags=["registry"])
    async def register(body: ApiPolicy, request: Request, actor=Depends(identity)):
        result = await registry(request).register(actor, body)
        return {"registration": result.registration.model_dump(mode="json"), "verification_challenge": result.challenge.get_secret_value()}

    @app.post("/api/v1/registry/development-mock", response_model=RegistrationResponse, status_code=201, tags=["registry"])
    async def development_mock(request: Request, actor=Depends(identity)):
        result = await registry(request).register_development_mock(actor)
        return {"registration": result.registration.model_dump(mode="json"), "verification_challenge": result.challenge.get_secret_value()}

    @app.get("/api/v1/registry", response_model=list[ApiRegistration], tags=["registry"])
    async def list_registry(request: Request, actor=Depends(identity), limit: int = Query(50, ge=1, le=100)):
        return await registry(request).list_registrations(actor, limit=limit)

    @app.get("/api/v1/registry/{api_id}", response_model=ApiRegistration, tags=["registry"])
    async def get_registry(api_id: UUID, request: Request, actor=Depends(identity)):
        return await registry(request).get(actor, api_id)

    @app.put("/api/v1/registry/{api_id}/policy", response_model=RegistrationResponse, tags=["registry"])
    async def update_policy(api_id: UUID, body: ApiPolicy, request: Request, actor=Depends(identity)):
        result = await registry(request).update_policy(actor, api_id, body)
        return {"registration": result.registration.model_dump(mode="json"), "verification_challenge": result.challenge.get_secret_value()}

    @app.post("/api/v1/registry/{api_id}/verify", response_model=ApiRegistration, tags=["registry"])
    async def verify(api_id: UUID, body: Verification, request: Request, actor=Depends(identity)):
        return await registry(request).verify(actor, api_id, body.challenge.get_secret_value())

    # Fixed actions prevent callers from requesting arbitrary state transitions.
    def transition_endpoint(target):
        async def endpoint(api_id: UUID, request: Request, actor=Depends(identity)):
            return await registry(request).transition(actor, api_id, target)
        return endpoint
    for action, target in (("approve", "approved"), ("suspend", "suspended"), ("revoke", "revoked")):
        app.add_api_route(f"/api/v1/registry/{{api_id}}/{action}", transition_endpoint(target),
                         methods=["POST"], response_model=ApiRegistration, tags=["registry"], name=action)

    @app.get("/api/v1/registry/{api_id}/history", tags=["registry"])
    async def history(api_id: UUID, request: Request, actor=Depends(identity)):
        return await registry(request).history(actor, api_id)

    from backend.diagnostic_admin import router as diagnostic_router
    from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
    from sqlalchemy.exc import IntegrityError
    @app.exception_handler(DiagnosticDenied)
    async def diagnostic_denied(request,exc):return await error(request,403,'Diagnostic administration operation denied')
    @app.exception_handler(DiagnosticConflict)
    async def diagnostic_conflict(request,exc):return await error(request,409,'Diagnostic version or lifecycle conflict')
    @app.exception_handler(IntegrityError)
    async def diagnostic_integrity(request,exc):return await error(request,409 if request.url.path.startswith('/api/v1/diagnostic-admin') else 500,'Database operation could not be completed')
    app.include_router(diagnostic_router)
    app.include_router(streaming_router)
    return app
