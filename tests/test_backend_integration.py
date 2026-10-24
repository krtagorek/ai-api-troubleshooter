"""Real migrated PostgreSQL REST tests; no LLM or external network calls."""
import asyncio
from uuid import UUID, uuid4
import httpx
import pytest
from sqlalchemy import select
from backend.app import create_app
from backend.execution import ExecutionReceipt
from registry.authorization import AdminAuthorization, Principal
from registry.service import RegistryService
from persistence.runtime import open_persistence
from persistence.tables import IncidentRow, ReportRow
from tests.test_postgres_integration import settings
from data.generate_incidents import generate

pytestmark = pytest.mark.integration


class Identities:
    def __init__(self): self.subjects = {key: Principal(uuid4()) for key in ("owner", "other", "admin")}
    async def authenticate(self, token): return self.subjects.get(token)


def payload():
    value = generate()["development"][0].model_dump(mode="json")
    for field in ("incident_id", "root_cause", "expected_resolution"): value.pop(field, None)
    value["request_metadata"] = {"Authorization": "Bearer never-store-this-token", "password": "never-store-password"}
    return value


def headers(who="owner"): return {"Authorization": f"Bearer {who}"}


def test_incident_api_persistence_isolation_and_execution(settings):
    async def run():
        identities = Identities()
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            app = create_app(runtime=runtime, authenticator=identities,
                admins=AdminAuthorization(frozenset({identities.subjects["admin"].subject_id})))
            async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/api/v1/health/ready")).status_code == 200
                response = await client.post("/api/v1/incidents", json=payload(), headers=headers())
                assert response.status_code == 201, response.text
                body = response.json()
                identifier = body["incident"]["incident_id"]
                investigation = body["investigations"][0]
                assert investigation["status"] == "pending"
                UUID(investigation["thread_id"])
                assert "never-store" not in response.text
                assert (await client.get(f"/api/v1/incidents/{identifier}", headers=headers())).status_code == 200
                assert (await client.get(f"/api/v1/incidents/{identifier}", headers=headers("other"))).status_code == 404
                assert (await client.get("/api/v1/incidents", headers=headers("other"))).json()["items"] == []
                assert len((await client.get("/api/v1/incidents", headers=headers("admin"))).json()["items"]) == 1
                assert (await client.get(f"/api/v1/incidents/{identifier}/report", headers=headers())).status_code == 409
                execute = f"/api/v1/investigations/{investigation['investigation_id']}/execute"
                assert (await client.post(execute, headers=headers("other"))).status_code == 404
                result = await client.post(execute, headers=headers())
                assert result.status_code == 503 and "not started" in result.text
                async with runtime.database.transaction() as session:
                    row = await session.get(IncidentRow, identifier)
                    assert row.owner_id == identities.subjects["owner"].subject_id
                    assert "never-store" not in str(row.payload)
                    # Simulate an already persisted worker report; HTTP never runs the graph.
                    session.add(ReportRow(investigation_id=UUID(investigation["investigation_id"]), payload={"incident_id": identifier, "summary": "Saved worker result", "diagnosis": {"summary": "Insufficient evidence"}, "evidence": {}}))
                assert (await client.get(f"/api/v1/incidents/{identifier}/report", headers=headers())).json()["summary"] == "Saved worker result"
                class Runner:
                    async def submit(self, identifier, **kwargs):
                        self.accepted = identifier
                        return ExecutionReceipt(job_id=uuid4())
                runner = Runner()
                app.state.execution = runner
                assert (await client.post(execute, headers=headers())).status_code == 202
                assert runner.accepted == UUID(investigation["investigation_id"])
    asyncio.run(run())


