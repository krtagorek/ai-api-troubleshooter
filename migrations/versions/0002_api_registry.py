"""API registry and append-only service audit history; no checkpoint changes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("api_registry",
        sa.Column("api_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", pg.JSONB(), nullable=False),
        sa.Column("pinned_ips", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("verified_revision", sa.Integer()),
        sa.Column("approved_revision", sa.Integer()),
        sa.Column("approval_digest", sa.String(64)),
        sa.Column("challenge_digest", sa.String(64), nullable=False),
        sa.Column("challenge_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','verified','approved','suspended','revoked')", name="ck_registry_status"),
        sa.CheckConstraint("revision >= 1", name="ck_registry_revision"), schema="app")
    op.create_index("ix_app_api_registry_owner_id", "api_registry", ["owner_id"], schema="app")
    op.create_table("api_registry_events",
        sa.Column("event_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("api_id", pg.UUID(as_uuid=True), sa.ForeignKey("app.api_registry.api_id")),
        sa.Column("actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="app")
    op.create_index("ix_app_api_registry_events_api_id", "api_registry_events", ["api_id"], schema="app")
    op.execute("""CREATE FUNCTION app.reject_registry_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'Registry audit history is append-only';
        END $$""")
    op.execute("""CREATE TRIGGER registry_events_append_only
        BEFORE UPDATE OR DELETE ON app.api_registry_events
        FOR EACH ROW EXECUTE FUNCTION app.reject_registry_event_mutation()""")


def downgrade():
    op.drop_table("api_registry_events", schema="app")
    op.execute("DROP FUNCTION app.reject_registry_event_mutation()")
    op.drop_table("api_registry", schema="app")
