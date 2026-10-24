"""Durable single-use diagnostic permits and shared endpoint fencing generations."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import Integer,String,DateTime,ForeignKey,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID,JSONB
from persistence.tables import Base

class AuthorizationFence(Base):
    __tablename__='diagnostic_authorization_fences'
    __table_args__=(CheckConstraint('generation >= 1',name='ck_daf_generation'),{'schema':'app'})
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'),primary_key=True)
    generation:Mapped[int]=mapped_column(Integer)
    blocked_keys:Mapped[list]=mapped_column(JSONB)

class AuthorizationPermit(Base):
    __tablename__='diagnostic_authorization_permits'
    __table_args__={'schema':'app'}
    permit_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),primary_key=True)
    capability_hash:Mapped[str]=mapped_column(String(64),unique=True)
    tenant_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True),index=True)
    actor_id:Mapped[UUID]=mapped_column(PGUUID(as_uuid=True))
    api_id:Mapped[UUID]=mapped_column(ForeignKey('app.api_registry.api_id'))
    envelope_id:Mapped[UUID]=mapped_column(ForeignKey('app.diagnostic_ingestion_envelopes.envelope_id'))
    context:Mapped[dict]=mapped_column(JSONB)
    issued_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class PermitConsumption(Base):
    __tablename__='diagnostic_permit_consumptions'
    __table_args__={'schema':'app'}
    permit_id:Mapped[UUID]=mapped_column(ForeignKey('app.diagnostic_authorization_permits.permit_id'),primary_key=True)
    consumed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    result_digest:Mapped[str]=mapped_column(String(64))
