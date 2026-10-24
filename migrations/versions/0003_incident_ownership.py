"""Owner isolation for REST access; legacy incidents remain administrator-only."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.add_column("incidents", sa.Column("owner_id", UUID(as_uuid=True), nullable=True), schema="app")
    op.create_index("ix_app_incidents_owner_id", "incidents", ["owner_id"], schema="app")


def downgrade():
    op.drop_index("ix_app_incidents_owner_id", table_name="incidents", schema="app")
    op.drop_column("incidents", "owner_id", schema="app")
