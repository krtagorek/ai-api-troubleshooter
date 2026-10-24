"""Durable lifecycle only. No semantic evaluator, graph node or HTTP execution."""
import asyncio
from datetime import datetime,timezone
from typing import Protocol
from uuid import UUID,uuid4
from sqlalchemy import select,func
from persistence.diagnostic_contracts import DiagnosticContractRow as Identity,DiagnosticVersionRow as Version,DiagnosticEventRow as Event
from persistence.tables import ApiRegistryRow
from registry.models import ApiPolicy
from registry.policy import policy_digest
from registry.authorization import Principal
from diagnostic_contracts.models import document,digest

class DiagnosticDenied(PermissionError):pass
class DiagnosticConflict(ValueError):pass

class TenantAuthorizer(Protocol):
    async def authorize(self,actor:Principal,tenant:UUID,api:UUID,owner:UUID,action:str)->bool: ...

class OwnershipVerifier(Protocol):
    async def verify(self,actor,tenant,api,contract_digest)->UUID | None: ...

class SecurityReviewer(Protocol):
    async def review(self,actor,tenant,api,contract_digest)->UUID | None: ...

class DiagnosticRegistry:
    def __init__(self,database,*,tenants=None,admins=None,ownership=None,security=None):
        self.db,self.tenants,self.admins,self.ownership,self.security=database,tenants,admins,ownership,security

    async def _allowed(self,actor,tenant,api,owner,action,*,session=None,binding_revision=None):
        if not isinstance(actor,Principal) or self.tenants is None:raise DiagnosticDenied('Authenticated tenant authorization required')
        try:
            if hasattr(self.tenants,'authorize_in_transaction'):
                if session is None:raise DiagnosticDenied('Transactional authorization required')
                return await asyncio.wait_for(self.tenants.authorize_in_transaction(session,actor,tenant,api,owner,action,binding_revision),timeout=2)
            ok=await asyncio.wait_for(self.tenants.authorize(actor,tenant,api,owner,action),timeout=2)
        except Exception:raise DiagnosticDenied('Tenant authorization unavailable') from None
        if ok is not True:raise DiagnosticDenied('Diagnostic contract unavailable')

    def _admin(self,actor,owner,reviewer=None):
        try:allowed=self.admins is not None and self.admins.is_admin(actor) is True
        except Exception:allowed=False
        if not allowed or actor.subject_id in (owner,reviewer):
            raise DiagnosticDenied('Independent administrator required')

    async def _reference(self,provider,method,actor,row,version,session):
        if provider is None:raise DiagnosticDenied('Verification provider required')
        try:
            if hasattr(provider,'issue_in_transaction'):
                ref=await asyncio.wait_for(provider.issue_in_transaction(session,method,actor,row,version),timeout=2)
            else:ref=await asyncio.wait_for(getattr(provider,method)(actor,row.tenant_id,row.api_id,version.digest),timeout=2)
        except Exception:raise DiagnosticDenied('Verification provider unavailable') from None
        if not isinstance(ref,UUID):raise DiagnosticDenied('Verified opaque reference required')
        return ref

    @staticmethod
    def _snapshot(row,v):
        return {'contract_id':str(row.contract_id),'tenant_id':str(row.tenant_id),'owner_id':str(row.owner_id),
            'api_id':str(row.api_id),'version':v.version,'endpoint_revision':v.endpoint_revision,
            'document':v.document,'digest':v.digest,'status':v.status,'revision':row.revision,
            'revocation_revision':row.revocation_revision}

    async def _event(self,session,row,v,actor,action,old=None):
        prior=await session.scalar(select(Event).where(Event.contract_id==row.contract_id).order_by(Event.sequence.desc()).limit(1))
        now=datetime.now(timezone.utc)
        payload={'contract_id':str(row.contract_id),'tenant_id':str(row.tenant_id),'version':v.version,
            'actor_id':str(actor.subject_id),'action':action,'before':old,'after':v.status,'document_digest':v.digest,
            'revision':row.revision,'revocation_revision':row.revocation_revision,'timestamp':now.isoformat(),
            'owner_verification_ref':str(v.owner_verification_ref) if v.owner_verification_ref else None,
            'security_review_ref':str(v.security_review_ref) if v.security_review_ref else None}
        prev=prior.event_hash if prior else None
        session.add(Event(contract_id=row.contract_id,tenant_id=row.tenant_id,version=v.version,
            sequence=row.revision,actor_id=actor.subject_id,action=action,previous_hash=prev,
            event_hash=digest({'previous':prev,'payload':payload}),payload=payload,created_at=now))

    async def register(self,actor,tenant,api,endpoint_revision,value):
        if type(endpoint_revision) is not int or endpoint_revision<1:raise DiagnosticConflict('Positive integer endpoint revision required')
        tenant,api=UUID(str(tenant)),UUID(str(api));doc=document(value);raw=doc.model_dump(mode='json')
        async with self.db.transaction() as s:
            endpoint=await s.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id==api).with_for_update())
            if endpoint is None:raise DiagnosticDenied('Endpoint unavailable')
            binding=await self._allowed(actor,tenant,api,endpoint.owner_id,'register',session=s)
            if actor.subject_id!=endpoint.owner_id:raise DiagnosticDenied('Endpoint owner required')
            self._endpoint(endpoint,endpoint_revision)
            now=datetime.now(timezone.utc)
            row=Identity(contract_id=uuid4(),tenant_id=tenant,owner_id=actor.subject_id,api_id=api,revision=1,revocation_revision=0,binding_revision=binding,created_at=now)
            s.add(row);await s.flush()
            v=Version(contract_id=row.contract_id,version=1,tenant_id=tenant,endpoint_revision=endpoint_revision,
                document=raw,digest=digest(raw),status='draft',revision=1,created_at=now)
            s.add(v);await s.flush();await self._event(s,row,v,actor,'registered')
            return self._snapshot(row,v)

    @staticmethod
    def _endpoint(endpoint,revision):
        if (endpoint is None or endpoint.status!='approved' or endpoint.revision!=revision
                or endpoint.approved_revision!=revision or endpoint.verified_revision!=revision
                or endpoint.approval_digest!=policy_digest(ApiPolicy.model_validate(endpoint.policy),endpoint.pinned_ips,revision)):
            raise DiagnosticConflict('Endpoint approval revision mismatch')

    async def _locked(self,s,actor,tenant,ident,action):
        # Endpoint first in every mutation, then identity: consistent lock order.
        key=UUID(str(ident));tenant=UUID(str(tenant))
        found=await s.scalar(select(Identity).where(Identity.contract_id==key,Identity.tenant_id==tenant))
        if found is None:raise DiagnosticDenied('Diagnostic contract unavailable')
        endpoint=await s.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id==found.api_id).with_for_update())
        await self._allowed(actor,tenant,found.api_id,found.owner_id,action,session=s,binding_revision=found.binding_revision)
        if hasattr(self.tenants,'authorize_in_transaction') and found.binding_revision is None:raise DiagnosticDenied('Historical contract requires explicit binding migration')
        row=await s.scalar(select(Identity).where(Identity.contract_id==key,Identity.tenant_id==tenant).with_for_update().execution_options(populate_existing=True))
        return row,endpoint

    async def revise(self,actor,tenant,ident,expected_revision,endpoint_revision,value,*,expected_digest=None,expected_version=None):
        if any(type(n) is not int or n<1 for n in (expected_revision,endpoint_revision)):raise DiagnosticConflict('Positive integer revisions required')
        raw=document(value).model_dump(mode='json')
        async with self.db.transaction() as s:
            row,endpoint=await self._locked(s,actor,tenant,ident,'revise')
            if actor.subject_id!=row.owner_id:raise DiagnosticDenied('Owner required')
            if row.revision!=expected_revision:raise DiagnosticConflict('Stale lifecycle revision')
            if expected_digest is not None:
                current=await s.get(Version,(row.contract_id,expected_version))
                if current is None or current.digest!=expected_digest:raise DiagnosticConflict('Document binding mismatch')
            self._endpoint(endpoint,endpoint_revision)
            number=(await s.scalar(select(func.max(Version.version)).where(Version.contract_id==row.contract_id)))+1
            row.revision+=1
            v=Version(contract_id=row.contract_id,version=number,tenant_id=row.tenant_id,endpoint_revision=endpoint_revision,
                document=raw,digest=digest(raw),status='draft',revision=row.revision,created_at=datetime.now(timezone.utc))
            s.add(v);await s.flush();await self._event(s,row,v,actor,'revised')
            return self._snapshot(row,v)

    async def transition(self,actor,tenant,ident,version,target,expected_revision,*,superseded_by=None,expected_digest=None,endpoint_revision=None):
        if any(type(n) is not int or n<1 for n in (version,expected_revision)):raise DiagnosticConflict('Positive integer revisions required')
        if target not in {'owner_verified','security_reviewed','approved','active','revoked','superseded'}:raise DiagnosticConflict('Unknown transition')
        async with self.db.transaction() as s:
            row,endpoint=await self._locked(s,actor,tenant,ident,target)
            v=await s.get(Version,(row.contract_id,version))
            if v is None:raise DiagnosticDenied('Version unavailable')
            if row.revision!=expected_revision:raise DiagnosticConflict('Stale lifecycle revision')
            if expected_digest is not None and v.digest!=expected_digest:raise DiagnosticConflict('Document digest mismatch')
            if endpoint_revision is not None and v.endpoint_revision!=endpoint_revision:raise DiagnosticConflict('Endpoint revision mismatch')
            old=v.status
            if old in {'revoked','superseded'}:raise DiagnosticConflict('Terminal version')
            if target not in {'revoked','superseded'}:self._endpoint(endpoint,v.endpoint_revision)
            if target=='owner_verified':
                if old!='draft' or actor.subject_id!=row.owner_id:raise DiagnosticDenied('Draft owner verification required')
                v.owner_verification_ref=await self._reference(self.ownership,'verify',actor,row,v,s)
            elif target=='security_reviewed':
                if old!='owner_verified' or actor.subject_id==row.owner_id:raise DiagnosticDenied('Independent security review required')
                v.security_review_ref=await self._reference(self.security,'review',actor,row,v,s);v.reviewer_id=actor.subject_id
            else:
                if target in {'approved','active'} and hasattr(self.ownership,'validate_in_transaction'):
                    await self.ownership.validate_in_transaction(s,actor,row,v)
                self._admin(actor,row.owner_id,v.reviewer_id if target in {'approved','active'} else None)
                if target=='approved':
                    if old!='security_reviewed':raise DiagnosticConflict('Security review required')
                    v.approved_by=actor.subject_id
                elif target=='active':
                    if old!='approved':raise DiagnosticConflict('Approval required')
                    active=await s.scalar(select(Version).where(Version.contract_id==row.contract_id,Version.status=='active'))
                    if active:raise DiagnosticConflict('Explicit supersession required')
                    v.activated_by=actor.subject_id
                elif target=='revoked':v.revoked_by=actor.subject_id;row.revocation_revision+=1
                elif target=='superseded':
                    successor=await s.get(Version,(row.contract_id,superseded_by)) if type(superseded_by) is int else None
                    if successor is None or successor.version<=version or successor.status!='approved':raise DiagnosticConflict('Approved newer successor required')
                    v.superseded_by=successor.version;row.revocation_revision+=1
            row.revision+=1;v.status=target;v.revision=row.revision
            await self._event(s,row,v,actor,target,old);await s.flush()
            return self._snapshot(row,v)

    async def history(self,actor,tenant,ident):
        # Read-only: no audit insertion, lifecycle update, endpoint execution or graph action.
        async with self.db.transaction() as s:
            row=await s.scalar(select(Identity).where(Identity.contract_id==UUID(str(ident)),Identity.tenant_id==UUID(str(tenant))).with_for_update(read=True))
            if row is None:raise DiagnosticDenied('Diagnostic contract unavailable')
            await self._allowed(actor,row.tenant_id,row.api_id,row.owner_id,'history',session=s,binding_revision=row.binding_revision)
            if hasattr(self.tenants,'authorize_in_transaction') and row.binding_revision is None:raise DiagnosticDenied('Historical contract is unbound')
            events=list(await s.scalars(select(Event).where(Event.contract_id==row.contract_id).order_by(Event.sequence)))
            if len(events)!=row.revision:raise DiagnosticConflict('Audit sequence incomplete')
            previous=None
            for seq,e in enumerate(events,1):
                if e.sequence!=seq or e.previous_hash!=previous or e.event_hash!=digest({'previous':previous,'payload':e.payload}):raise DiagnosticConflict('Audit integrity failure')
                previous=e.event_hash
            return [e.payload for e in events]

    async def active_binding(self,actor,tenant,ident):
        """Fresh read authorization interface, NOT an execution permit or routing input."""
        async with self.db.transaction() as s:
            row,endpoint=await self._locked(s,actor,tenant,ident,'read_active')
            v=await s.scalar(select(Version).where(Version.contract_id==row.contract_id,Version.status=='active'))
            if v is None:raise DiagnosticDenied('No active diagnostic approval')
            self._endpoint(endpoint,v.endpoint_revision)
            if hasattr(self.ownership,'validate_in_transaction'):await self.ownership.validate_in_transaction(s,actor,row,v)
            return self._snapshot(row,v)
