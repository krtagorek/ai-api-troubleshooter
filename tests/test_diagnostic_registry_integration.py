"""Real isolated PostgreSQL; all identity/verifier adapters are TEST ONLY."""
import asyncio
from uuid import uuid4,UUID
import pytest
from sqlalchemy import text
from persistence.runtime import open_persistence
from persistence.database import PersistenceError
from registry.authorization import Principal,AdminAuthorization
from diagnostic_contracts.service import DiagnosticRegistry,DiagnosticDenied,DiagnosticConflict
from tests.test_postgres_integration import settings,migrate
from tests.test_registry_integration import service,approved
from tests.test_diagnostic_registry import payload
pytestmark=pytest.mark.integration


class TenantAdapter:
    def __init__(self,tenant,actors):self.tenant,self.actors=tenant,set(actors)
    async def authorize(self,actor,tenant,api,owner,action):return tenant==self.tenant and actor.subject_id in self.actors

class Proof:
    async def verify(self,*a):return uuid4()
    async def review(self,*a):return uuid4()

async def setup(runtime):
    endpoints,owner,admin=service(runtime);ep=await approved(endpoints,owner,admin)
    tenant=uuid4();reviewer=Principal(uuid4())
    s=DiagnosticRegistry(runtime.database,tenants=TenantAdapter(tenant,[owner.subject_id,admin.subject_id,reviewer.subject_id]),admins=AdminAuthorization(frozenset({admin.subject_id})),ownership=Proof(),security=Proof())
    return s,tenant,owner,admin,reviewer,ep

async def draft(s,t,o,ep):return await s.register(o,t,ep.api_id,ep.revision,payload())
async def staged(s,t,o,a,r,v):
    for actor,status in [(o,'owner_verified'),(r,'security_reviewed'),(a,'approved')]:
        v=await s.transition(actor,t,v['contract_id'],v['version'],status,v['revision'])
    return v


def test_durable_lifecycle_immutability_audit_and_schema(settings):
    async def run():
        async with open_persistence(settings) as rt:
            async with rt.database.transaction() as session:
                assert await session.scalar(text('SELECT version_num FROM app.alembic_version'))=='0008'
            s,t,o,a,r,ep=await setup(rt);v=await draft(s,t,o,ep)
            with pytest.raises(DiagnosticDenied):await s.active_binding(o,t,v['contract_id'])
            v=await staged(s,t,o,a,r,v);v=await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            assert (await s.active_binding(o,t,v['contract_id']))['digest']==v['digest']
            h=await s.history(o,t,v['contract_id']);assert len(h)==5 and h[-1]['after']=='active'
            for sql in ["UPDATE app.diagnostic_contract_versions SET document='{}'::jsonb",'DELETE FROM app.diagnostic_contract_events','TRUNCATE app.diagnostic_contract_events']:
                with pytest.raises(PersistenceError):
                    async with rt.database.transaction() as session:await session.execute(text(sql))
            assert await s.history(o,t,v['contract_id'])==h
            v=await s.transition(a,t,v['contract_id'],1,'revoked',v['revision'])
            assert v['revocation_revision']==1
            with pytest.raises(DiagnosticDenied):await s.active_binding(o,t,v['contract_id'])
            with pytest.raises(DiagnosticConflict):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())


def test_separation_tenants_and_missing_providers(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);v=await draft(s,t,o,ep)
            with pytest.raises(DiagnosticDenied):await s.history(o,uuid4(),v['contract_id'])
            with pytest.raises(DiagnosticDenied):await s.history(Principal(uuid4()),t,v['contract_id'])
            with pytest.raises(DiagnosticDenied):await s.register(None,t,ep.api_id,ep.revision,payload())
            s.tenants=None
            with pytest.raises(DiagnosticDenied):await draft(s,t,o,ep)
            s.tenants=TenantAdapter(t,[o.subject_id,a.subject_id,r.subject_id]);s.ownership=None
            with pytest.raises(DiagnosticDenied):await s.transition(o,t,v['contract_id'],1,'owner_verified',1)
            s.ownership=Proof();v=await s.transition(o,t,v['contract_id'],1,'owner_verified',1)
            with pytest.raises(DiagnosticDenied):await s.transition(o,t,v['contract_id'],1,'security_reviewed',v['revision'])
            s.security=None
            with pytest.raises(DiagnosticDenied):await s.transition(r,t,v['contract_id'],1,'security_reviewed',v['revision'])
            s.security=Proof();v=await s.transition(r,t,v['contract_id'],1,'security_reviewed',v['revision'])
            with pytest.raises(DiagnosticDenied):await s.transition(o,t,v['contract_id'],1,'approved',v['revision'])
            with pytest.raises(DiagnosticDenied):await s.transition(r,t,v['contract_id'],1,'approved',v['revision'])
            assert len(await s.history(o,t,v['contract_id']))==3
    asyncio.run(run())


