"""Application-owned tables only. LangGraph tables are deliberately excluded."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, event, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from tools.redaction import redact


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = {"schema": "app"}
    incident_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationRow(Base):
    __tablename__ = "investigations"
    __table_args__ = (UniqueConstraint("thread_id", name="uq_investigations_thread_id"), {"schema": "app"})
    investigation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[str] = mapped_column(ForeignKey("app.incidents.incident_id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    diagnostic_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "app"}
    report_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("app.investigations.investigation_id"), unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("app.investigations.investigation_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiRegistryRow(Base):
    __tablename__ = "api_registry"
    __table_args__ = (
        CheckConstraint("status IN ('pending','verified','approved','suspended','revoked')", name="ck_registry_status"),
        CheckConstraint("revision >= 1", name="ck_registry_revision"),
        {"schema": "app"})
    api_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pinned_ips: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    verified_revision: Mapped[int | None] = mapped_column(Integer)
    approved_revision: Mapped[int | None] = mapped_column(Integer)
    approval_digest: Mapped[str | None] = mapped_column(String(64))
    challenge_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    challenge_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApiRegistryEventRow(Base):
    __tablename__ = "api_registry_events"
    __table_args__ = {"schema": "app"}
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    api_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.api_registry.api_id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(Session, "before_flush")
def sanitize_diagnostic_rows(session, *_):
    for row in session.new | session.dirty:
        if isinstance(row, (IncidentRow, ReportRow, ProgressEventRow)):
            row.payload = redact(row.payload)
        elif isinstance(row, InvestigationRow):
            row.diagnostic_state = redact(row.diagnostic_state)
        elif isinstance(row, AuditEventRow):
            row.details = redact(row.details)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','awaiting_evidence','interrupted','failed','cancelled')", name="ck_jobs_status"),
        CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 5 AND attempts <= max_attempts", name="ck_jobs_attempts"),
        CheckConstraint("event_sequence >= 0", name="ck_jobs_sequence"),
        {"schema": "app"})
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("app.investigations.investigation_id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProgressEventRow(Base):
    __tablename__ = "progress_events"
    __table_args__ = {"schema": "app"}
    job_id: Mapped[UUID] = mapped_column(ForeignKey("app.jobs.job_id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

# Register new application-owned metadata; no service or graph activation.
import persistence.diagnostic_contracts  # noqa: E402,F401
import persistence.diagnostic_authority  # noqa: E402,F401
import persistence.ingestion  # noqa: E402,F401
import persistence.permits  # noqa: E402,F401
