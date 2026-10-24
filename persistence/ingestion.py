"""Separate, opt-in ingestion provenance; no historical evidence backfill."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import String,Integer,Boolean,DateTime,ForeignKey,ForeignKeyConstraint,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID,JSONB
from persistence.tables import Base

class IngestionGrant(Base):
    __tablename__='diagnostic_ingestion_grants'
    __table_args__=(CheckConstraint('contract_version >= 1 AND endpoint_revision >= 1 AND binding_revision >= 1',name='ck_ingestion_grant_revisions'),ForeignKeyConstraint(['contract_id','contract_version'],['app.diagnostic_contract_versions.contract_id','app.diagnostic_contract_versions.version']),{'schema':'app'})
    grant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'))
    producer_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    contract_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    contract_version:Mapped[int]=mapped_column(Integer)
    endpoint_revision:Mapped[int]=mapped_column(Integer)
    binding_revision:Mapped[int]=mapped_column(Integer)
    source:Mapped[str]=mapped_column(String(32))
    resource:Mapped[str]=mapped_column(String(128))
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked:Mapped[bool]=mapped_column(Boolean)

class IngestionEnvelope(Base):
    __tablename__='diagnostic_ingestion_envelopes'
    __table_args__=(CheckConstraint("signature_valid AND payload_digest ~ '^[a-f0-9]{64}$' AND snapshot_digest ~ '^[a-f0-9]{64}$' AND key_fingerprint ~ '^[a-f0-9]{64}$' AND scope_digest ~ '^[a-f0-9]{64}$' AND captured_at <= ingested_at",name='ck_ingestion_integrity'),ForeignKeyConstraint(['contract_id','contract_version'],['app.diagnostic_contract_versions.contract_id','app.diagnostic_contract_versions.version']),{'schema':'app'})
    envelope_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    grant_id:Mapped[UUID]=mapped_column(ForeignKey('app.diagnostic_ingestion_grants.grant_id'))
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),index=True)
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'))
    producer_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    contract_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    contract_version:Mapped[int]=mapped_column(Integer)
    key_id:Mapped[str]=mapped_column(String(64))
    key_fingerprint:Mapped[str]=mapped_column(String(64))
    payload_digest:Mapped[str]=mapped_column(String(64))
    snapshot_digest:Mapped[str]=mapped_column(String(64))
    scope_digest:Mapped[str]=mapped_column(String(64),unique=True)
    captured_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    ingested_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    document:Mapped[dict]=mapped_column(JSONB)
    signature_valid:Mapped[bool]=mapped_column(Boolean)
