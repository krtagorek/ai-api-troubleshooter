"""Add durable diagnostic governance only; existing application rows untouched."""
from alembic import op
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('CREATE TABLE app.diagnostic_contracts (\n\tcontract_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tapi_id UUID NOT NULL, \n\trevision INTEGER NOT NULL, \n\trevocation_revision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (contract_id), \n\tCONSTRAINT uq_dc_tenant UNIQUE (contract_id, tenant_id), \n\tCONSTRAINT ck_dc_revision CHECK (revision >= 1 AND revocation_revision >= 0), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id)\n)')
    op.execute('CREATE INDEX ix_app_diagnostic_contracts_api_id ON app.diagnostic_contracts (api_id)')
    op.execute('CREATE INDEX ix_app_diagnostic_contracts_tenant_id ON app.diagnostic_contracts (tenant_id)')
    op.execute("CREATE TABLE app.diagnostic_contract_versions (\n\tcontract_id UUID NOT NULL, \n\tversion INTEGER NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tendpoint_revision INTEGER NOT NULL, \n\tdocument JSONB NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\trevision INTEGER NOT NULL, \n\towner_verification_ref UUID, \n\tsecurity_review_ref UUID, \n\treviewer_id UUID, \n\tapproved_by UUID, \n\tactivated_by UUID, \n\trevoked_by UUID, \n\tsuperseded_by INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (contract_id, version), \n\tFOREIGN KEY(contract_id, tenant_id) REFERENCES app.diagnostic_contracts (contract_id, tenant_id), \n\tFOREIGN KEY(contract_id, superseded_by) REFERENCES app.diagnostic_contract_versions (contract_id, version), \n\tCONSTRAINT ck_dcv_revision CHECK (version >= 1 AND endpoint_revision >= 1 AND revision >= 1), \n\tCONSTRAINT ck_dcv_digest CHECK (digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_dcv_verified CHECK (status NOT IN ('owner_verified','security_reviewed','approved','active') OR owner_verification_ref IS NOT NULL), \n\tCONSTRAINT ck_dcv_reviewed CHECK (status NOT IN ('security_reviewed','approved','active') OR (security_review_ref IS NOT NULL AND reviewer_id IS NOT NULL)), \n\tCONSTRAINT ck_dcv_approved CHECK (status NOT IN ('approved','active') OR approved_by IS NOT NULL), \n\tCONSTRAINT ck_dcv_activated CHECK (status != 'active' OR activated_by IS NOT NULL), \n\tCONSTRAINT ck_dcv_revoked CHECK (status != 'revoked' OR revoked_by IS NOT NULL), \n\tCONSTRAINT ck_dcv_superseded CHECK (status != 'superseded' OR superseded_by IS NOT NULL), \n\tCONSTRAINT ck_dcv_status CHECK (status IN ('draft','owner_verified','security_reviewed','approved','active','revoked','superseded'))\n)")
    op.execute("CREATE UNIQUE INDEX uq_dcv_active ON app.diagnostic_contract_versions (contract_id) WHERE status = 'active'")
    op.execute("CREATE TABLE app.diagnostic_contract_events (\n\tcontract_id UUID NOT NULL, \n\tsequence INTEGER NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tversion INTEGER NOT NULL, \n\tactor_id UUID NOT NULL, \n\taction VARCHAR(32) NOT NULL, \n\tprevious_hash VARCHAR(64), \n\tevent_hash VARCHAR(64) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (contract_id, sequence), \n\tCONSTRAINT ck_dce_sequence CHECK (sequence >= 1), \n\tCONSTRAINT ck_dce_hash CHECK (event_hash ~ '^[0-9a-f]{64}$'), \n\tFOREIGN KEY(contract_id, version) REFERENCES app.diagnostic_contract_versions (contract_id, version), \n\tFOREIGN KEY(contract_id, tenant_id) REFERENCES app.diagnostic_contracts (contract_id, tenant_id)\n)")
    op.execute("""CREATE FUNCTION app.guard_diagnostic_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Diagnostic versions are immutable'; END IF;
      IF ROW(NEW.contract_id,NEW.version,NEW.tenant_id,NEW.endpoint_revision,NEW.document,NEW.digest,NEW.created_at)
         IS DISTINCT FROM ROW(OLD.contract_id,OLD.version,OLD.tenant_id,OLD.endpoint_revision,OLD.document,OLD.digest,OLD.created_at)
      THEN RAISE EXCEPTION 'Diagnostic document versions are immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER diagnostic_version_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_contract_versions FOR EACH ROW EXECUTE FUNCTION app.guard_diagnostic_immutable()")
    op.execute("""CREATE FUNCTION app.guard_diagnostic_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF ROW(NEW.contract_id,NEW.tenant_id,NEW.owner_id,NEW.api_id,NEW.created_at)
         IS DISTINCT FROM ROW(OLD.contract_id,OLD.tenant_id,OLD.owner_id,OLD.api_id,OLD.created_at)
      THEN RAISE EXCEPTION 'Diagnostic identity is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER diagnostic_identity_immutable BEFORE UPDATE ON app.diagnostic_contracts FOR EACH ROW EXECUTE FUNCTION app.guard_diagnostic_identity()")
    op.execute("""CREATE FUNCTION app.reject_diagnostic_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Diagnostic audit is append-only'; END $$""")
    op.execute("CREATE TRIGGER diagnostic_audit_append_only BEFORE UPDATE OR DELETE ON app.diagnostic_contract_events FOR EACH ROW EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    for table in ('diagnostic_contracts','diagnostic_contract_versions','diagnostic_contract_events'):
        op.execute(f"CREATE TRIGGER diagnostic_no_truncate BEFORE TRUNCATE ON app.{table} FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")


def downgrade():
    op.drop_table('diagnostic_contract_events',schema='app')
    op.drop_table('diagnostic_contract_versions',schema='app')
    op.drop_table('diagnostic_contracts',schema='app')
    op.execute('DROP FUNCTION app.guard_diagnostic_immutable()')
    op.execute('DROP FUNCTION app.guard_diagnostic_identity()')
    op.execute('DROP FUNCTION app.reject_diagnostic_audit_mutation()')