def test_concurrent_approval_revocation_revision_and_supersession(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);v=await draft(s,t,o,ep)
            for who,status in [(o,'owner_verified'),(r,'security_reviewed')]:v=await s.transition(who,t,v['contract_id'],1,status,v['revision'])
            results=await asyncio.gather(*[s.transition(a,t,v['contract_id'],1,'approved',v['revision']) for _ in range(2)],return_exceptions=True)
            assert sum(isinstance(x,dict) for x in results)==1
            assert sum(isinstance(x,DiagnosticConflict) for x in results)==1
            v=next(x for x in results if isinstance(x,dict));v=await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            second=await s.revise(o,t,v['contract_id'],v['revision'],ep.revision,payload())
            assert second['status']=='draft' and second['version']==2
            second=await staged(s,t,o,a,r,second)
            with pytest.raises(DiagnosticConflict):await s.transition(a,t,v['contract_id'],2,'active',second['revision'])
            old=await s.transition(a,t,v['contract_id'],1,'superseded',second['revision'],superseded_by=2)
            second=await s.transition(a,t,v['contract_id'],2,'active',old['revision'])
            results=await asyncio.gather(*[s.transition(a,t,v['contract_id'],2,'revoked',second['revision']) for _ in range(2)],return_exceptions=True)
            assert sum(isinstance(x,dict) for x in results)==1
            assert sum(isinstance(x,DiagnosticConflict) for x in results)==1
            assert [e['revision'] for e in await s.history(o,t,v['contract_id'])]==list(range(1,13))
    asyncio.run(run())


def test_endpoint_changes_deny_binding_and_approval_but_allow_revocation(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);v=await staged(s,t,o,a,r,await draft(s,t,o,ep))
            async with rt.database.transaction() as session:
                await session.execute(text('UPDATE app.api_registry SET revision=revision+1 WHERE api_id=:id'),{'id':ep.api_id})
            with pytest.raises(DiagnosticConflict):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            v=await s.transition(a,t,v['contract_id'],1,'revoked',v['revision']);assert v['status']=='revoked'
            with pytest.raises(DiagnosticConflict):await draft(s,t,o,ep)
    asyncio.run(run())


def test_duplicate_activation_and_revision_changes_are_serialized(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);v=await staged(s,t,o,a,r,await draft(s,t,o,ep))
            results=await asyncio.gather(*[s.transition(a,t,v['contract_id'],1,'active',v['revision']) for _ in range(2)],return_exceptions=True)
            assert sum(isinstance(x,dict) for x in results)==1 and sum(isinstance(x,DiagnosticConflict) for x in results)==1
            v=next(x for x in results if isinstance(x,dict))
            results=await asyncio.gather(*[s.revise(o,t,v['contract_id'],v['revision'],ep.revision,payload()) for _ in range(2)],return_exceptions=True)
            assert sum(isinstance(x,dict) for x in results)==1 and sum(isinstance(x,DiagnosticConflict) for x in results)==1
            current=next(x for x in results if isinstance(x,dict))
            with pytest.raises(DiagnosticConflict):await s.transition(a,t,v['contract_id'],current['version'],'active',current['revision'])
            assert (await s.active_binding(o,t,v['contract_id']))['version']==1
    asyncio.run(run())


def test_missing_or_unavailable_authority_fails_closed(settings):
    class Down:
        async def authorize(self,*a):raise RuntimeError('private detail must not escape')
    class DownProof:
        async def verify(self,*a):raise RuntimeError('private detail must not escape')
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);s.tenants=Down()
            with pytest.raises(DiagnosticDenied,match='unavailable'):await draft(s,t,o,ep)
            s.tenants=TenantAdapter(t,[o.subject_id,a.subject_id,r.subject_id]);v=await draft(s,t,o,ep);s.ownership=DownProof()
            with pytest.raises(DiagnosticDenied,match='unavailable'):await s.transition(o,t,v['contract_id'],1,'owner_verified',1)
            s.ownership=Proof();v=await staged(s,t,o,a,r,v);s.admins=None
            with pytest.raises(DiagnosticDenied):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
    asyncio.run(run())


def test_endpoint_policy_digest_and_audit_column_integrity(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,t,o,a,r,ep=await setup(rt);v=await staged(s,t,o,a,r,await draft(s,t,o,ep))
            async with rt.database.transaction() as session:
                await session.execute(text("UPDATE app.api_registry SET approval_digest=:bad WHERE api_id=:id"),{'id':ep.api_id,'bad':'0'*64})
            with pytest.raises(DiagnosticConflict):await s.transition(a,t,v['contract_id'],1,'active',v['revision'])
            assert len(await s.history(o,t,v['contract_id']))==4
    asyncio.run(run())


def test_additive_upgrade_preserves_existing_endpoint_record(settings):
    async def run():
        await asyncio.to_thread(migrate,settings,'downgrade','0004')
        # Use the established endpoint service on the old schema, close all
        # connections, then apply 0005 only to this disposable database.
        async with open_persistence(settings) as rt:
            endpoints,o,a=service(rt);ep=await approved(endpoints,o,a)
            before=ep.model_dump(mode='json')
        await asyncio.to_thread(migrate,settings,'upgrade','head')
        async with open_persistence(settings) as rt:
            endpoints=service(rt)[0]
            assert (await endpoints.get(o,ep.api_id)).model_dump(mode='json')==before
            async with rt.database.transaction() as session:
                assert await session.scalar(text('SELECT version_num FROM app.alembic_version'))=='0008'
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())
