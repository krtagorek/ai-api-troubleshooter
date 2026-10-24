"""Persistence boundaries that do not require a running database."""

import json
import os
from pathlib import Path
import subprocess
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt, Command

from data.generate_incidents import generate
from graph.state import initialize_state
from graph.workflow import build_workflow
from persistence.checkpoints import SanitizedSerializer, safe_config
from persistence.config import DatabaseSettings
from persistence.tables import Base
from persistence import runtime as persistence_runtime
from tests.test_workflow import fixtures


def test_safe_serializer_preserves_models_interrupts_and_redacts():
    state = initialize_state(generate()["test"][0], str(uuid4()))
    state["sanitized_inputs"].logs.append({"password": "raw-password", "message": "Email person@example.com"})
    serializer = SanitizedSerializer()
    encoded = serializer.dumps_typed({"state": state, "interrupt": (Interrupt(value={"token": "raw-token"}),)})
    assert b"raw-password" not in encoded[1]
    assert b"raw-token" not in encoded[1]
    assert b"person@example.com" not in encoded[1]
    decoded = serializer.loads_typed(encoded)
    assert decoded["state"]["sanitized_inputs"].incident_id == state["incident_id"]
    assert decoded["interrupt"][0].value == {"token": "[REDACTED]"}
    payload = {"tag": "model", "module": "os", "__domain_model__": "malicious"}
    assert serializer.loads_typed(serializer.dumps_typed(payload)) == payload
    with pytest.raises(ValueError):
        serializer.loads_typed(("pickle", b"not-safe"))
    with pytest.raises(TypeError):
        serializer.dumps_typed(RuntimeError("raw-error"))


def test_configuration_redaction_and_schema_ownership():
    settings = DatabaseSettings(database_url="postgresql+asyncpg://user:hidden@localhost/db",
        checkpoint_database_url="postgresql://user:hidden@localhost/db")
    assert "hidden" not in str(settings)
    assert all(table.schema == "app" for table in Base.metadata.tables.values())
    assert set(Base.metadata.tables) == {"app.incidents", "app.investigations", "app.reports", "app.audit_events",
                                         "app.api_registry", "app.api_registry_events", "app.jobs", "app.progress_events", "app.diagnostic_contracts", "app.diagnostic_contract_versions", "app.diagnostic_contract_events", "app.diagnostic_memberships", "app.diagnostic_endpoint_tenants", "app.diagnostic_attestations", "app.diagnostic_authority_events", "app.diagnostic_ingestion_grants", "app.diagnostic_ingestion_envelopes", "app.diagnostic_authorization_fences", "app.diagnostic_authorization_permits", "app.diagnostic_permit_consumptions"}
    with pytest.raises(ValueError):
        DatabaseSettings(database_url="sqlite:///data", checkpoint_database_url="postgresql://localhost/db")
    with pytest.raises(ValueError):
        safe_config({"configurable": {"thread_id": "Bearer raw-token"}})


def test_guard_resume_does_not_restart_completed_nodes():
    agents, calls = fixtures()
    state = initialize_state(generate()["test"][0], str(uuid4()))
    graph = build_workflow(agents=agents, checkpointer=InMemorySaver(), interrupt_before=["root_cause_analyst"])
    config = {"configurable": {"thread_id": state["thread_id"]}}
    graph.invoke(state, config)
    assert graph.get_state(config).next == ("root_cause_analyst",)
    assert calls["classifier"] == calls["investigator"] == 1
    result = graph.invoke(None, config)
    assert result["status"] == "completed"
    assert calls["classifier"] == calls["investigator"] == 1
    with pytest.raises(ValueError, match="thread"):
        graph.invoke(state, {"configurable": {"thread_id": str(uuid4())}})
    with pytest.raises(ValueError, match="routing"):
        graph.invoke(Command(goto="final_report"), config)


def test_offline_migration_contains_no_credentials_or_checkpoint_tables():
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        env={**os.environ, "DATABASE_URL": "postgresql+asyncpg://user:offline-secret@localhost/db"},
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, "Offline migration failed"
    assert "offline-secret" not in result.stdout
    assert "CREATE TABLE app.incidents" in result.stdout
    assert "CREATE TABLE langgraph" not in result.stdout


def test_database_settings_read_environment_without_exposing_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:env-sensitive@localhost/db")
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "postgresql://user:env-sensitive@localhost/db")
    settings = DatabaseSettings.from_env()
    assert settings.database_url.get_secret_value() == os.environ["DATABASE_URL"]
    assert "env-sensitive" not in repr(settings)
    assert "env-sensitive" not in settings.model_dump_json()
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValueError, match="must be configured"):
        DatabaseSettings.from_env()


@pytest.mark.parametrize("failed_destination", ["application", "checkpoint"])
def test_unavailable_database_has_safe_actionable_error_and_closes_resources(monkeypatch, failed_destination):
    settings = DatabaseSettings(database_url="postgresql+asyncpg://user:unavailable-secret@localhost/db",
        checkpoint_database_url="postgresql://user:unavailable-secret@localhost/db")
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=SimpleNamespace(execute=AsyncMock()))
    connection_context.__aexit__ = AsyncMock(return_value=False)
    runtime = SimpleNamespace(
        database=SimpleNamespace(engine=SimpleNamespace(connect=lambda: connection_context), close=AsyncMock()),
        checkpoint_pool=SimpleNamespace(open=AsyncMock(), close=AsyncMock()),
        lock_pool=SimpleNamespace(open=AsyncMock(), close=AsyncMock()))
    monkeypatch.setattr(persistence_runtime, "PersistenceRuntime", lambda _: runtime)
    error = OSError("driver failure containing unavailable-secret")
    if failed_destination == "application":
        connection_context.__aenter__.side_effect = error
    else:
        monkeypatch.setattr(persistence_runtime.AsyncConnection, "connect", AsyncMock(side_effect=error))
    async def run():
        with pytest.raises(persistence_runtime.DatabaseUnavailableError) as captured:
            async with persistence_runtime.open_persistence(settings):
                pytest.fail("Unavailable databases must not yield a runtime")
        message = str(captured.value)
        assert "Start your PostgreSQL server" in message
        assert "DATABASE_URL" in message
        assert "unavailable-secret" not in message
        assert captured.value.__suppress_context__
        runtime.database.close.assert_awaited_once()
        runtime.checkpoint_pool.close.assert_awaited_once()
        runtime.lock_pool.close.assert_awaited_once()
        runtime.checkpoint_pool.open.assert_not_awaited()
    asyncio.run(run())
