"""Durable single-use permits and monotonic endpoint fences. No backfill."""
from alembic import op
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('\nCREATE TABLE app.diagnostic_authorization_fences (\n\tapi_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tblocked_keys JSONB NOT NULL, \n\tPRIMARY KEY (api_id), \n\tCONSTRAINT ck_daf_generation CHECK (generation >= 1), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id)\n)\n\n')
    op.execute('\nCREATE TABLE app.diagnostic_authorization_permits (\n\tpermit_id UUID NOT NULL, \n\tcapability_hash VARCHAR(64) NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\tapi_id UUID NOT NULL, \n\tenvelope_id UUID NOT NULL, \n\tcontext JSONB NOT NULL, \n\tissued_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (permit_id), \n\tUNIQUE (capability_hash), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id), \n\tFOREIGN KEY(envelope_id) REFERENCES app.diagnostic_ingestion_envelopes (envelope_id)\n)\n\n')
    op.execute('CREATE INDEX ix_app_diagnostic_authorization_permits_tenant_id ON app.diagnostic_authorization_permits (tenant_id)')
    op.execute("CREATE TRIGGER diagnostic_authorization_permits_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_authorization_permits FOR EACH ROW EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("CREATE TRIGGER diagnostic_authorization_permits_no_truncate BEFORE TRUNCATE ON app.diagnostic_authorization_permits FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute('\nCREATE TABLE app.diagnostic_permit_consumptions (\n\tpermit_id UUID NOT NULL, \n\tconsumed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tresult_digest VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (permit_id), \n\tFOREIGN KEY(permit_id) REFERENCES app.diagnostic_authorization_permits (permit_id)\n)\n\n')
    op.execute("CREATE TRIGGER diagnostic_permit_consumptions_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_permit_consumptions FOR EACH ROW EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("CREATE TRIGGER diagnostic_permit_consumptions_no_truncate BEFORE TRUNCATE ON app.diagnostic_permit_consumptions FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("""CREATE FUNCTION app.guard_authorization_fence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Fence cannot be deleted'; END IF;
IF NEW.api_id IS DISTINCT FROM OLD.api_id OR NEW.generation != OLD.generation+1 OR NOT NEW.blocked_keys @> OLD.blocked_keys THEN RAISE EXCEPTION 'Fence must increase by one'; END IF;
RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER diagnostic_fence_monotonic BEFORE UPDATE OR DELETE ON app.diagnostic_authorization_fences FOR EACH ROW EXECUTE FUNCTION app.guard_authorization_fence()")
    op.execute("CREATE TRIGGER diagnostic_fence_no_truncate BEFORE TRUNCATE ON app.diagnostic_authorization_fences FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")

def downgrade():
    op.drop_table('diagnostic_permit_consumptions',schema='app')
    op.drop_table('diagnostic_authorization_permits',schema='app')
    op.drop_table('diagnostic_authorization_fences',schema='app')
    op.execute('DROP FUNCTION app.guard_authorization_fence()')
