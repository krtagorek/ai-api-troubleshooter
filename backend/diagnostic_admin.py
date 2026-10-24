"""Configured-only control plane. No graph execution or permit issuance endpoints."""
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
import json
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,func,text,or_,and_
from sqlalchemy.exc import IntegrityError
from backend.auth import identity
from diagnostic_contracts.identity import JWTAuthenticator
from diagnostic_contracts.authority import DurableAuthority
from diagnostic_contracts.service import DiagnosticRegistry,DiagnosticDenied,DiagnosticConflict
from diagnostic_contracts.ingestion import IngestionService,Payload,Envelope
from persistence.diagnostic_authority import MembershipRow,EndpointTenantRow,AuthorityEventRow,AttestationRow
from persistence.diagnostic_contracts import DiagnosticContractRow as Contract,DiagnosticVersionRow as Version
from persistence.ingestion import IngestionGrant,IngestionEnvelope
from persistence.tables import ApiRegistryRow
from tools.redaction import redact

PREFIX='/api/v1/diagnostic-admin'
router=APIRouter(prefix=PREFIX,tags=['diagnostic administration'])

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)
class Registration(Input):
    api_id:UUID
    endpoint_revision:int=Field(strict=True,ge=1)
    document:dict
class Bound(Input):
    version:int=Field(strict=True,ge=1)
    digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    expected_revision:int=Field(strict=True,ge=1)
    endpoint_revision:int=Field(strict=True,ge=1)
class Transition(Bound):
    superseded_by:int|None=Field(default=None,strict=True,ge=1)
class Revision(Bound):
    document:dict
class GrantRequest(Bound):
    payload:dict|str=Field(max_length=240000)
    ttl_seconds:int=Field(default=120,strict=True,ge=1,le=120)
class EnvelopeRequest(Bound):
    envelope:dict|str=Field(max_length=240000)
class BindingRequest(Input):
    endpoint_revision:int=Field(strict=True,ge=1)
    expected_revision:int=Field(strict=True,ge=0)

@dataclass
class Administration:
    registry:DiagnosticRegistry
    ingestion:IngestionService
    requests_per_minute:int=120

    @property
    def authority(self):return self.registry.tenants
    def configured(self,auth):
        a=self.authority
        if (not isinstance(a,DurableAuthority) or not isinstance(auth,JWTAuthenticator)
            or a.identities is not auth or auth.keys is None or auth.revocations is None or a.evidence_provider is None or a.global_admins is None
            or self.registry.ownership is not a or self.registry.security is not a
            or self.ingestion.authority is not a or self.ingestion.keys is None
            or not 1<=self.requests_per_minute<=600):
            raise HTTPException(503,'Trusted diagnostic administration providers are not configured')

    async def quota(self,actor):
        # Shared across processes. Audit insert and count are serialized by actor.
        async with self.registry.db.transaction() as s:
            await s.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':actor.subject_id.int%(2**63-1)})
            now=await s.scalar(select(func.clock_timestamp()))
            count=await s.scalar(select(func.count()).select_from(AuthorityEventRow).where(AuthorityEventRow.actor_id==actor.subject_id,AuthorityEventRow.action=='admin_http_request',AuthorityEventRow.created_at>now-timedelta(minutes=1)))
            if count>=self.requests_per_minute:raise HTTPException(429,'Diagnostic administration rate limit exceeded',headers={'Retry-After':'60'})
            s.add(AuthorityEventRow(event_id=uuid4(),actor_id=actor.subject_id,action='admin_http_request',subject_ref=actor.subject_id,details={},created_at=now))

    async def membership(self,actor,tenant):
        await self.authority._identity(actor)
        async with self.registry.db.transaction() as s:
            member=await s.get(MembershipRow,(tenant,actor.subject_id))
            if member is None or not member.enabled or member.expires_at<=self.authority.clock() or not set(member.roles)&{'owner','reader','security_reviewer','contract_admin'}:raise HTTPException(404,'Diagnostic tenant unavailable')

    async def read(self,actor,tenant,ident):
        async with self.registry.db.transaction() as s:
            row=await s.scalar(select(Contract).where(Contract.contract_id==ident,Contract.tenant_id==tenant))
            if row is None:raise HTTPException(404,'Diagnostic contract unavailable')
            await self.registry._allowed(actor,tenant,row.api_id,row.owner_id,'history',session=s,binding_revision=row.binding_revision)
            return row

    async def version(self,actor,tenant,ident,number):
        row=await self.read(actor,tenant,ident)
        async with self.registry.db.transaction() as s:
            v=await s.get(Version,(ident,number))
            if v is None:raise HTTPException(404,'Version unavailable')
            return {**self.registry._snapshot(row,v),'owner_verified':v.owner_verification_ref is not None,'security_reviewed':v.security_review_ref is not None}

