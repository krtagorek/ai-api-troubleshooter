"""Disposable DB and mocked IdP/control proofs; not production credentials."""
import asyncio
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from tests.test_postgres_integration import settings,migrate
from persistence.runtime import open_persistence
from tests.test_registry_integration import service,approved
from tests.test_diagnostic_registry import payload
from diagnostic_contracts.authority import production_registry,VerifiedEvidence
from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
from registry.authorization import AdminAuthorization,Principal
from tests.test_diagnostic_identity import configured,token
pytestmark=pytest.mark.integration

class ProofProvider:
    reused=None
    bad=False
    ttl=240
    async def attest(self,request):
        now=datetime.now(timezone.utc)
        return VerifiedEvidence(self.reused or uuid4(),request.fingerprint() if not self.bad else 'forged',now,now+timedelta(seconds=self.ttl))

async def setup(rt):
    auth,key,keys,rev=configured()
    owner=await auth.authenticate(token(key));admin=await auth.authenticate(token(key));reviewer=await auth.authenticate(token(key))
    # Endpoint registry fixture uses the same authenticated UUID owner/admin.
    from registry.service import RegistryService
    from tests.test_registry_integration import PublishedProof
    from tests.test_registry import policy
    eps=RegistryService(rt.database,admins=AdminAuthorization(frozenset({admin.subject_id})),ownership_verifier=PublishedProof(),resolver=lambda *_:{'93.184.216.34'})
    ch=await eps.register(owner,policy());ep=await eps.verify(owner,ch.registration.api_id,ch.challenge);ep=await eps.transition(admin,ep.api_id,'approved')
    proof=ProofProvider();svc,authority=production_registry(rt.database,identities=auth,global_admins=AdminAuthorization(frozenset({admin.subject_id})),evidence_provider=proof)
    tenant=uuid4()
    for p,role in ((owner,'owner'),(admin,'contract_admin'),(reviewer,'security_reviewer')):
        await authority.set_membership(admin,tenant,p.subject_id,[role],expected_revision=0)
    await authority.bind_endpoint(admin,tenant,ep.api_id,owner.subject_id,expected_revision=0,endpoint_revision=ep.revision)
    return svc,authority,auth,proof,tenant,owner,admin,reviewer,ep,rev

async def staged(s,t,o,a,r,ep):
    v=await s.register(o,t,ep.api_id,ep.revision,payload())
    for who,status in ((o,'owner_verified'),(r,'security_reviewed'),(a,'approved')):
        v=await s.transition(who,t,v['contract_id'],1,status,v['revision'])
    return v


def test_trusted_identity_proofs_permissions_and_revocation(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            with pytest.raises(DiagnosticDenied):await s.register(Principal(o.subject_id),t,ep.api_id,ep.revision,payload())
            with pytest.raises(DiagnosticDenied):await s.register(o,uuid4(),ep.api_id,ep.revision,payload())
            v=await staged(s,t,o,a,r,ep)
            assert v['status']=='approved'
            with pytest.raises(DiagnosticDenied):await s.transition(o,t,v['contract_id'],1,'active',v['revision'])
            await au.set_membership(a,t,r.subject_id,[],expected_revision=1)
            with pytest.raises(DiagnosticDenied):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            await au.set_membership(a,t,r.subject_id,['security_reviewer'],expected_revision=2)
            from persistence.diagnostic_contracts import DiagnosticVersionRow
            async with rt.database.transaction() as session:row=await session.get(DiagnosticVersionRow,(UUID(v['contract_id']),1));reference=row.security_review_ref
            await au.revoke_evidence(a,reference)
            with pytest.raises(DiagnosticDenied):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            rev.allowed=False
            with pytest.raises(DiagnosticDenied):await s.history(o,t,v['contract_id'])
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())


def test_evidence_reuse_expiry_and_ownership_transfer(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            proof.reused=uuid4()
            v=await s.register(o,t,ep.api_id,ep.revision,payload());v=await s.transition(o,t,v['contract_id'],1,'owner_verified',v['revision'])
            with pytest.raises(DiagnosticDenied):await s.transition(r,t,v['contract_id'],1,'security_reviewed',v['revision'])
            proof.reused=None
            await au.bind_endpoint(a,uuid4(),ep.api_id,o.subject_id,expected_revision=1,endpoint_revision=ep.revision)
            with pytest.raises(DiagnosticDenied):await s.history(o,t,v['contract_id'])
            await au.bind_endpoint(a,t,ep.api_id,o.subject_id,expected_revision=2,endpoint_revision=ep.revision)
            with pytest.raises(DiagnosticDenied):await s.history(o,t,v['contract_id'])
            au.clock=lambda:datetime.now(timezone.utc)+timedelta(minutes=6)
            with pytest.raises(DiagnosticDenied):await s.register(o,t,ep.api_id,ep.revision,payload())
    asyncio.run(run())


def test_missing_provider_and_no_silent_backfill(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            au.evidence_provider=None
            with pytest.raises(DiagnosticDenied):await au.bind_endpoint(a,t,ep.api_id,o.subject_id,expected_revision=1,endpoint_revision=ep.revision)
            async with rt.database.transaction() as session:
                assert await session.scalar(text('SELECT version_num FROM app.alembic_version'))=='0008'
            au.identities=None
            with pytest.raises(DiagnosticDenied):await s.register(o,t,ep.api_id,ep.revision,payload())
    asyncio.run(run())


def test_concurrent_membership_changes_and_stale_review_generation(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            v=await staged(s,t,o,a,r,ep)
            v=await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            assert (await s.active_binding(o,t,v['contract_id']))['status']=='active'
            results=await asyncio.gather(*[au.set_membership(a,t,r.subject_id,[],expected_revision=1) for _ in range(2)],return_exceptions=True)
            assert sum(isinstance(x,int) for x in results)==1
            assert sum(isinstance(x,DiagnosticConflict) for x in results)==1
            await au.set_membership(a,t,r.subject_id,['security_reviewer'],expected_revision=2)
            with pytest.raises(DiagnosticDenied):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            with pytest.raises(DiagnosticDenied):await au.set_membership(o,t,o.subject_id,['contract_admin'],expected_revision=1)
    asyncio.run(run())


def test_expired_digest_bound_review_and_revoked_endpoint(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            proof.ttl=10;v=await staged(s,t,o,a,r,ep)
            au.clock=lambda:datetime.now(timezone.utc)+timedelta(seconds=11)
            with pytest.raises(DiagnosticDenied):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            au.clock=lambda:datetime.now(timezone.utc)
            async with rt.database.transaction() as session:
                await session.execute(text("UPDATE app.api_registry SET status='revoked' WHERE api_id=:id"),{'id':ep.api_id})
            with pytest.raises(DiagnosticDenied):await s.history(o,t,v['contract_id'])
    asyncio.run(run())


def test_wrong_verification_context_and_original_owner_change(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,au,auth,proof,t,o,a,r,ep,rev=await setup(rt)
            proof.bad=True
            v=await s.register(o,t,ep.api_id,ep.revision,payload())
            with pytest.raises(DiagnosticDenied):await s.transition(o,t,v['contract_id'],1,'owner_verified',v['revision'])
            async with rt.database.transaction() as session:
                await session.execute(text('UPDATE app.api_registry SET owner_id=:owner WHERE api_id=:id'),{'owner':uuid4(),'id':ep.api_id})
            with pytest.raises(DiagnosticDenied):await s.history(o,t,v['contract_id'])
    asyncio.run(run())
