"""Add isolated signed ingestion provenance. No historical backfill."""
from alembic import op
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('\nCREATE TABLE app.diagnostic_ingestion_grants (\n\tgrant_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tapi_id UUID NOT NULL, \n\tproducer_id UUID NOT NULL, \n\tcontract_id UUID NOT NULL, \n\tcontract_version INTEGER NOT NULL, \n\tendpoint_revision INTEGER NOT NULL, \n\tbinding_revision INTEGER NOT NULL, \n\tsource VARCHAR(32) NOT NULL, \n\tresource VARCHAR(128) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked BOOLEAN NOT NULL, \n\tPRIMARY KEY (grant_id), \n\tCONSTRAINT ck_ingestion_grant_revisions CHECK (contract_version >= 1 AND endpoint_revision >= 1 AND binding_revision >= 1), \n\tFOREIGN KEY(contract_id, contract_version) REFERENCES app.diagnostic_contract_versions (contract_id, version), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id)\n)\n\n')
    op.execute("\nCREATE TABLE app.diagnostic_ingestion_envelopes (\n\tenvelope_id UUID NOT NULL, \n\tgrant_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tapi_id UUID NOT NULL, \n\tproducer_id UUID NOT NULL, \n\tcontract_id UUID NOT NULL, \n\tcontract_version INTEGER NOT NULL, \n\tkey_id VARCHAR(64) NOT NULL, \n\tkey_fingerprint VARCHAR(64) NOT NULL, \n\tpayload_digest VARCHAR(64) NOT NULL, \n\tsnapshot_digest VARCHAR(64) NOT NULL, \n\tscope_digest VARCHAR(64) NOT NULL, \n\tcaptured_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tingested_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdocument JSONB NOT NULL, \n\tsignature_valid BOOLEAN NOT NULL, \n\tPRIMARY KEY (envelope_id), \n\tCONSTRAINT ck_ingestion_integrity CHECK (signature_valid AND payload_digest ~ '^[a-f0-9]{64}$' AND snapshot_digest ~ '^[a-f0-9]{64}$' AND key_fingerprint ~ '^[a-f0-9]{64}$' AND scope_digest ~ '^[a-f0-9]{64}$' AND captured_at <= ingested_at), \n\tFOREIGN KEY(contract_id, contract_version) REFERENCES app.diagnostic_contract_versions (contract_id, version), \n\tFOREIGN KEY(grant_id) REFERENCES app.diagnostic_ingestion_grants (grant_id), \n\tFOREIGN KEY(api_id) REFERENCES app.api_registry (api_id), \n\tUNIQUE (scope_digest)\n)\n\n")
    op.execute('CREATE INDEX ix_app_diagnostic_ingestion_envelopes_tenant_id ON app.diagnostic_ingestion_envelopes (tenant_id)')
    op.execute("CREATE TRIGGER ingestion_envelope_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_ingestion_envelopes FOR EACH ROW EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("CREATE TRIGGER ingestion_envelope_no_truncate BEFORE TRUNCATE ON app.diagnostic_ingestion_envelopes FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")
    op.execute("""CREATE FUNCTION app.guard_ingestion_grant() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP='DELETE' OR (to_jsonb(NEW)-'revoked') IS DISTINCT FROM (to_jsonb(OLD)-'revoked') OR (OLD.revoked AND NOT NEW.revoked) THEN RAISE EXCEPTION 'Immutable producer grant'; END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER ingestion_grant_immutable BEFORE UPDATE OR DELETE ON app.diagnostic_ingestion_grants FOR EACH ROW EXECUTE FUNCTION app.guard_ingestion_grant()")
    op.execute("CREATE TRIGGER ingestion_grant_no_truncate BEFORE TRUNCATE ON app.diagnostic_ingestion_grants FOR EACH STATEMENT EXECUTE FUNCTION app.reject_diagnostic_audit_mutation()")

def downgrade():
    op.drop_table('diagnostic_ingestion_envelopes',schema='app')
    op.drop_table('diagnostic_ingestion_grants',schema='app')
    op.execute('DROP FUNCTION app.guard_ingestion_grant()')
