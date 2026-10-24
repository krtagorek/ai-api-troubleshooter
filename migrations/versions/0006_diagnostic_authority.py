"""Explicit diagnostic tenant authority and time-limited evidence; no backfill."""
from alembic import op
import sqlalchemy as sa
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('diagnostic_contracts',sa.Column('binding_revision',sa.Integer(),nullable=True),schema='app')
    op.execute('CREATE TABLE app.diagnostic_memberships (\n\ttenant_id UUID NOT NULL, \n\tsubject_id UUID NOT NULL, \n\troles JSONB NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\trevision INTEGER NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (tenant_id, subject_id), \n\tCONSTRAINT ck_dm_revision CHECK (revision >= 1)\n)')
    op.execute('CREATE TABLE app.diagnostic_endpoint_tenants (\n\tapi_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tendpoint_revision INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tverification_ref UUID NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tPRIMARY KEY (api_id), \n\tCONSTRAINT ck_det_revision CHECK (revision >= 1 AND endpoint_revision >= 1), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id), \n\tUNIQUE (verification_ref)\n)')
    op.execute('CREATE INDEX ix_app_diagnostic_endpoint_tenants_tenant_id ON app.diagnostic_endpoint_tenants (tenant_id)')
    op.execute("CREATE TABLE app.diagnostic_attestations (\n\treference UUID NOT NULL, \n\tprovider_ref UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tapi_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tsubject_id UUID NOT NULL, \n\tcontract_id UUID, \n\tversion INTEGER, \n\tdigest VARCHAR(64) NOT NULL, \n\tendpoint_revision INTEGER NOT NULL, \n\tbinding_revision INTEGER NOT NULL, \n\tmembership_revision INTEGER, \n\tkind VARCHAR(16) NOT NULL, \n\tissued_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked BOOLEAN NOT NULL, \n\tPRIMARY KEY (reference), \n\tFOREIGN KEY(contract_id, version) REFERENCES app.diagnostic_contract_versions (contract_id, version), \n\tCONSTRAINT uq_da_provider UNIQUE (provider_ref), \n\tCONSTRAINT ck_da_kind CHECK (kind IN ('ownership','security','endpoint')), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id), \n\tFOREIGN KEY(contract_id) REFERENCES app.diagnostic_contracts (contract_id)\n)")
    op.execute('CREATE INDEX ix_app_diagnostic_attestations_tenant_id ON app.diagnostic_attestations (tenant_id)')
    op.execute('CREATE TABLE app.diagnostic_authority_events (\n\tevent_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\taction VARCHAR(32) NOT NULL, \n\tsubject_ref UUID NOT NULL, \n\tdetails JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (event_id)\n)')
    op.execute("CREATE TRIGGER diagnostic_authority_append_only BEFORE UPDATE OR DELETE ON app.diagnostic_authority_events FOR EACH ROW EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("CREATE TRIGGER diagnostic_authority_no_truncate BEFORE TRUNCATE ON app.diagnostic_authority_events FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("""CREATE FUNCTION app.guard_diagnostic_attestation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Verification evidence is immutable'; END IF;
      IF (to_jsonb(NEW)-'revoked') IS DISTINCT FROM (to_jsonb(OLD)-'revoked') OR (OLD.revoked AND NOT NEW.revoked)
      THEN RAISE EXCEPTION 'Verification evidence cannot be rewritten or unrevoked'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER diagnostic_attestation_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_attestations FOR EACH ROW EXECUTE FUNCTION app.guard_diagnostic_attestation()")

    op.execute("""CREATE FUNCTION app.guard_diagnostic_binding_revision() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.binding_revision IS DISTINCT FROM OLD.binding_revision THEN RAISE EXCEPTION 'Contract ownership binding is immutable'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER diagnostic_binding_immutable BEFORE UPDATE ON app.diagnostic_contracts FOR EACH ROW EXECUTE FUNCTION app.guard_diagnostic_binding_revision()")

def downgrade():
    op.execute('DROP TRIGGER diagnostic_binding_immutable ON app.diagnostic_contracts')
    op.execute('DROP FUNCTION app.guard_diagnostic_binding_revision()')
    op.drop_table('diagnostic_authority_events',schema='app')
    op.drop_table('diagnostic_attestations',schema='app')
    op.execute('DROP FUNCTION app.guard_diagnostic_attestation()')
    op.drop_table('diagnostic_endpoint_tenants',schema='app')
    op.drop_table('diagnostic_memberships',schema='app')
    op.drop_column('diagnostic_contracts','binding_revision',schema='app')
