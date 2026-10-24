"""Initial application tables. Never manage LangGraph's internal schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("incidents",
        sa.Column("incident_id", sa.String(128), primary_key=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="app")
    op.create_table("investigations",
        sa.Column("investigation_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", sa.String(128), sa.ForeignKey("app.incidents.incident_id"), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("diagnostic_state", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", name="uq_investigations_thread_id"), schema="app")
    op.create_index("ix_app_investigations_incident_id", "investigations", ["incident_id"], schema="app")
    op.create_table("reports",
        sa.Column("report_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), sa.ForeignKey("app.investigations.investigation_id"), nullable=False, unique=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="app")
    op.create_table("audit_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), sa.ForeignKey("app.investigations.investigation_id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("details", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="app")
    op.create_index("ix_app_audit_events_investigation_id", "audit_events", ["investigation_id"], schema="app")


def downgrade():
    for table in ("audit_events", "reports", "investigations", "incidents"):
        op.drop_table(table, schema="app")
