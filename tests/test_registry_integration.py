"""Real PostgreSQL governance, authorization, auditing and tool integration."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from pydantic import BaseModel

from persistence.runtime import open_persistence
from persistence.database import PersistenceError
from persistence.tables import ApiRegistryRow, ApiRegistryEventRow
from registry.authorization import AdminAuthorization, Principal
from registry.execution import ApprovedApiExecutor
from registry.models import ApiPolicy, Operation
from registry.service import RegistryService, RegistryDenied, RegistryTransitionError
from tests.test_postgres_integration import settings  # Shared disposable-PostgreSQL fixture.
from tests.test_registry import policy
from tools import api_client

pytestmark = pytest.mark.integration


class PublishedProof:
    """Trusted TEST adapter only; a deployed verifier must check real ownership."""
    async def verify(self, origin, challenge):
        return origin == "https://api.example.com" and bool(challenge)


def service(runtime, *, environment="production", verifier=True, resolver=None):
    owner, admin = Principal(uuid4()), Principal(uuid4())
    result = RegistryService(runtime.database, admins=AdminAuthorization(frozenset({admin.subject_id})),
        ownership_verifier=PublishedProof() if verifier else None,
        resolver=resolver or (lambda *_: {"93.184.216.34"}), environment=environment)
    return result, owner, admin


async def approved(svc, owner, admin):
    challenge = await svc.register(owner, policy())
    identifier = challenge.registration.api_id
    await svc.verify(owner, identifier, challenge.challenge)
    return await svc.transition(admin, identifier, "approved")


def test_lifecycle_admin_authorization_audit_and_hash_only_proof(settings):
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime)
            challenge = await svc.register(owner, policy())
            identifier = challenge.registration.api_id
            assert challenge.registration.status == "pending"
            with pytest.raises(RegistryDenied):
                await svc.transition(owner, identifier, "approved")
            with pytest.raises(RegistryTransitionError):
                await svc.transition(admin, identifier, "approved")
            assert (await svc.verify(owner, identifier, challenge.challenge)).status == "verified"
            assert (await svc.transition(admin, identifier, "approved")).status == "approved"
            assert (await svc.transition(admin, identifier, "suspended")).status == "suspended"
            with pytest.raises(RegistryTransitionError):
                await svc.transition(admin, identifier, "approved")
            assert (await svc.transition(admin, identifier, "revoked")).status == "revoked"
            with pytest.raises(RegistryDenied):
                await svc.update_policy(owner, identifier, policy())
            history = await svc.history(admin, identifier)
            assert {"registered", "verified", "approved", "suspended", "revoked"} <= {entry["action"] for entry in history}
            assert any(item["outcome"] == "denied" for item in history)
            assert challenge.challenge.get_secret_value() not in repr(history)
            async with runtime.database.transaction() as session:
                row = await session.get(ApiRegistryRow, identifier)
                assert row.challenge_digest != challenge.challenge.get_secret_value()
                assert len(row.challenge_digest) == 64
            with pytest.raises(RegistryDenied):
                await svc.history(owner, identifier)
            with pytest.raises(PersistenceError):
                async with runtime.database.transaction() as session:
                    await session.execute(text("UPDATE app.api_registry_events SET reason='ok' WHERE api_id=:api"), {"api": identifier})
            assert len(await svc.history(admin, identifier)) >= len(history)
            assert [item.api_id for item in await svc.list_registrations(owner)] == [identifier]
            assert await svc.list_registrations(Principal(uuid4())) == []
    asyncio.run(run())


def test_ownership_verifier_is_required_and_challenges_expire(settings):
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime, verifier=False)
            challenge = await svc.register(owner, policy())
            with pytest.raises(RegistryDenied):
                await svc.verify(owner, challenge.registration.api_id, challenge.challenge)
            svc.ownership_verifier = PublishedProof()
            with pytest.raises(RegistryDenied):
                await svc.verify(owner, challenge.registration.api_id, "self-claimed-proof")
            async with runtime.database.transaction() as session:
                row = await session.get(ApiRegistryRow, challenge.registration.api_id)
                row.challenge_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with pytest.raises(RegistryDenied):
                await svc.verify(owner, challenge.registration.api_id, challenge.challenge)
            assert (await svc.get(owner, challenge.registration.api_id)).status == "pending"
    asyncio.run(run())


def test_execution_rechecks_approval_owner_revision_and_revocation(settings, monkeypatch):
    calls = []
    def respond(_transport, request):
        calls.append(request)
        return httpx.Response(200, json={"status": "ok", "password": "must-not-leak"})
    monkeypatch.setattr(api_client.PinnedTransport, "handle_request", respond)
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime)
            registration = await svc.register(owner, policy())
            identifier = registration.registration.api_id
            executor = ApprovedApiExecutor(svc, principal=owner)
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
            assert not calls
            await svc.verify(owner, identifier, registration.challenge)
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
            await svc.transition(admin, identifier, "approved")
            assert (await executor.aexecute(identifier, "other")).error == "blocked"
            outsider = ApprovedApiExecutor(svc, principal=Principal(uuid4()))
            assert (await outsider.aexecute(identifier, "health")).error == "blocked"
            result = await executor.aexecute(identifier, "health")
            assert result.ok and result.body["password"] == "[REDACTED]"
            assert calls[-1].url == "https://api.example.com/health"
            assert "authorization" not in calls[-1].headers
            replacement = ApiPolicy(origin="https://api.example.com", operations=(Operation(operation_id="health", path="/v2/health"),))
            changed = await svc.update_policy(owner, identifier, replacement)
            assert changed.registration.status == "pending"
            assert changed.registration.revision == 2
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
            await svc.verify(owner, identifier, changed.challenge)
            await svc.transition(admin, identifier, "approved")
            assert (await executor.aexecute(identifier, "health")).ok
            await svc.transition(admin, identifier, "revoked")
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
            assert len(calls) == 2
    asyncio.run(run())


def test_dns_changes_and_redirects_cannot_bypass_approval(settings, monkeypatch):
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime)
            item = await approved(svc, owner, admin)
            executor = ApprovedApiExecutor(svc, principal=owner)
            svc.resolver = lambda *_: {"169.254.169.254"}
            assert (await executor.aexecute(item.api_id, "health")).error == "blocked"
            answers = iter([{"93.184.216.34"}, {"127.0.0.1"}])
            svc.resolver = lambda *_: next(answers)
            assert (await executor.aexecute(item.api_id, "health")).error == "blocked"
            svc.resolver = lambda *_: {"93.184.216.34"}
            requests = []
            def redirect(_transport, request):
                requests.append(request)
                return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})
            monkeypatch.setattr(api_client.PinnedTransport, "handle_request", redirect)
            assert (await executor.aexecute(item.api_id, "health")).error == "blocked"
            assert len(requests) == 1
    asyncio.run(run())


def test_local_mock_is_development_only_and_still_requires_approval(settings, monkeypatch):
    monkeypatch.setattr(api_client.PinnedTransport, "handle_request", lambda *_: httpx.Response(200, json={"status": "ok"}))
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime)
            with pytest.raises(RegistryDenied):
                await svc.register_development_mock(admin)
            svc.environment = "development"
            svc.resolver = lambda *_: {"127.0.0.1"}
            with pytest.raises(RegistryDenied):
                await svc.register_development_mock(owner)
            registration = await svc.register_development_mock(admin)
            identifier = registration.registration.api_id
            executor = ApprovedApiExecutor(svc, principal=admin)
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
            await svc.verify(admin, identifier, registration.challenge)
            await svc.transition(admin, identifier, "approved")
            assert (await executor.aexecute(identifier, "health")).ok
            svc.environment = "production"
            assert (await executor.aexecute(identifier, "health")).error == "blocked"
    asyncio.run(run())


def test_registry_tools_integrate_with_async_workflow(settings, monkeypatch):
    from data.generate_incidents import generate
    from graph.state import initialize_state
    from graph.workflow import build_workflow
    from tests.test_workflow import fixtures
    monkeypatch.setattr(api_client.PinnedTransport, "handle_request", lambda *_: httpx.Response(200, json={"status": "ok"}))
    class Response(BaseModel):
        status: str
    async def run():
        async with open_persistence(settings) as runtime:
            svc, owner, admin = service(runtime)
            item = await approved(svc, owner, admin)
            executor = ApprovedApiExecutor(svc, principal=owner)
            state = initialize_state(generate()["test"][0], str(uuid4()))
            length = len(state["sanitized_inputs"].logs)
            agents, calls = fixtures(sufficient=lambda s: len(s["sanitized_inputs"].logs) > length)
            del agents["validator"]
            graph = build_workflow(agents=agents,
                evidence_tools={"registered_health": executor.evidence_tool(item.api_id, "health")},
                validation_tool=executor.validation_tool(item.api_id, "health", Response))
            result = await graph.ainvoke(state)
            assert result["status"] == "completed"
            assert calls["investigator"] == 2
            assert result["validation_results"][-1].status == "passed"
    asyncio.run(run())
