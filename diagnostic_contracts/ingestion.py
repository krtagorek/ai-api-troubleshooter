"""Opt-in signed provenance boundary. Never called by existing incident ingestion.

A signature establishes producer attribution, not truth or semantic authority.
Only server-issued grants authorize producers; no envelope can activate contracts.
"""
import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from diagnostic_contracts.service import DiagnosticDenied
from persistence.diagnostic_contracts import DiagnosticContractRow, DiagnosticVersionRow
from persistence.ingestion import IngestionGrant, IngestionEnvelope
from tools.redaction import redact


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, revalidate_instances='always', hide_input_in_errors=True)
    version: Literal['signed-observations-1']
    envelope_id: UUID
    grant_id: UUID
    tenant_id: UUID
    api_id: UUID
    producer_id: UUID
    contract_id: UUID
    contract_version: int = Field(ge=1)
    contract_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    endpoint_revision: int = Field(ge=1)
    binding_revision: int = Field(ge=1)
    source: Literal['service_logs', 'verifier', 'gateway']
    resource: str = Field(pattern=r'^[a-zA-Z0-9_.:-]{1,128}$')
    correlation_id: str = Field(pattern=r'^[a-zA-Z0-9_.:-]{1,128}$')
    captured_at: datetime
    snapshot: dict
    snapshot_digest: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def safe(self):
        if self.captured_at.utcoffset() is None:
            raise ValueError('Timezone required')
        # Reject rather than silently alter the signed snapshot. No labels/secrets.
        if redact(self.wire()) != self.wire() or digest(self.snapshot) != self.snapshot_digest:
            raise ValueError('Snapshot must be sanitized and digest matched')
        if len(canonical(self.snapshot)) > 262144:
            raise ValueError('Snapshot too large')
        return self

    def wire(self):
        return self.model_dump(mode='json')


class Envelope(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, revalidate_instances='always', hide_input_in_errors=True)
    algorithm: Literal['Ed25519']
    key_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    payload: Payload
    signature: str = Field(pattern=r'^[A-Za-z0-9_-]{86}==$')

    def signed_bytes(self):
        return canonical({'algorithm': self.algorithm, 'key_id': self.key_id,
                          'payload': self.payload.wire()})


@dataclass(frozen=True)
class ProducerKey:
    public_key: Ed25519PublicKey
    producer_id: UUID
    tenant_id: UUID
    api_id: UUID
    not_before: datetime
    expires_at: datetime
    checked_until: datetime
    revoked: bool = False


class ProducerKeyAuthority(Protocol):
    """Server-injected, revocation-aware key source. Never token-controlled discovery.

    Resolve must bind a fresh key status to a <=30 second checked_until lease.
    Historical keys may remain available during rotation only while unrevoked.
    """
    async def resolve(self, key_id: str) -> ProducerKey | None: ...


