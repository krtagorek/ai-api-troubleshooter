"""Atomic application projections. Checkpoints are managed independently."""

import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from graph.state import deserialize_state, serialize_state, validate_state
from models.diagnosis import FinalReport
from persistence.tables import IncidentRow, InvestigationRow, ReportRow, AuditEventRow, JobRow
from tools.redaction import redact


@dataclass(frozen=True)
class Investigation:
    investigation_id: UUID
    incident_id: str
    thread_id: str
    status: str
    state: dict


def view(row):
    return Investigation(row.investigation_id, row.incident_id, row.thread_id, row.status,
                         deserialize_state(json.dumps(row.diagnostic_state)))


class InvestigationRepository:
    def __init__(self, database):
        self.database = database

    async def create(self, state, *, owner_id=None):
        state = validate_state(state)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", state["incident_id"]):
            raise ValueError("Use an opaque incident identifier")
        thread_id = str(UUID(state["thread_id"]))
        if state["thread_id"] != thread_id:
            raise ValueError("Use a canonical UUID thread identifier")
        identifier = uuid4()
        async with self.database.transaction() as session:
            await session.execute(insert(IncidentRow).values(incident_id=state["incident_id"],
                payload=state["sanitized_inputs"].model_dump(mode="json"), owner_id=owner_id).on_conflict_do_nothing())
            incident = await session.scalar(select(IncidentRow).where(
                IncidentRow.incident_id == state["incident_id"]).with_for_update())
            if owner_id is not None and incident.owner_id != owner_id:
                raise PermissionError("Incident ownership mismatch")
            session.add(InvestigationRow(investigation_id=identifier, incident_id=state["incident_id"],
                thread_id=thread_id, status=state["status"], diagnostic_state=json.loads(serialize_state(state))))
            await session.flush()
            session.add(AuditEventRow(investigation_id=identifier, event_type="created", details={}))
        return identifier

    async def get(self, identifier):
        async with self.database.transaction() as session:
            row = await session.get(InvestigationRow, UUID(str(identifier)))
            if row is None:
                raise LookupError("Investigation not found")
            return view(row)

    async def list_investigations(self, incident_id, *, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")
        async with self.database.transaction() as session:
            rows = await session.scalars(select(InvestigationRow).where(
                InvestigationRow.incident_id == incident_id).order_by(
                InvestigationRow.created_at.desc(), InvestigationRow.investigation_id).limit(limit))
            return [view(row) for row in rows]

    async def save(self, identifier, state, *, status=None, event_type="state_saved", publication=None):
        state = validate_state(state)
        status = status or state["status"]
        if status not in {"pending", "running", "interrupted", "awaiting_evidence", "completed", "failed"}:
            raise ValueError("Unknown investigation status")
        if event_type not in {"state_saved", "execution_started", "execution_stopped", "execution_failed", "recovered"}:
            raise ValueError("Unknown audit event")
        async with self.database.transaction() as session:
            row = await session.scalar(select(InvestigationRow).where(
                InvestigationRow.investigation_id == UUID(str(identifier))).with_for_update())
            if row is None:
                raise LookupError("Investigation not found")
            if await session.scalar(select(JobRow.job_id).where(JobRow.investigation_id == row.investigation_id)):
                raise ValueError("Durable job projections must use the fenced job lifecycle")
            if (row.thread_id, row.incident_id) != (state["thread_id"], state["incident_id"]):
                raise ValueError("State identity does not match investigation")
            from diagnostic_contracts.integration import check_publication
            state=await check_publication(session,state,publication)
            if state['stop_reason']=='report_publication_denied':status=state['status']
            row.diagnostic_state, row.status = json.loads(serialize_state(state)), status
            if state["final_report"]:
                payload = redact(state["final_report"].model_dump(mode="json"))
                await session.execute(insert(ReportRow).values(report_id=uuid4(), investigation_id=row.investigation_id,
                    payload=payload).on_conflict_do_nothing(index_elements=[ReportRow.investigation_id]))
            session.add(AuditEventRow(investigation_id=row.investigation_id, event_type=event_type,
                details={"status": status, "stop_reason": state["stop_reason"]}))

    async def get_report(self, identifier):
        async with self.database.transaction() as session:
            row = await session.scalar(select(ReportRow).where(ReportRow.investigation_id == UUID(str(identifier))))
            from tools.output_safety import protect_report
            return protect_report(row.payload) if row else None
