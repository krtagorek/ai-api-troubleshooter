"""Explicit tenant bindings, membership, evidence, and authority audit. No backfill."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import Integer,String,Boolean,DateTime,ForeignKey,ForeignKeyConstraint,UniqueConstraint,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy.dialects.postgresql import JSONB,UUID as PGUUID
from persistence.tables import Base

class MembershipRow(Base):
    __tablename__='diagnostic_memberships'
    __table_args__=(CheckConstraint('revision >= 1',name='ck_dm_revision'),{'schema':'app'})
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    subject_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    roles:Mapped[list]=mapped_column(JSONB)
    enabled:Mapped[bool]=mapped_column(Boolean)
    revision:Mapped[int]=mapped_column(Integer)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class EndpointTenantRow(Base):
    __tablename__='diagnostic_endpoint_tenants'
    __table_args__=(CheckConstraint('revision >= 1 AND endpoint_revision >= 1',name='ck_det_revision'),{'schema':'app'})
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'),primary_key=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),index=True)
    owner_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    endpoint_revision:Mapped[int]=mapped_column(Integer)
    revision:Mapped[int]=mapped_column(Integer)
    verification_ref:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),unique=True)
    enabled:Mapped[bool]=mapped_column(Boolean)

class AttestationRow(Base):
    __tablename__='diagnostic_attestations'
    __table_args__=(ForeignKeyConstraint(['contract_id','version'],['app.diagnostic_contract_versions.contract_id','app.diagnostic_contract_versions.version']),UniqueConstraint('provider_ref',name='uq_da_provider'),CheckConstraint("kind IN ('ownership','security','endpoint')",name='ck_da_kind'),{'schema':'app'})
    reference:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    provider_ref:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),index=True)
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'))
    owner_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    subject_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    contract_id:Mapped[UUID|None]=mapped_column(ForeignKey('app.diagnostic_contracts.contract_id'))
    version:Mapped[int|None]=mapped_column(Integer)
    digest:Mapped[str]=mapped_column(String(64))
    endpoint_revision:Mapped[int]=mapped_column(Integer)
    binding_revision:Mapped[int]=mapped_column(Integer)
    membership_revision:Mapped[int|None]=mapped_column(Integer)
    kind:Mapped[str]=mapped_column(String(16))
    issued_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked:Mapped[bool]=mapped_column(Boolean)

class AuthorityEventRow(Base):
    __tablename__='diagnostic_authority_events'
    __table_args__={'schema':'app'}
    event_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    actor_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    action:Mapped[str]=mapped_column(String(32))
    subject_ref:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    details:Mapped[dict]=mapped_column(JSONB)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