async def context(request:Request,tenant:UUID,actor=Depends(identity)):
    factory=getattr(request.app.state,'diagnostic_admin_factory',None)
    if factory is None:raise HTTPException(503,'Trusted diagnostic administration providers are not configured')
    try:
        admin=factory(request.app.state.runtime.database)
        if not isinstance(admin,Administration):raise ValueError()
        admin.configured(request.app.state.authenticator)
        if admin.registry.db is not request.app.state.runtime.database or admin.ingestion.database is not admin.registry.db or admin.authority.db is not admin.registry.db:raise ValueError('Administration database composition mismatch')
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Trusted diagnostic administration providers unavailable') from None
    await admin.authority._identity(actor)
    await admin.quota(actor)
    await admin.membership(actor,tenant)
    return admin,actor

def safe(value):
    # Explicit DTO construction below excludes envelopes/signatures/keys/capabilities.
    return redact(value)

def page(items,limit,offset):return {'items':items,'limit':limit,'offset':offset}

def bound_payload(body,tenant,ident):
    value=body.payload
    p=Payload.model_validate_json(value if isinstance(value,str) else json.dumps(value))
    if (p.tenant_id,p.contract_id,p.contract_version,p.contract_digest,p.endpoint_revision)!=(tenant,ident,body.version,body.digest,body.endpoint_revision):raise DiagnosticConflict('Requested scope mismatch')
    return p

