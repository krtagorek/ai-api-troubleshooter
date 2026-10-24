"""Durable jobs and ordered progress, separate from LangGraph checkpoints."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None


def upgrade():
    op.create_table("jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation_id", UUID(as_uuid=True), sa.ForeignKey("app.investigations.investigation_id"), unique=True, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_id", UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','succeeded','awaiting_evidence','interrupted','failed','cancelled')", name="ck_jobs_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 5 AND attempts <= max_attempts", name="ck_jobs_attempts"),
        sa.CheckConstraint("event_sequence >= 0", name="ck_jobs_sequence"), schema="app")
    op.create_index("ix_app_jobs_available_at", "jobs", ["available_at"], schema="app")
    op.create_table("progress_events",
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("app.jobs.job_id"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="app")


def downgrade():
    op.drop_table("progress_events", schema="app")
    op.drop_index("ix_app_jobs_available_at", table_name="jobs", schema="app")
    op.drop_table("jobs", schema="app")
