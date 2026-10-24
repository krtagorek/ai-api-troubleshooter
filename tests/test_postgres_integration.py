"""Real PostgreSQL tests. Each test owns a freshly created disposable database.

TEST_DATABASE_URL must name a development database ending in _test, with a role
allowed to CREATE DATABASE. Never use production credentials for this suite.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from langgraph.types import interrupt

from data.generate_incidents import generate
from graph.state import initialize_state
from graph.workflow import build_workflow
from persistence.config import DatabaseSettings
from persistence.database import PersistenceError
from persistence.repository import InvestigationRepository
from persistence.runtime import InvestigationBusy, open_persistence
from persistence.service import InvestigationExecutionError, InvestigationService
from persistence.tables import IncidentRow, InvestigationRow, AuditEventRow, ReportRow
from tests.test_workflow import fixtures

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


def migrate(settings, *arguments):
    env = {**os.environ, "DATABASE_URL": settings.database_url.get_secret_value()}
    result = subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT,
                            env=env, capture_output=True, text=True)
    if result.returncode:
        # URLs/driver exceptions may include credentials. Don't echo raw stderr.
        raise AssertionError("Alembic migration command failed")
    return result.stdout


@pytest.fixture
def settings():
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL integration tests")
    try:
        url = make_url(raw)
    except Exception:
        pytest.fail("TEST_DATABASE_URL must be a valid PostgreSQL URL; credentials withheld", pytrace=False)
    if url.drivername != "postgresql" or not url.database or not url.database.endswith("_test"):
        pytest.fail("Integration tests require a PostgreSQL database name ending in _test")
    name = "phase8_" + uuid4().hex + "_test"
    try:
        with psycopg.connect(raw, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    except Exception:
        pytest.fail("PostgreSQL integration setup failed. Start PostgreSQL, check TEST_DATABASE_URL, "
                    "and use a development role with CREATE DATABASE permission. Credentials withheld.", pytrace=False)
    checkpoint = url.set(database=name).render_as_string(hide_password=False)
    configuration = DatabaseSettings(database_url=url.set(database=name, drivername="postgresql+asyncpg").render_as_string(hide_password=False),
                                     checkpoint_database_url=checkpoint)
    try:
        migrate(configuration, "upgrade", "head")
        yield configuration
    finally:
        try:
            with psycopg.connect(raw, autocommit=True, connect_timeout=5) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        except Exception:
            pytest.fail("PostgreSQL test cleanup failed. Restore the server connection and remove "
                        "the disposable phase8_*_test database. Credentials withheld.", pytrace=False)


def incident():
    record = generate()["development"][0].model_dump()
    record["request_metadata"]["headers"]["Authorization"] = "Bearer raw-test-token"
    record["response_body"]["email"] = "person@example.com"
    return record


def test_checkpoint_roundtrip_same_thread_after_reconnect(settings):
    """A fresh graph/connection resumes the persisted thread, not a new run."""
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    async def first_invocation():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            state = initialize_state(incident(), thread_id)
            await InvestigationRepository(runtime.database).create(state)
            agents, _ = fixtures()
            graph = build_workflow(agents=agents, checkpointer=runtime.saver,
                                   interrupt_before=["root_cause_analyst"])
            await graph.ainvoke(state, config)
            checkpoint = await runtime.saver.aget_tuple(config)
            assert checkpoint is not None
            assert checkpoint.config["configurable"]["thread_id"] == thread_id
            assert (await graph.aget_state(config)).next == ("root_cause_analyst",)
    asyncio.run(first_invocation())
    async def second_invocation():
        async with open_persistence(settings) as runtime:
            agents, calls = fixtures()
            graph = build_workflow(agents=agents, checkpointer=runtime.saver)
            assert (await graph.aget_state(config)).values["thread_id"] == thread_id
            result = await graph.ainvoke(None, config)
            assert result["status"] == "completed"
            assert calls["classifier"] == calls["investigator"] == 0
            assert calls["analyst"] == 1
            async with runtime.database.transaction() as session:
                assert await session.scalar(text("SELECT count(*) FROM app.incidents")) == 1
                count = await session.scalar(text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id=:thread"),
                                             {"thread": thread_id})
                assert count >= 2
    asyncio.run(second_invocation())


def test_migrations_schema_isolation_and_roundtrip(settings):
    async def run():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            async with runtime.database.transaction() as session:
                tables = (await session.execute(text(
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN ('app','langgraph')"))).all()
                assert ("app", "incidents") in tables
                assert ("langgraph", "checkpoints") in tables
                assert ("app", "checkpoints") not in tables
                assert ("langgraph", "incidents") not in tables
            # Setup is idempotent and uses the library's own migration tracking.
            await runtime.setup_checkpoints()
    asyncio.run(run())
    migrate(settings, "check")
    migrate(settings, "downgrade", "base")
    migrate(settings, "upgrade", "head")
    async def verify():
        async with open_persistence(settings) as runtime:
            async with runtime.database.transaction() as session:
                assert await session.scalar(text("SELECT count(*) FROM langgraph.checkpoint_migrations")) > 0
    asyncio.run(verify())


def test_application_transactions_retrieval_and_redaction(settings):
    async def run():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            repository = InvestigationRepository(runtime.database)
            state = initialize_state(incident(), str(uuid4()))
            identifier = await repository.create(state)
            state["sanitized_inputs"].logs.append({"message": "hello", "password": "raw-row-secret"})
            await repository.save(identifier, state)
            loaded = await repository.get(identifier)
            assert loaded.thread_id == state["thread_id"]
            previous = await repository.list_investigations(state["incident_id"])
            assert [row.investigation_id for row in previous] == [identifier]
            assert "raw-row-secret" not in json.dumps(loaded.state["sanitized_inputs"].model_dump())
            with pytest.raises(PersistenceError):
                await repository.create(state)  # Duplicate thread rolls back atomically.
            assert len(await repository.list_investigations(state["incident_id"])) == 1
            async with runtime.database.transaction() as session:
                rows = [await session.scalar(select(IncidentRow.payload)),
                        await session.scalar(select(InvestigationRow.diagnostic_state)),
                        await session.scalar(select(AuditEventRow.details))]
                assert "raw-test-token" not in json.dumps(rows)
                assert "person@example.com" not in json.dumps(rows)
            with pytest.raises(RuntimeError):
                async with runtime.database.transaction() as session:
                    session.add(IncidentRow(incident_id="rollback-only", payload={"password": "raw-rollback"}))
                    await session.flush()
                    raise RuntimeError("rollback")
            async with runtime.database.transaction() as session:
                assert await session.get(IncidentRow, "rollback-only") is None
            wrong = {**state, "thread_id": str(uuid4())}
            with pytest.raises(ValueError):
                await repository.save(identifier, wrong)
            assert (await repository.get(identifier)).thread_id == state["thread_id"]
    asyncio.run(run())


def test_success_reports_and_terminal_projection_recovery(settings):
    async def first_process():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            agents, _ = fixtures()
            service = InvestigationService(runtime, agents=agents)
            result = await service.start(incident())
            assert result.status == "completed"
            assert await service.repository.get_report(result.investigation_id)
            # Simulate a stale application projection after a separately committed checkpoint.
            stale = initialize_state(incident(), result.thread_id)
            await service.repository.save(result.investigation_id, stale, status="running")
            return result.investigation_id
    identifier = asyncio.run(first_process())
    async def recovered_process():
        async with open_persistence(settings) as runtime:
            agents, calls = fixtures()
            service = InvestigationService(runtime, agents=agents)
            result = await service.resume(identifier)
            assert result.status == "completed"
            assert not calls  # A terminal checkpoint is projected, never re-executed.
            async with runtime.database.transaction() as session:
                reports = list(await session.scalars(select(ReportRow).where(ReportRow.investigation_id == identifier)))
                assert len(reports) == 1
    asyncio.run(recovered_process())


def test_interrupt_resume_across_resource_lifecycles(settings):
    async def stop():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            agents, calls = fixtures()
            service = InvestigationService(runtime, agents=agents, interrupt_before=["root_cause_analyst"])
            row = await service.start(incident())
            assert row.status == "interrupted"
            assert calls["classifier"] == calls["investigator"] == 1
            assert calls["analyst"] == 0
            return row.investigation_id
    identifier = asyncio.run(stop())
    async def resume():
        async with open_persistence(settings) as runtime:
            agents, calls = fixtures()
            service = InvestigationService(runtime, agents=agents, interrupt_before=["root_cause_analyst"])
            row = await service.resume(identifier)
            assert row.status == "completed"
            assert calls["classifier"] == calls["investigator"] == 0
            assert calls["analyst"] == 1
    asyncio.run(resume())


def test_node_error_recovery_and_no_checkpoint_secret_leakage(settings):
    async def fail():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            agents, _ = fixtures()
            def broken(_):
                raise RuntimeError("unlabeled-sensitive-error")
            agents["root_cause_analyst"] = broken
            service = InvestigationService(runtime, agents=agents)
            with pytest.raises(InvestigationExecutionError) as captured:
                await service.start(incident())
            identifier = captured.value.investigation_id
            row = await service.repository.get(identifier)
            assert row.status == "interrupted"
            async with runtime.checkpoint_pool.connection() as connection:
                checkpoints = await (await connection.execute("SELECT checkpoint, metadata FROM checkpoints")).fetchall()
                writes = await (await connection.execute("SELECT blob FROM checkpoint_writes")).fetchall()
                blobs = await (await connection.execute("SELECT blob FROM checkpoint_blobs WHERE blob IS NOT NULL")).fetchall()
                raw = str(checkpoints) + str([bytes(item["blob"]) for item in writes + blobs])
                for secret in ("raw-test-token", "person@example.com", "unlabeled-sensitive-error"):
                    assert secret not in raw
            return identifier
    identifier = asyncio.run(fail())
    async def recover():
        async with open_persistence(settings) as runtime:
            agents, calls = fixtures()
            row = await InvestigationService(runtime, agents=agents).resume(identifier)
            assert row.status == "completed"
            assert calls["classifier"] == calls["investigator"] == 0
    asyncio.run(recover())


def test_thread_lock_and_missing_input_checkpoint_recovery(settings):
    async def run():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            agents, _ = fixtures()
            service = InvestigationService(runtime, agents=agents)
            row = await service.create(incident())
            async with runtime.thread_lock(row.thread_id):
                with pytest.raises(InvestigationBusy):
                    await service.resume(row.investigation_id)
            result = await service.resume(row.investigation_id)
            assert result.status == "completed"
    asyncio.run(run())


def test_dynamic_interrupt_response_is_sanitized_and_durable(settings):
    observed = []
    def agents_for_interrupt():
        agents, _ = fixtures()
        original = agents["classifier"]
        def classify(state):
            response = interrupt({"question": "Provide the sanitized diagnostic context"})
            observed.append(response)
            return original(state)
        agents["classifier"] = classify
        return agents
    async def stop():
        async with open_persistence(settings, setup_checkpoints=True) as runtime:
            service = InvestigationService(runtime, agents=agents_for_interrupt())
            row = await service.start(incident())
            assert row.status == "interrupted"
            return row.investigation_id
    identifier = asyncio.run(stop())
    async def resume():
        async with open_persistence(settings) as runtime:
            service = InvestigationService(runtime, agents=agents_for_interrupt())
            with pytest.raises(ValueError, match="response is required"):
                await service.resume(identifier)
            row = await service.resume(identifier, response={"password": "resume-secret", "detail": "Additional context"})
            assert row.status == "completed"
            assert observed == [{"password": "[REDACTED]", "detail": "Additional context"}]
            async with runtime.checkpoint_pool.connection() as connection:
                writes = await (await connection.execute("SELECT blob FROM checkpoint_writes")).fetchall()
                assert "resume-secret" not in str([bytes(item["blob"]) for item in writes])
    asyncio.run(resume())