@router.get('/tenants/{tenant}/endpoints')
async def endpoints(tenant:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c
    async with admin.registry.db.transaction() as s:
        rows=list(await s.scalars(select(EndpointTenantRow).where(EndpointTenantRow.tenant_id==tenant).order_by(EndpointTenantRow.api_id).limit(limit).offset(offset)))
        result=[]
        for row in rows:
            await admin.authority.authorize_in_transaction(s,actor,tenant,row.api_id,row.owner_id,'history',row.revision)
            result.append({'api_id':str(row.api_id),'endpoint_revision':row.endpoint_revision,'binding_revision':row.revision})
    return page(result,limit,offset)

@router.post('/tenants/{tenant}/endpoint-bindings/{api}')
async def bind(tenant:UUID,api:UUID,body:BindingRequest,c=Depends(context)):
    admin,actor=c
    async with admin.registry.db.transaction() as s:
        ep=await s.get(ApiRegistryRow,api)
        if ep is None:raise HTTPException(404,'Endpoint unavailable')
        owner=ep.owner_id
        member=await s.get(MembershipRow,(tenant,owner))
        if member is None or not member.enabled or 'owner' not in member.roles or member.expires_at<=admin.authority.clock():raise DiagnosticDenied('Current owner membership required')
        DiagnosticRegistry._endpoint(ep,body.endpoint_revision)
    revision=await admin.authority.bind_endpoint(actor,tenant,api,owner,expected_revision=body.expected_revision,endpoint_revision=body.endpoint_revision,require_tenant_membership=True)
    return {'api_id':str(api),'binding_revision':revision}

@router.get('/tenants/{tenant}/contracts')
async def contracts(tenant:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c
    async with admin.registry.db.transaction() as s:
        rows=list(await s.scalars(select(Contract).where(Contract.tenant_id==tenant).order_by(Contract.contract_id).offset(offset).limit(limit)))
        keys=[row.contract_id for row in rows]
        versions={key:await s.scalar(select(func.max(Version.version)).where(Version.contract_id==key)) for key in keys}
    return page([safe(await admin.version(actor,tenant,key,versions[key])) for key in keys],limit,offset)

@router.post('/tenants/{tenant}/contracts',status_code=201)
async def register(tenant:UUID,body:Registration,c=Depends(context)):
    admin,actor=c
    return safe(await admin.registry.register(actor,tenant,body.api_id,body.endpoint_revision,body.document))

@router.get('/tenants/{tenant}/contracts/{ident}')
async def detail(tenant:UUID,ident:UUID,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    async with admin.registry.db.transaction() as s:number=await s.scalar(select(func.max(Version.version)).where(Version.contract_id==ident))
    return safe(await admin.version(actor,tenant,ident,number))

@router.get('/tenants/{tenant}/contracts/{ident}/versions')
async def versions(tenant:UUID,ident:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    async with admin.registry.db.transaction() as s:numbers=list(await s.scalars(select(Version.version).where(Version.contract_id==ident).order_by(Version.version).limit(limit).offset(offset)))
    return page([safe(await admin.version(actor,tenant,ident,n)) for n in numbers],limit,offset)

@router.get('/tenants/{tenant}/contracts/{ident}/audit')
async def audit(tenant:UUID,ident:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    items=await admin.registry.history(actor,tenant,ident)
    return page(safe(items[offset:offset+limit]),limit,offset)

@router.post('/tenants/{tenant}/contracts/{ident}/revisions',status_code=201)
async def revise(tenant:UUID,ident:UUID,body:Revision,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    return safe(await admin.registry.revise(actor,tenant,ident,body.expected_revision,body.endpoint_revision,body.document,expected_digest=body.digest,expected_version=body.version))

# Fixed routes, not a client-controlled approval/status field.
def transition(target):
    async def perform(tenant:UUID,ident:UUID,body:Transition,c=Depends(context)):
        admin,actor=c;await admin.read(actor,tenant,ident)
        if target!='superseded' and body.superseded_by is not None:raise DiagnosticConflict('Unexpected successor')
        return safe(await admin.registry.transition(actor,tenant,ident,body.version,target,body.expected_revision,superseded_by=body.superseded_by,expected_digest=body.digest,endpoint_revision=body.endpoint_revision))
    return perform
for action,target in (('verify-owner','owner_verified'),('security-review','security_reviewed'),('approve','approved'),('activate','active'),('revoke','revoked'),('supersede','superseded')):
    router.add_api_route('/tenants/{tenant}/contracts/{ident}/'+action,transition(target),methods=['POST'],name=action)

@router.get('/tenants/{tenant}/contracts/{ident}/authorization')
async def authorization(tenant:UUID,ident:UUID,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    try:await admin.registry.active_binding(actor,tenant,ident)
    except (DiagnosticDenied,DiagnosticConflict):return {'allowed':False,'execution_permit':False}
    return {'allowed':True,'execution_permit':False,'qualified_only':True}

@router.get('/tenants/{tenant}/contracts/{ident}/grants')
async def grants(tenant:UUID,ident:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    async with admin.registry.db.transaction() as s:
        rows=list(await s.scalars(select(IngestionGrant).where(IngestionGrant.contract_id==ident).order_by(IngestionGrant.grant_id).limit(limit).offset(offset)))
        items=[{'grant_id':str(g.grant_id),'producer_id':str(g.producer_id),'version':g.contract_version,'revoked':g.revoked,'expires_at':g.expires_at.isoformat()} for g in rows]
    return page(items,limit,offset)

@router.post('/tenants/{tenant}/contracts/{ident}/grants',status_code=201)
async def issue(tenant:UUID,ident:UUID,body:GrantRequest,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident);p=bound_payload(body,tenant,ident)
    await admin.ingestion.authorize_producer(actor,p,ttl_seconds=body.ttl_seconds,expected_revision=body.expected_revision)
    return {'grant_id':str(p.grant_id),'issued':True}

@router.post('/tenants/{tenant}/contracts/{ident}/grants/{grant}/revoke')
async def revoke_grant(tenant:UUID,ident:UUID,grant:UUID,body:Bound,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    await admin.ingestion.revoke_producer(actor,grant,expected={'tenant_id':tenant,'contract_id':ident,**body.model_dump()})
    return {'grant_id':str(grant),'revoked':True}

@router.post('/tenants/{tenant}/contracts/{ident}/envelopes',status_code=201)
async def ingest(tenant:UUID,ident:UUID,body:EnvelopeRequest,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    e=Envelope.model_validate_json(body.envelope if isinstance(body.envelope,str) else json.dumps(body.envelope));p=e.payload
    if (p.tenant_id,p.contract_id,p.contract_version,p.contract_digest,p.endpoint_revision)!=(tenant,ident,body.version,body.digest,body.endpoint_revision):raise DiagnosticConflict('Envelope scope mismatch')
    ident=await admin.ingestion.ingest(actor,e,expected_revision=body.expected_revision)
    return {'envelope_id':str(ident),'accepted':True,'diagnostic_truth':False}

@router.get('/tenants/{tenant}/contracts/{ident}/envelopes')
async def envelopes(tenant:UUID,ident:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    async with admin.registry.db.transaction() as s:
        rows=list(await s.scalars(select(IngestionEnvelope).where(IngestionEnvelope.contract_id==ident).order_by(IngestionEnvelope.ingested_at,IngestionEnvelope.envelope_id).limit(limit).offset(offset)))
        items=[{'envelope_id':str(r.envelope_id),'version':r.contract_version,'snapshot_digest':r.snapshot_digest,'captured_at':r.captured_at.isoformat(),'ingested_at':r.ingested_at.isoformat(),'signature_valid_at_ingest':r.signature_valid,'diagnostic_truth':False} for r in rows]
    return page(items,limit,offset)

@router.get('/tenants/{tenant}/contracts/{ident}/envelopes/{envelope}')
async def provenance(tenant:UUID,ident:UUID,envelope:UUID,c=Depends(context)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    async with admin.registry.db.transaction() as s:
        row=await s.get(IngestionEnvelope,envelope)
        if row is None or (row.tenant_id,row.contract_id)!=(tenant,ident):raise HTTPException(404,'Envelope unavailable')
        args={'tenant_id':tenant,'api_id':row.api_id,'contract_id':ident,'contract_version':row.contract_version,'snapshot_digest':row.snapshot_digest}
    try:return await admin.ingestion.verify_provenance(actor,envelope,**args)
    except DiagnosticDenied:return {'envelope_id':str(envelope),'currently_verified':False,'diagnostic_truth':False}


@router.get('/tenants/{tenant}/contracts/{ident}/authority-audit')
async def authority_audit(tenant:UUID,ident:UUID,c=Depends(context),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    admin,actor=c;await admin.read(actor,tenant,ident)
    # UUIDs are unique within each table, not across all entity kinds. Keep
    # action kind paired with its exact tenant/contract reference query.
    scoped=or_(
        and_(AuthorityEventRow.action.in_(('ingestion_grant','ingestion_revoke')),AuthorityEventRow.subject_ref.in_(select(IngestionGrant.grant_id).where(IngestionGrant.contract_id==ident,IngestionGrant.tenant_id==tenant))),
        and_(AuthorityEventRow.action=='ingestion_accepted',AuthorityEventRow.subject_ref.in_(select(IngestionEnvelope.envelope_id).where(IngestionEnvelope.contract_id==ident,IngestionEnvelope.tenant_id==tenant))),
        and_(AuthorityEventRow.action=='verification',AuthorityEventRow.subject_ref.in_(select(AttestationRow.reference).where(AttestationRow.contract_id==ident,AttestationRow.tenant_id==tenant))))
    async with admin.registry.db.transaction() as s:
        rows=list(await s.scalars(select(AuthorityEventRow).where(scoped).order_by(AuthorityEventRow.created_at,AuthorityEventRow.event_id).offset(offset).limit(limit)))
        items=[{'event_id':str(r.event_id),'action':r.action,'actor_id':str(r.actor_id),'subject_ref':str(r.subject_ref),'timestamp':r.created_at.isoformat()} for r in rows]
    return page(items,limit,offset)
