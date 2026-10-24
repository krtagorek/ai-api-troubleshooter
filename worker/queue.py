"""Transactional job state, PostgreSQL-clock leases and fencing tokens."""
from dataclasses import dataclass
from datetime import timedelta
import json
from uuid import UUID, uuid4

from sqlalchemy import select, func, or_, and_
from sqlalchemy.dialects.postgresql import insert

from backend.execution import ExecutionReceipt
from graph.state import serialize_state, validate_state
from persistence.tables import JobRow, ProgressEventRow, IncidentRow, InvestigationRow, ReportRow, AuditEventRow
from registry.authorization import AdminAuthorization, Principal
from tools.redaction import redact

TERMINAL = frozenset({"succeeded", "awaiting_evidence", "interrupted", "failed", "cancelled"})
NODES = frozenset({"initialize", "classifier", "log_investigator", "root_cause_analyst", "resolution_generator", "validator", "collect_evidence", "request_user_input", "retry_analysis", "validation_exhausted", "final_report"})
EVENTS = frozenset({"queued", "claimed", "node_completed", "retry_scheduled", "cancel_requested", *TERMINAL})


class LeaseLost(RuntimeError): pass
class JobConflict(ValueError): pass


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    investigation_id: UUID
    lease_id: UUID


class JobQueue:
    def __init__(self, database, *, admins=None, lease_seconds=30, max_attempts=3):
        if not 1 <= lease_seconds <= 300 or not 1 <= max_attempts <= 5:
            raise ValueError("Invalid lease or attempt budget")
        self.database, self.admins = database, admins or AdminAuthorization()
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts

    async def _authorized(self, session, identifier, principal, *, lock=False):
        if not isinstance(principal, Principal): raise PermissionError("Authenticated identity required")
        query = select(InvestigationRow).where(InvestigationRow.investigation_id == identifier)
        if lock: query = query.with_for_update()
        investigation = await session.scalar(query)
        incident = await session.get(IncidentRow, investigation.incident_id) if investigation else None
        if incident is None or (incident.owner_id != principal.subject_id and not self.admins.is_admin(principal)):
            raise LookupError("Investigation not found")
        return investigation

    @staticmethod
    def _event(session, job, event_type, *, node=None):
        if event_type not in EVENTS or (node is not None and node not in NODES):
            raise ValueError("Unknown progress event")
        job.event_sequence += 1
        # Only controlled progress codes; no raw LLM output, evidence, or exception text.
        payload = redact({"status": job.status, "attempt": job.attempts, **({"node": node} if node else {})})
        session.add(ProgressEventRow(job_id=job.job_id, sequence=job.event_sequence,
            event_type=event_type, payload=payload))

    async def submit(self, investigation_id, *, principal, correlation_id):
        identifier = UUID(str(investigation_id))
        async with self.database.transaction() as session:
            investigation = await self._authorized(session, identifier, principal, lock=True)
            existing = await session.scalar(select(JobRow).where(JobRow.investigation_id == identifier))
            if existing:
                return ExecutionReceipt(job_id=existing.job_id)
            if investigation.status not in {"pending", "interrupted", "awaiting_evidence"}:
                raise JobConflict("Investigation cannot be submitted")
            job = JobRow(job_id=uuid4(), investigation_id=identifier, status="queued", attempts=0,
                         max_attempts=self.max_attempts, event_sequence=0, cancel_requested=False)
            session.add(job)
            investigation.status = "queued"
            self._event(session, job, "queued")
            session.add(AuditEventRow(investigation_id=identifier, event_type="job_queued", details={"job_id": str(job.job_id)}))
            return ExecutionReceipt(job_id=job.job_id)

    async def claim(self):
        async with self.database.transaction() as session:
            job = await session.scalar(select(JobRow).where(or_(
                and_(JobRow.status == "queued", JobRow.available_at <= func.clock_timestamp()),
                and_(JobRow.status == "running", JobRow.lease_expires_at <= func.clock_timestamp())
            )).order_by(JobRow.available_at, JobRow.job_id).with_for_update(skip_locked=True).limit(1))
            if job is None: return None
            investigation = await session.get(InvestigationRow, job.investigation_id)
            if job.cancel_requested or job.attempts >= job.max_attempts:
                job.status = "cancelled" if job.cancel_requested else "failed"
                investigation.status = job.status
                job.lease_id = job.lease_expires_at = None
                self._event(session, job, job.status)
                return None
            job.attempts += 1
            job.status, investigation.status = "running", "running"
            job.lease_id = uuid4()
            job.lease_expires_at = func.clock_timestamp() + timedelta(seconds=self.lease_seconds)
            self._event(session, job, "claimed")
            return Claim(job.job_id, job.investigation_id, job.lease_id)

    async def _fenced(self, session, claim):
        job = await session.scalar(select(JobRow).where(JobRow.job_id == claim.job_id,
            JobRow.lease_id == claim.lease_id, JobRow.status == "running",
            JobRow.lease_expires_at > func.clock_timestamp()).with_for_update())
        if job is None: raise LeaseLost("Worker lease is no longer valid")
        return job

    async def heartbeat(self, claim):
        async with self.database.transaction() as session:
            job = await self._fenced(session, claim)
            job.lease_expires_at = func.clock_timestamp() + timedelta(seconds=self.lease_seconds)
            return job.cancel_requested

    async def progress(self, claim, node):
        async with self.database.transaction() as session:
            job = await self._fenced(session, claim)
            if job.cancel_requested: return False
            self._event(session, job, "node_completed", node=node)
            return True

    async def finish(self, claim, *, state=None, outcome=None, publication=None):
        clean = validate_state(state) if state else None
        async with self.database.transaction() as session:
            job = await self._fenced(session, claim)
            investigation = await session.get(InvestigationRow, job.investigation_id)
            if clean and (clean['incident_id'],clean['thread_id']) != (investigation.incident_id,investigation.thread_id):
                raise ValueError("State identity mismatch")
            if job.cancel_requested: outcome = "cancelled"
            if clean and not job.cancel_requested:
                from diagnostic_contracts.integration import check_publication
                clean=await check_publication(session,clean,publication)
                if clean['stop_reason']=='report_publication_denied':outcome=None
            if outcome is None:
                outcome = {"completed": "succeeded", "awaiting_evidence": "awaiting_evidence", "failed": "failed"}.get(clean["status"], "interrupted")
            if outcome not in TERMINAL: raise ValueError("Invalid terminal status")
            job.status = outcome
            investigation.status = "completed" if outcome == "succeeded" else outcome
            if clean:
                investigation.diagnostic_state = json.loads(serialize_state(clean))
                if clean["final_report"] and outcome != "cancelled":
                    await session.execute(insert(ReportRow).values(report_id=uuid4(),investigation_id=investigation.investigation_id,
                        payload=clean["final_report"].model_dump(mode="json")).on_conflict_do_nothing(
                            index_elements=[ReportRow.investigation_id]))
            job.lease_id = job.lease_expires_at = None
            self._event(session, job, outcome)
            session.add(AuditEventRow(investigation_id=investigation.investigation_id,event_type="job_finished",details={"status":outcome}))

    async def retry(self, claim):
        async with self.database.transaction() as session:
            job = await self._fenced(session, claim)
            investigation = await session.get(InvestigationRow, job.investigation_id)
            job.status = "cancelled" if job.cancel_requested else ("failed" if job.attempts >= job.max_attempts else "queued")
            investigation.status = job.status
            job.available_at = func.clock_timestamp() + timedelta(seconds=min(60,2 ** job.attempts))
            job.lease_id = job.lease_expires_at = None
            self._event(session, job, "retry_scheduled" if job.status == "queued" else job.status)

    async def cancel(self, identifier, *, principal):
        async with self.database.transaction() as session:
            await self._authorized(session, identifier, principal)
            job = await session.scalar(select(JobRow).where(JobRow.investigation_id == identifier).with_for_update())
            if job is None: raise LookupError("Job not found")
            if job.status not in TERMINAL and not job.cancel_requested:
                job.cancel_requested = True
                if job.status == "queued":
                    job.status = "cancelled"
                    investigation = await session.get(InvestigationRow, identifier)
                    investigation.status = "cancelled"
                self._event(session, job, "cancelled" if job.status == "cancelled" else "cancel_requested")
                session.add(AuditEventRow(investigation_id=identifier,event_type="job_cancel_requested",
                    details={"actor_id":str(principal.subject_id)}))
            return {"job_id": job.job_id, "status": job.status, "cancel_requested": job.cancel_requested}

    async def events(self, identifier, *, principal, after=0, expected_job=None):
        from tools.output_safety import progress_view
        async with self.database.transaction() as session:
            await self._authorized(session, identifier, principal)
            job = await session.scalar(select(JobRow).where(JobRow.investigation_id == identifier))
            if job is None: raise LookupError("Job not found")
            if after < 0 or after > job.event_sequence or (expected_job and expected_job != job.job_id):
                raise JobConflict("Invalid event cursor")
            rows = await session.scalars(select(ProgressEventRow).where(ProgressEventRow.job_id == job.job_id,
                ProgressEventRow.sequence > after).order_by(ProgressEventRow.sequence).limit(100))
            return job.job_id, job.status, job.event_sequence, [
                {"sequence":r.sequence,"event":r.event_type,
                 "data":progress_view(redact(r.payload),status=job.status,event=r.event_type,attempt=job.attempts),
                 "timestamp":r.created_at.isoformat()} for r in rows]
