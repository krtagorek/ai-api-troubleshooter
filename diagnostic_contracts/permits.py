"""Opt-in authorization fence for trusted, pure local semantic evaluation only.

No graph integration, external tools, automatic activation, or checkpoint authority.
"""
import asyncio
import hashlib
import json
import secrets
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID,uuid4
from dataclasses import dataclass
from pydantic import SecretStr
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from diagnostic_contracts.ingestion import Envelope,digest
from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
from tools.redaction import redact
from tools.output_safety import protect_generated
from persistence.permits import AuthorizationFence,AuthorizationPermit,PermitConsumption
from persistence.ingestion import IngestionEnvelope,IngestionGrant
from persistence.diagnostic_contracts import DiagnosticVersionRow
from persistence.diagnostic_authority import MembershipRow,EndpointTenantRow,AttestationRow

@dataclass(frozen=True)
class Permit:
    permit_id: UUID
    capability: SecretStr

class PermitService:
    def __init__(self,ingestion,*,evaluator=None,require_active=False,operation="qualified_semantic_evaluation_v1",subject_digest=None):
        self.ingestion=ingestion
        self.database=ingestion.database
        self.authority=ingestion.authority
        self.clock=ingestion.clock
        # Trusted server composition only: synchronous pure deterministic code.
        self.evaluator=evaluator
        self.require_active=require_active
        if operation not in ('qualified_semantic_evaluation_v1','diagnostic_report_publication_v1'):
            raise ValueError('Unsupported protected operation')
        if subject_digest is not None and not re.fullmatch(r'[a-f0-9]{64}',subject_digest):raise ValueError('Invalid subject digest')
        self.operation,self.subject_digest=operation,subject_digest

    async def _context(self,s,actor,row):
        e=Envelope.model_validate_json(json.dumps(row.document));p=e.payload
        contract=await self.ingestion._binding(s,actor,p)
        await self.ingestion._grant(s,p)
        if await self.ingestion._key(e)!=row.key_fingerprint or digest(p.wire())!=row.payload_digest:
            raise DiagnosticDenied('Provenance integrity mismatch')
        try:key=await asyncio.wait_for(self.ingestion.keys.resolve(e.key_id),2)
        except Exception:raise DiagnosticDenied('Fresh key authority unavailable') from None
        # _key validates validity/revocation; repeat after metadata lookup to fence rotation.
        if await self.ingestion._key(e)!=row.key_fingerprint:
            raise DiagnosticDenied('Key changed')
        identity=self.authority.identities
        expiry=await asyncio.wait_for(identity.authorization_expiry(actor),2)
        if expiry is None:raise DiagnosticDenied('Fresh identity lease required')
        member=await s.get(MembershipRow,(p.tenant_id,actor.subject_id),with_for_update={'read':True})
        producer=await s.get(MembershipRow,(p.tenant_id,p.producer_id),with_for_update={'read':True})
        if producer is None or not producer.enabled or producer.expires_at<=self.clock():
            raise DiagnosticDenied('Producer membership unavailable')
        binding=await s.get(EndpointTenantRow,p.api_id)
        version=await s.get(DiagnosticVersionRow,(p.contract_id,p.contract_version))
        if self.require_active and version.status!='active':raise DiagnosticDenied('Active contract required')
        grant=await s.get(IngestionGrant,p.grant_id)
        proofs=[await s.get(AttestationRow,ref) for ref in (binding.verification_ref,version.owner_verification_ref,version.security_review_ref)]
        reviewers=[await s.get(MembershipRow,(p.tenant_id,subject),with_for_update={'read':True}) for subject in (contract.owner_id,version.reviewer_id)]
        # Endpoint lock was obtained by _binding, before fence row creation/lock.
        await s.execute(insert(AuthorizationFence).values(api_id=p.api_id,generation=1,blocked_keys=[]).on_conflict_do_nothing())
        fence=await s.get(AuthorizationFence,p.api_id,with_for_update=True)
        if digest([e.key_id,row.key_fingerprint]) in fence.blocked_keys:
            raise DiagnosticDenied('Producer key durably revoked')
        until=min([self.clock()+timedelta(seconds=5),expiry,key.expires_at,key.checked_until,grant.expires_at,
                   p.captured_at+timedelta(seconds=120),member.expires_at,producer.expires_at]
                  +[x.expires_at for x in proofs+reviewers])
        dbnow=await s.scalar(select(func.clock_timestamp()))
        until=min(until,dbnow+timedelta(seconds=5))
        if abs((dbnow-self.clock()).total_seconds())>1 or until<=dbnow or p.captured_at>dbnow:
            raise DiagnosticDenied('Authorization validity or clock discipline exhausted')
        context={'operation':self.operation,'tenant':str(p.tenant_id),'actor':str(actor.subject_id),
          'contract':str(p.contract_id),'version':p.contract_version,'contract_digest':p.contract_digest,
          'lifecycle_revision':contract.revision,'version_revision':version.revision,'revocation_revision':contract.revocation_revision,
          'endpoint':str(p.api_id),'endpoint_revision':p.endpoint_revision,'binding_generation':binding.revision,
          'membership_generation':member.revision,'producer_membership_generation':producer.revision,
          'reviewer_generations':[x.revision for x in reviewers],
          'fence_generation':fence.generation,'envelope':str(row.envelope_id),'snapshot_digest':row.snapshot_digest,
          'payload_digest':row.payload_digest,'producer':str(p.producer_id),'grant':str(p.grant_id),
          'key_id':e.key_id,'key_fingerprint':row.key_fingerprint,'correlation_id':p.correlation_id,'resource':p.resource}
        if self.subject_digest is not None:context["subject_digest"]=self.subject_digest
        return context,until,p,version

    def _audit(self,s,actor,action,row,details):
        self.authority._audit(s,actor,action,row.permit_id,{'tenant':str(row.tenant_id),'endpoint':str(row.api_id),
            'contract':row.context['contract'],'correlation_id':row.context['correlation_id'],**details})

    async def issue(self,actor,envelope_id,*,tenant_id,api_id,snapshot_digest):
        async with self.database.transaction() as s:
            row=await s.get(IngestionEnvelope,envelope_id)
            if row is None or (row.tenant_id,row.api_id,row.snapshot_digest)!=(tenant_id,api_id,snapshot_digest):
                raise DiagnosticDenied('Exact provenance scope required')
            context,until,_,_=await self._context(s,actor,row)
            secret=secrets.token_urlsafe(32)
            permit=AuthorizationPermit(permit_id=uuid4(),capability_hash=hashlib.sha256(secret.encode()).hexdigest(),
                tenant_id=tenant_id,actor_id=actor.subject_id,api_id=api_id,envelope_id=envelope_id,
                context=context,issued_at=self.clock(),expires_at=until)
            s.add(permit);self._audit(s,actor,'permit_issued',permit,{'context_digest':digest(context),'expires_at':until.isoformat()})
        return Permit(permit.permit_id,SecretStr(secret))

    async def revoke(self,actor,api_id,*,expected_generation,key_id=None,key_fingerprint=None):
        """Durably invalidate every previously issued permit for this endpoint."""
        if type(expected_generation) is not int or expected_generation<1:
            raise DiagnosticConflict('Positive fence generation required')
        if (key_id is None)!=(key_fingerprint is None):raise DiagnosticDenied('Complete key scope required')
        if key_id is not None and (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key_id) or not re.fullmatch(r'[a-f0-9]{64}',key_fingerprint)):
            raise DiagnosticDenied('Invalid key scope')
        await self.authority._control(actor)
        from persistence.tables import ApiRegistryRow
        async with self.database.transaction() as s:
            endpoint=await s.get(ApiRegistryRow,api_id,with_for_update=True)
            if endpoint is None:raise DiagnosticDenied('Endpoint unavailable')
            await s.execute(insert(AuthorizationFence).values(api_id=api_id,generation=1,blocked_keys=[]).on_conflict_do_nothing())
            fence=await s.get(AuthorizationFence,api_id,with_for_update=True)
            if fence.generation!=expected_generation:raise DiagnosticConflict('Fence revision mismatch')
            fence.generation+=1
            if key_id is not None:
                fence.blocked_keys=sorted(set(fence.blocked_keys+[digest([key_id,key_fingerprint])]))
            binding=await s.get(EndpointTenantRow,api_id)
            self.authority._audit(s,actor,'permit_revocation',api_id,{'generation':fence.generation,'endpoint':str(api_id),'tenant':str(binding.tenant_id) if binding else None,'contracts':'all_endpoint_contracts','key_scope_digest':digest([key_id,key_fingerprint]) if key_id else None})
            return fence.generation

    @asynccontextmanager
    async def _transaction(self,session):
        if session is None:
            async with self.database.transaction() as owned:yield owned
        else:
            if not session.in_transaction():raise DiagnosticDenied('Publication transaction required')
            yield session

    async def evaluate(self,actor,permit,*,tenant_id,api_id,snapshot_digest,_session=None):
        """Checks and pure evaluation share one transaction. Nothing escapes pre-commit.

        Crash rollback permits a fresh-checked retry of pure computation only, never
        external side effects. A committed consumption is permanently single-use.
        """
        await self.authority._identity(actor)
        denied=False
        async with self._transaction(_session) as s:
            row=await s.get(AuthorizationPermit,permit.permit_id)
            # Do not attribute forged/unknown capabilities to another actor.
            if (row is None or row.actor_id!=actor.subject_id or
                not secrets.compare_digest(row.capability_hash,hashlib.sha256(permit.capability.get_secret_value().encode()).hexdigest())):
                raise DiagnosticDenied('Invalid authorization capability')
            envelope=await s.get(IngestionEnvelope,row.envelope_id)
            try:
                if row.tenant_id!=tenant_id or row.api_id!=api_id:
                    raise DiagnosticDenied('Capability context mismatch')
                context,until,p,version=await self._context(s,actor,envelope)
                if (row.context!=context or snapshot_digest!=context['snapshot_digest'] or
                    self.clock()>=min(row.expires_at,until) or await s.get(PermitConsumption,row.permit_id)):
                    raise DiagnosticDenied('Stale or consumed permit')
                if self.evaluator is None:raise DiagnosticDenied('Trusted evaluator unavailable')
                # A connection loss releases locks; require a live transaction
                # immediately before pure evaluation and again before publishing.
                live=await s.scalar(select(AuthorizationFence).where(AuthorizationFence.api_id==row.api_id).with_for_update().execution_options(populate_existing=True))
                dbnow=await s.scalar(select(func.clock_timestamp()))
                if live.generation!=context['fence_generation'] or dbnow>=min(row.expires_at,until):
                    raise DiagnosticDenied('Authorization fence expired')
            except (DiagnosticDenied,DiagnosticConflict,AttributeError,TimeoutError):
                denied=True
                self._audit(s,actor,'permit_denied',row,{'reason':'stale_or_unavailable_authority'})
            if not denied:
                # Linearization point: authoritative rows locked and checks passed.
                # Only pure local work is admitted; results escape after commit.
                result=self.evaluator(json.loads(json.dumps(p.snapshot)),json.loads(json.dumps(version.document)))
                if hasattr(result,'__await__'):
                    if hasattr(result,'close'):result.close()
                    raise DiagnosticDenied('Only pure synchronous evaluation supported')
                result=redact(protect_generated(result))
                if max(self.clock(),await s.scalar(select(func.clock_timestamp())))>=min(row.expires_at,until):
                    denied=True
                    self._audit(s,actor,'permit_denied',row,{'reason':'evaluation_exceeded_validity'})
                else:
                    s.add(PermitConsumption(permit_id=row.permit_id,consumed_at=self.clock(),result_digest=digest(result)))
                    self._audit(s,actor,'permit_consumed',row,{'context_digest':digest(context),'result_digest':digest(result)})
        if denied:raise DiagnosticDenied('Stale or unavailable authorization')
        return result
