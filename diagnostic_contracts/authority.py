"""Fresh database authorization and independently supplied verification evidence."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from sqlalchemy import select
from persistence.diagnostic_authority import MembershipRow,EndpointTenantRow,AttestationRow,AuthorityEventRow
from persistence.tables import ApiRegistryRow
from diagnostic_contracts.models import digest
from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict

@dataclass(frozen=True)
class EvidenceRequest:
    kind:str
    tenant:UUID
    api:UUID
    owner:UUID
    subject:UUID
    contract:UUID|None
    version:int|None
    document_digest:str
    endpoint_revision:int
    binding_revision:int
    membership_revision:int|None=None

    def fingerprint(self):return digest({k:str(v) if isinstance(v,UUID) else v for k,v in asdict(self).items()})

@dataclass(frozen=True)
class VerifiedEvidence:
    provider_ref:UUID
    request_digest:str
    issued_at:datetime
    expires_at:datetime

class DurableAuthority:
    def __init__(self,database,*,identities=None,global_admins=None,evidence_provider=None,clock=None):
        self.db,self.identities,self.global_admins,self.evidence_provider=database,identities,global_admins,evidence_provider
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    async def _identity(self,actor):
        if self.identities is None or not await self.identities.current(actor):raise DiagnosticDenied('Fresh trusted identity required')

    async def _control(self,actor):
        await self._identity(actor)
        if self.global_admins is None or self.global_admins.is_admin(actor) is not True:raise DiagnosticDenied('Control-plane administrator required')

    def _audit(self,s,actor,action,subject,details):
        s.add(AuthorityEventRow(event_id=uuid4(),actor_id=actor.subject_id,action=action,subject_ref=subject,details=details,created_at=self.clock()))

    async def set_membership(self,actor,tenant,subject,roles,*,expected_revision,enabled=True,ttl_seconds=3600):
        await self._control(actor)
        if not set(roles)<={'owner','security_reviewer','contract_admin','reader'} or not 1<=ttl_seconds<=86400:raise ValueError('Invalid membership policy')
        async with self.db.transaction() as s:
            row=await s.scalar(select(MembershipRow).where(MembershipRow.tenant_id==tenant,MembershipRow.subject_id==subject).with_for_update())
            if (row.revision if row else 0)!=expected_revision:raise DiagnosticConflict('Membership revision changed')
            if row is None:row=MembershipRow(tenant_id=tenant,subject_id=subject);s.add(row)
            row.revision=expected_revision+1;row.roles=sorted(set(roles));row.enabled=enabled;row.expires_at=self.clock()+timedelta(seconds=ttl_seconds)
            self._audit(s,actor,'membership',subject,{'tenant':str(tenant),'revision':row.revision,'enabled':enabled,'roles':row.roles})
            return row.revision

    async def _proof(self,s,actor,request):
        if self.evidence_provider is None:raise DiagnosticDenied('Independent evidence provider required')
        try:
            import asyncio
            proof=await asyncio.wait_for(self.evidence_provider.attest(request),timeout=2)
            now=self.clock()
            if (not isinstance(proof,VerifiedEvidence) or not isinstance(proof.provider_ref,UUID) or
                proof.request_digest!=request.fingerprint() or not proof.issued_at<=now<proof.expires_at<=now+timedelta(minutes=5)):
                raise ValueError
        except Exception:raise DiagnosticDenied('Independent verification failed') from None
        # Unique provider_ref prevents reuse across versions/tenants/owners.
        prior=await s.scalar(select(AttestationRow).where(AttestationRow.provider_ref==proof.provider_ref))
        if prior:raise DiagnosticDenied('Verification evidence already consumed')
        ref=uuid4();s.add(AttestationRow(reference=ref,provider_ref=proof.provider_ref,tenant_id=request.tenant,
            api_id=request.api,owner_id=request.owner,subject_id=request.subject,contract_id=request.contract,version=request.version,
            digest=request.document_digest,endpoint_revision=request.endpoint_revision,binding_revision=request.binding_revision,
            kind=request.kind,membership_revision=request.membership_revision,issued_at=proof.issued_at,expires_at=proof.expires_at,revoked=False))
        self._audit(s,actor,'verification',ref,{'request_digest':request.fingerprint(),'kind':request.kind})
        await s.flush();return ref

    async def bind_endpoint(self,actor,tenant,api,owner,*,expected_revision,endpoint_revision,require_tenant_membership=False):
        await self._control(actor)
        async with self.db.transaction() as s:
            ep=await s.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id==api).with_for_update())
            if ep is None or ep.owner_id!=owner or ep.revision!=endpoint_revision or ep.status=='revoked':raise DiagnosticDenied('Endpoint ownership/revision mismatch')
            if require_tenant_membership:
                from diagnostic_contracts.service import DiagnosticRegistry
                DiagnosticRegistry._endpoint(ep,endpoint_revision)
                for subject,roles in ((owner,{'owner'}),(actor.subject_id,{'owner','contract_admin'})):
                    member=await s.get(MembershipRow,(tenant,subject),with_for_update={'read':True})
                    if member is None or not member.enabled or member.expires_at<=self.clock() or not set(member.roles)&roles:raise DiagnosticDenied('Current binding membership required')
            row=await s.scalar(select(EndpointTenantRow).where(EndpointTenantRow.api_id==api).with_for_update())
            if (row.revision if row else 0)!=expected_revision:raise DiagnosticConflict('Ownership binding revision changed')
            next_revision=expected_revision+1
            ref=await self._proof(s,actor,EvidenceRequest('endpoint',tenant,api,owner,actor.subject_id,None,None,
                digest({'api':str(api),'tenant':str(tenant),'owner':str(owner),'endpoint_revision':endpoint_revision}),endpoint_revision,next_revision))
            if row is None:row=EndpointTenantRow(api_id=api);s.add(row)
            row.tenant_id=tenant;row.owner_id=owner;row.endpoint_revision=endpoint_revision;row.revision=next_revision;row.verification_ref=ref;row.enabled=True
            self._audit(s,actor,'endpoint_binding',api,{'tenant':str(tenant),'owner':str(owner),'revision':next_revision})
            return next_revision

    async def authorize_in_transaction(self,s,actor,tenant,api,owner,action,binding_revision=None):
        await self._identity(actor)
        row=await s.scalar(select(EndpointTenantRow).where(EndpointTenantRow.api_id==api).with_for_update(read=True))
        member=await s.scalar(select(MembershipRow).where(MembershipRow.tenant_id==tenant,MembershipRow.subject_id==actor.subject_id).with_for_update(read=True))
        now=self.clock()
        if (row is None or not row.enabled or row.tenant_id!=tenant or row.owner_id!=owner or
            (binding_revision is not None and row.revision!=binding_revision) or member is None or not member.enabled or member.expires_at<=now):
            raise DiagnosticDenied('Tenant/ownership authorization unavailable')
        ep=await s.get(ApiRegistryRow,api)
        if ep is None or ep.owner_id!=owner or ep.revision!=row.endpoint_revision or ep.status=='revoked':raise DiagnosticDenied('Endpoint binding stale')
        proof=await s.get(AttestationRow,row.verification_ref,with_for_update={'read':True})
        if proof is None or proof.revoked or proof.expires_at<=now:raise DiagnosticDenied('Endpoint control verification expired')
        roles=set(member.roles)
        needed={'register':'owner','revise':'owner','owner_verified':'owner','security_reviewed':'security_reviewer',
                'approved':'contract_admin','active':'contract_admin','revoked':'contract_admin','superseded':'contract_admin'}
        if action in needed and needed[action] not in roles:raise DiagnosticDenied('Current permission required')
        if action in {'history','read_active'} and not roles.intersection({'owner','security_reviewer','contract_admin','reader'}):raise DiagnosticDenied('Read permission required')
        return row.revision

    async def issue_in_transaction(self,s,method,actor,row,v):
        kind='ownership' if method=='verify' else 'security'
        membership=await s.get(MembershipRow,(row.tenant_id,actor.subject_id),with_for_update={'read':True})
        if membership is None:raise DiagnosticDenied('Membership unavailable')
        return await self._proof(s,actor,EvidenceRequest(kind,row.tenant_id,row.api_id,row.owner_id,actor.subject_id,
            row.contract_id,v.version,v.digest,v.endpoint_revision,row.binding_revision,membership.revision))

    async def validate_in_transaction(self,s,actor,row,v):
        await self._identity(actor)
        for ref,kind,subject in ((v.owner_verification_ref,'ownership',row.owner_id),(v.security_review_ref,'security',v.reviewer_id)):
            evidence=await s.get(AttestationRow,ref,with_for_update={'read':True}) if ref else None
            if (evidence is None or evidence.revoked or evidence.expires_at<=self.clock() or
                (evidence.tenant_id,evidence.api_id,evidence.owner_id,evidence.subject_id,evidence.contract_id,evidence.version,evidence.digest,evidence.endpoint_revision,evidence.binding_revision,evidence.kind)!=
                (row.tenant_id,row.api_id,row.owner_id,subject,row.contract_id,v.version,v.digest,v.endpoint_revision,row.binding_revision,kind)):
                raise DiagnosticDenied('Version verification stale or revoked')
            member=await s.get(MembershipRow,(row.tenant_id,subject),with_for_update={'read':True})
            role='owner' if kind=='ownership' else 'security_reviewer'
            if member is None or not member.enabled or member.expires_at<=self.clock() or role not in member.roles or member.revision!=evidence.membership_revision:raise DiagnosticDenied('Verifier membership changed')

    async def revoke_evidence(self,actor,reference):
        await self._control(actor)
        async with self.db.transaction() as s:
            row=await s.get(AttestationRow,reference,with_for_update=True)
            if row is None:raise DiagnosticDenied('Evidence unavailable')
            row.revoked=True;self._audit(s,actor,'evidence_revoked',reference,{})


def production_registry(database,*,identities,global_admins,evidence_provider):
    """Explicit server composition only; never derive grants from request bodies."""
    from diagnostic_contracts.service import DiagnosticRegistry
    authority=DurableAuthority(database,identities=identities,global_admins=global_admins,evidence_provider=evidence_provider)
    return DiagnosticRegistry(database,tenants=authority,admins=global_admins,ownership=authority,security=authority),authority