class IngestionService:
    def __init__(self, database, authority, *, keys=None, clock=None):
        self.database, self.authority, self.keys = database, authority, keys
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def _key(self, envelope):
        try:
            if self.keys is None:
                raise ValueError()
            key = await asyncio.wait_for(self.keys.resolve(envelope.key_id), 2)
            now, p = self.clock(), envelope.payload
            if not isinstance(key, ProducerKey) or not isinstance(key.public_key, Ed25519PublicKey):
                raise ValueError()
            if key.revoked or not key.not_before <= p.captured_at <= now < key.expires_at:
                raise ValueError()
            if not now < key.checked_until or (key.checked_until-now).total_seconds() > 30:
                raise ValueError()
            if (key.producer_id, key.tenant_id, key.api_id) != (p.producer_id,p.tenant_id,p.api_id):
                raise ValueError()
            if not -5 <= (now-p.captured_at).total_seconds() <= 120:
                raise ValueError()
            signature = base64.urlsafe_b64decode(envelope.signature)
            if len(signature) != 64:
                raise ValueError()
            key.public_key.verify(signature, envelope.signed_bytes())
            return hashlib.sha256(key.public_key.public_bytes(Encoding.Raw,PublicFormat.Raw)).hexdigest()
        except Exception:
            raise DiagnosticDenied('Signed provenance unavailable or invalid') from None

    async def _binding(self, session, actor, p):
        # Same lock order as lifecycle services; endpoint lock fences ownership/revisions.
        from persistence.tables import ApiRegistryRow
        endpoint=await session.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id==p.api_id).with_for_update())
        from diagnostic_contracts.service import DiagnosticRegistry
        DiagnosticRegistry._endpoint(endpoint,p.endpoint_revision)
        row = await session.get(DiagnosticContractRow,p.contract_id,with_for_update=True)
        if row is None or (row.tenant_id,row.api_id,row.binding_revision)!=(p.tenant_id,p.api_id,p.binding_revision):
            raise DiagnosticDenied('Provenance binding unavailable')
        await self.authority.authorize_in_transaction(session,actor,p.tenant_id,p.api_id,row.owner_id,
                                                     'read_active',binding_revision=p.binding_revision)
        version = await session.get(DiagnosticVersionRow,(p.contract_id,p.contract_version),with_for_update=True)
        if version is None or version.status not in ('approved','active') or version.digest != p.contract_digest or version.endpoint_revision != p.endpoint_revision:
            raise DiagnosticDenied('Approved contract binding required')
        await self.authority.validate_in_transaction(session,actor,row,version)
        return row

    def _ready(self):
        if self.authority is None:
            raise DiagnosticDenied("Trusted authorization provider required")

    async def authorize_producer(self, actor, payload, *, ttl_seconds=120, expected_revision=None):
        """Explicit control-plane grant; payload is a scope template, not proof."""
        self._ready()
        p = Payload.model_validate(payload.model_dump())
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 120:
            raise DiagnosticDenied('Invalid grant duration')
        await self.authority._control(actor)
        from datetime import timedelta
        async with self.database.transaction() as s:
            row=await self._binding(s,actor,p)
            if expected_revision is not None:
                if row.revision!=expected_revision:raise DiagnosticDenied('Contract revision changed')
                from diagnostic_contracts.service import DiagnosticRegistry
                version=await s.get(DiagnosticVersionRow,(p.contract_id,p.contract_version))
                await self.authority.authorize_in_transaction(s,actor,p.tenant_id,p.api_id,row.owner_id,'approved',row.binding_revision)
                DiagnosticRegistry(None,admins=self.authority.global_admins)._admin(actor,row.owner_id,version.reviewer_id)
                from persistence.diagnostic_authority import MembershipRow
                member=await s.get(MembershipRow,(p.tenant_id,p.producer_id),with_for_update={'read':True})
                if member is None or not member.enabled or member.expires_at<=self.clock() or not set(member.roles)&{'owner','reader','security_reviewer','contract_admin'}:raise DiagnosticDenied('Producer membership unavailable')
            if await s.get(IngestionGrant,p.grant_id):
                raise DiagnosticDenied('Grant is single issue')
            s.add(IngestionGrant(grant_id=p.grant_id,tenant_id=p.tenant_id,api_id=p.api_id,
                  producer_id=p.producer_id,contract_id=p.contract_id,contract_version=p.contract_version,
                  binding_revision=p.binding_revision,endpoint_revision=p.endpoint_revision,
                  source=p.source,resource=p.resource,expires_at=self.clock()+timedelta(seconds=ttl_seconds),revoked=False))
            self.authority._audit(s,actor,'ingestion_grant',p.grant_id,{'scope_digest':digest(p.wire())})

    async def revoke_producer(self,actor,grant_id,*,expected=None):
        self._ready()
        await self.authority._control(actor)
        async with self.database.transaction() as s:
            if expected is not None:
                from diagnostic_contracts.service import DiagnosticRegistry
                from persistence.diagnostic_contracts import DiagnosticVersionRow
                registry=DiagnosticRegistry(self.database,tenants=self.authority,admins=self.authority.global_admins)
                row,endpoint=await registry._locked(s,actor,expected['tenant_id'],expected['contract_id'],'revoked')
                version=await s.get(DiagnosticVersionRow,(row.contract_id,expected['version']))
                if row.revision!=expected['expected_revision'] or version is None or version.digest!=expected['digest'] or version.endpoint_revision!=expected['endpoint_revision']:raise DiagnosticDenied('Grant revision binding mismatch')
                registry._admin(actor,row.owner_id)
            g=await s.get(IngestionGrant,grant_id,with_for_update=True)
            if g is None:raise DiagnosticDenied('Unknown grant')
            if expected is not None and (g.tenant_id,g.contract_id,g.contract_version)!=(expected['tenant_id'],expected['contract_id'],expected['version']):raise DiagnosticDenied('Grant scope mismatch')
            if expected is not None and g.revoked:raise DiagnosticDenied('Grant already revoked')
            g.revoked=True
            self.authority._audit(s,actor,'ingestion_revoke',grant_id,{})

    async def _grant(self,s,p):
        g=await s.get(IngestionGrant,p.grant_id,with_for_update=True)
        names=('tenant_id','api_id','producer_id','contract_id','contract_version','binding_revision','endpoint_revision','source','resource')
        if g is None or g.revoked or self.clock()>=g.expires_at or any(getattr(g,n)!=getattr(p,n) for n in names):
            raise DiagnosticDenied('Explicit current producer authorization required')

    async def ingest(self, actor, envelope,*,expected_revision=None):
        # Revalidate nested model instances, including model_construct/model_copy bypasses.
        self._ready()
        e=Envelope.model_validate(envelope.model_dump() if isinstance(envelope,Envelope) else envelope)
        p=e.payload
        await self.authority._identity(actor)
        if actor.subject_id!=p.producer_id:raise DiagnosticDenied('Producer identity mismatch')
        fingerprint=await self._key(e)
        async with self.database.transaction() as s:
            contract=await self._binding(s,actor,p)
            if expected_revision is not None and contract.revision!=expected_revision:raise DiagnosticDenied('Contract revision changed')
            await self._grant(s,p)
            scope=digest([str(p.tenant_id),str(p.api_id),str(p.producer_id),p.source,p.resource,p.correlation_id])
            # Endpoint lock serializes competing producers/grants before this query.
            previous=await s.scalar(select(IngestionEnvelope).where(IngestionEnvelope.scope_digest==scope))
            latest=await s.scalar(select(IngestionEnvelope).where(
                IngestionEnvelope.tenant_id==p.tenant_id,IngestionEnvelope.api_id==p.api_id,
                IngestionEnvelope.producer_id==p.producer_id,
                IngestionEnvelope.document['payload']['resource'].astext==p.resource).order_by(IngestionEnvelope.captured_at.desc()).limit(1))
            if latest is not None and p.captured_at <= latest.captured_at:
                raise DiagnosticDenied('Out-of-order observation requires independent reconciliation')
            if previous is not None or await s.get(IngestionEnvelope,p.envelope_id):
                raise DiagnosticDenied('Replay or conflicting observation scope')
            if fingerprint!=await self._key(e):raise DiagnosticDenied('Key changed during ingestion')
            now=self.clock()
            record=IngestionEnvelope(envelope_id=p.envelope_id,grant_id=p.grant_id,tenant_id=p.tenant_id,
                api_id=p.api_id,producer_id=p.producer_id,contract_id=p.contract_id,contract_version=p.contract_version,
                key_id=e.key_id,key_fingerprint=fingerprint,payload_digest=digest(p.wire()),snapshot_digest=p.snapshot_digest,
                scope_digest=scope,captured_at=p.captured_at,ingested_at=now,document=e.model_dump(mode='json'),signature_valid=True)
            s.add(record)
            self.authority._audit(s,actor,'ingestion_accepted',p.envelope_id,{'payload_digest':record.payload_digest,'key_fingerprint':fingerprint})
        return p.envelope_id

    async def verify_provenance(self,actor,envelope_id,*,tenant_id,api_id,contract_id,contract_version,snapshot_digest):
        """Fresh, read-only verification. Does not confer diagnostic truth or routing permission."""
        self._ready()
        async with self.database.transaction() as s:
            row=await s.get(IngestionEnvelope,envelope_id)
            if row is None or (row.tenant_id,row.api_id,row.contract_id,row.contract_version,row.snapshot_digest)!=(tenant_id,api_id,contract_id,contract_version,snapshot_digest):
                raise DiagnosticDenied('Provenance context mismatch')
            e=Envelope.model_validate_json(json.dumps(row.document))
            await self._binding(s,actor,e.payload)
            await self._grant(s,e.payload)
            if await self._key(e)!=row.key_fingerprint or digest(e.payload.wire())!=row.payload_digest:
                raise DiagnosticDenied('Provenance integrity mismatch')
            return {'envelope_id':str(envelope_id),'snapshot_digest':row.snapshot_digest,
                    'signature_valid':True,'diagnostic_truth':False,'qualified_only':True}
