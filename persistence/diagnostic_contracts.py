"""Application-owned diagnostic governance only; no graph integration."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import String,Integer,DateTime,ForeignKey,ForeignKeyConstraint,UniqueConstraint,CheckConstraint,Index,text
from sqlalchemy.dialects.postgresql import JSONB,UUID as PGUUID
from sqlalchemy.orm import Mapped,mapped_column
from persistence.tables import Base

class DiagnosticContractRow(Base):
    __tablename__='diagnostic_contracts'
    __table_args__=(UniqueConstraint('contract_id','tenant_id',name='uq_dc_tenant'),CheckConstraint('revision >= 1 AND revocation_revision >= 0',name='ck_dc_revision'),{'schema':'app'})
    contract_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),index=True)
    owner_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'),index=True)
    binding_revision:Mapped[int|None]=mapped_column(Integer)
    revision:Mapped[int]=mapped_column(Integer)
    revocation_revision:Mapped[int]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class DiagnosticVersionRow(Base):
    __tablename__='diagnostic_contract_versions'
    __table_args__=(ForeignKeyConstraint(['contract_id','tenant_id'],['app.diagnostic_contracts.contract_id','app.diagnostic_contracts.tenant_id']),ForeignKeyConstraint(['contract_id','superseded_by'],['app.diagnostic_contract_versions.contract_id','app.diagnostic_contract_versions.version']),CheckConstraint('version >= 1 AND endpoint_revision >= 1 AND revision >= 1',name='ck_dcv_revision'),CheckConstraint("digest ~ '^[0-9a-f]{64}$'",name='ck_dcv_digest'),CheckConstraint("status NOT IN ('owner_verified','security_reviewed','approved','active') OR owner_verification_ref IS NOT NULL",name='ck_dcv_verified'),CheckConstraint("status NOT IN ('security_reviewed','approved','active') OR (security_review_ref IS NOT NULL AND reviewer_id IS NOT NULL)",name='ck_dcv_reviewed'),CheckConstraint("status NOT IN ('approved','active') OR approved_by IS NOT NULL",name='ck_dcv_approved'),CheckConstraint("status != 'active' OR activated_by IS NOT NULL",name='ck_dcv_activated'),CheckConstraint("status != 'revoked' OR revoked_by IS NOT NULL",name='ck_dcv_revoked'),CheckConstraint("status != 'superseded' OR superseded_by IS NOT NULL",name='ck_dcv_superseded'),CheckConstraint("status IN ('draft','owner_verified','security_reviewed','approved','active','revoked','superseded')",name='ck_dcv_status'),Index('uq_dcv_active','contract_id',unique=True,postgresql_where=text("status = 'active'")),{'schema':'app'})
    contract_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    version:Mapped[int]=mapped_column(Integer,primary_key=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    endpoint_revision:Mapped[int]=mapped_column(Integer)
    document:Mapped[dict]=mapped_column(JSONB)
    digest:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(24))
    revision:Mapped[int]=mapped_column(Integer)
    owner_verification_ref:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    security_review_ref:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    reviewer_id:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    approved_by:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    activated_by:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    revoked_by:Mapped[UUID|None]=mapped_column(PGUUID(as_uuid=True))
    superseded_by:Mapped[int|None]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class DiagnosticEventRow(Base):
    __tablename__='diagnostic_contract_events'
    __table_args__=(CheckConstraint('sequence >= 1',name='ck_dce_sequence'),CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'",name='ck_dce_hash'),ForeignKeyConstraint(['contract_id','version'],['app.diagnostic_contract_versions.contract_id','app.diagnostic_contract_versions.version']),ForeignKeyConstraint(['contract_id','tenant_id'],['app.diagnostic_contracts.contract_id','app.diagnostic_contracts.tenant_id']),{'schema':'app'})
    contract_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    sequence:Mapped[int]=mapped_column(Integer,primary_key=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    version:Mapped[int]=mapped_column(Integer)
    actor_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    action:Mapped[str]=mapped_column(String(32))
    previous_hash:Mapped[str|None]=mapped_column(String(64))
    event_hash:Mapped[str]=mapped_column(String(64))
    payload:Mapped[dict]=mapped_column(JSONB)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