def test_registry_api_reuses_approval_policy(settings):
    async def run():
        identities = Identities()
        admins = AdminAuthorization(frozenset({identities.subjects["admin"].subject_id}))
        async with open_persistence(settings) as runtime:
            factory = lambda db: RegistryService(db, admins=admins, environment="development", resolver=lambda *_: ["127.0.0.1"])
            app = create_app(runtime=runtime, authenticator=identities, admins=admins, registry_factory=factory)
            async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.post("/api/v1/registry/development-mock", headers=headers())).status_code == 403
                result = await client.post("/api/v1/registry/development-mock", headers=headers("admin"))
                assert result.status_code == 201, result.text
                body = result.json()
                prefix = "/api/v1/registry/" + body["registration"]["api_id"]
                assert (await client.post(prefix + "/approve", headers=headers("admin"))).status_code == 409
                result = await client.post(prefix + "/verify", headers=headers("admin"), json={"challenge": body["verification_challenge"]})
                assert result.status_code == 200, result.text
                assert (await client.post(prefix + "/approve", headers=headers())).status_code == 403
                assert (await client.post(prefix + "/approve", headers=headers("admin"))).json()["status"] == "approved"
                assert (await client.get(prefix, headers=headers("admin"))).status_code == 200
                assert len((await client.get("/api/v1/registry", headers=headers("admin"))).json()) == 1
                policy = body["registration"]["policy"]
                policy["operations"].append({"operation_id": "inventory", "method": "GET", "path": "/inventory/1"})
                updated = await client.put(prefix + "/policy", json=policy, headers=headers("admin"))
                assert updated.status_code == 200
                assert updated.json()["registration"]["status"] == "pending"
                assert (await client.post(prefix + "/approve", headers=headers("admin"))).status_code == 409
                assert (await client.post(prefix + "/verify", headers=headers("admin"), json={"challenge": updated.json()["verification_challenge"]})).status_code == 200
                assert (await client.post(prefix + "/approve", headers=headers("admin"))).status_code == 200
                assert (await client.post(prefix + "/suspend", headers=headers("admin"))).json()["status"] == "suspended"
                assert (await client.post(prefix + "/revoke", headers=headers("admin"))).json()["status"] == "revoked"
                assert (await client.get(prefix + "/history", headers=headers())).status_code == 403
                events = (await client.get(prefix + "/history", headers=headers("admin"))).json()
                assert any(item["outcome"] == "denied" for item in events)
                assert "verification_challenge" not in str(events)
                # Strict schema validation rejects SSRF before DNS/network access.
                response = await client.post("/api/v1/registry", headers=headers(), json={"origin": "http://169.254.169.254", "operations": [{"operation_id": "read", "path": "/"}]})
                assert response.status_code == 422
    asyncio.run(run())


def test_legacy_report_output_safety_projection_preserves_database_original(settings):
    """Disposable database only: unsafe old narratives never leave the report API."""
    async def run():
        identities=Identities()
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            app=create_app(runtime=runtime,authenticator=identities,
                admins=AdminAuthorization(frozenset({identities.subjects['admin'].subject_id})))
            async with app.router.lifespan_context(app),httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                created=(await client.post('/api/v1/incidents',json=payload(),headers=headers())).json()
                ident=created['incident']['incident_id'];investigation=UUID(created['investigations'][0]['investigation_id'])
                unsafe="Evidence of the client's authentication credentials and their validation results."
                stored={'incident_id':ident,'summary':unsafe,'diagnosis':{'status':'inconclusive',
                    'summary':'Further diagnostic evidence needed',
                    'missing_evidence':[{'description':unsafe,'required_to_resolve':unsafe}]},
                    'evidence':{'missing':[{'description':unsafe,'required_to_resolve':unsafe}]},
                    'limitations':['Attach session cookies']}
                async with runtime.database.transaction() as session:
                    session.add(ReportRow(investigation_id=investigation,payload=stored))
                response=await client.get(f'/api/v1/incidents/{ident}/report',headers=headers())
                assert response.status_code==200
                assert unsafe not in response.text
                assert '[OUTPUT SAFETY:' in response.json()['summary']
                assert (await client.get(f'/api/v1/incidents/{ident}/report',headers=headers('other'))).status_code==404
                async with runtime.database.transaction() as session:
                    row=await session.scalar(select(ReportRow).where(ReportRow.investigation_id==investigation))
                    assert row.payload==stored  # Read-time projection does not rewrite historical storage.
    asyncio.run(run())
