"""Real disposable PG, independent service instances, mocked identity/key sources."""
import asyncio
from uuid import uuid4
from dataclasses import replace
from datetime import timedelta
import pytest
from sqlalchemy import text,select
from tests.test_postgres_integration import settings,migrate
from tests.test_ingestion_integration import fixture
from tests.test_ingestion import sign
from persistence.runtime import open_persistence
from diagnostic_contracts.permits import PermitService,Permit
from diagnostic_contracts.ingestion import IngestionService
from diagnostic_contracts.service import DiagnosticDenied
from persistence.permits import PermitConsumption,AuthorizationPermit
from persistence.diagnostic_authority import AuthorityEventRow
from persistence.database import PersistenceError
pytestmark=pytest.mark.integration

def pure(snapshot,contract):return {'qualified_only':True,'truth_confirmed':False}

async def arrange(rt):
    ingestion,p,key,keys,o,a,au=await fixture(rt)
    await ingestion.ingest(o,sign(p,key))
    svc=PermitService(ingestion,evaluator=pure)
    second=PermitService(IngestionService(rt.database,au,keys=keys),evaluator=pure)
    args=dict(tenant_id=p.tenant_id,api_id=p.api_id,snapshot_digest=p.snapshot_digest)
    permit=await svc.issue(o,p.envelope_id,**args)
    return svc,second,permit,args,ingestion,p,keys,o,a,au


def test_single_use_recovery_context_and_audit(settings):
    async def run():
        async with open_persistence(settings) as rt:
            s,s2,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            assert await s2.evaluate(o,permit,**args)==pure(None,None)
            with pytest.raises(DiagnosticDenied):await s.evaluate(o,permit,**args)
            with pytest.raises(DiagnosticDenied):await s.evaluate(o,permit,**{**args,'tenant_id':uuid4()})
            with pytest.raises(DiagnosticDenied):await s.evaluate(o,permit,**{**args,'api_id':uuid4()})
            async with rt.database.transaction() as db:
                events=(await db.scalars(select(AuthorityEventRow).where(AuthorityEventRow.subject_ref==permit.permit_id))).all()
                assert {x.action for x in events}=={'permit_issued','permit_consumed','permit_denied'}
                assert await db.get(PermitConsumption,permit.permit_id)
            for table in ('diagnostic_authorization_permits','diagnostic_permit_consumptions','diagnostic_authorization_fences'):
                with pytest.raises(PersistenceError):
                    async with rt.database.transaction() as db:await db.execute(text('DELETE FROM app.'+table))
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())

@pytest.mark.parametrize('change',['fence','grant','membership','transfer','key_revoked','key_rotated','outage','expired','snapshot','superseded','endpoint_revision'])
def test_stale_authorization_never_enters_evaluator(settings,change):
    async def run():
        async with open_persistence(settings) as rt:
            svc,other,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            calls=[];other.evaluator=lambda *x:calls.append(1) or pure(None,None)
            if change=='fence':await svc.revoke(a,p.api_id,expected_generation=1)
            if change=='grant':await ing.revoke_producer(a,p.grant_id)
            if change=='membership':
                await au.set_membership(a,p.tenant_id,o.subject_id,[],expected_revision=1)
                await au.set_membership(a,p.tenant_id,o.subject_id,['owner'],expected_revision=2)
            if change=='transfer':await au.bind_endpoint(a,uuid4(),p.api_id,o.subject_id,expected_revision=1,endpoint_revision=p.endpoint_revision)
            if change=='key_revoked':keys.key=replace(keys.key,revoked=True)
            if change=='key_rotated':
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                keys.key=replace(keys.key,public_key=Ed25519PrivateKey.generate().public_key())
            if change=='outage':other.ingestion.keys=None
            if change=='expired':other.clock=lambda:ing.clock()+timedelta(seconds=10)
            if change=='snapshot':args['snapshot_digest']='b'*64
            if change=='superseded':
                from diagnostic_contracts.service import DiagnosticRegistry
                from tests.test_diagnostic_registry import payload
                from persistence.diagnostic_contracts import DiagnosticContractRow,DiagnosticVersionRow
                registry=DiagnosticRegistry(rt.database,tenants=au,admins=au.global_admins,ownership=au,security=au)
                async with rt.database.transaction() as db:
                    current=await db.get(DiagnosticContractRow,p.contract_id)
                    version=await db.get(DiagnosticVersionRow,(p.contract_id,1))
                    reviewer_id=version.reviewer_id
                # Reauthenticate the reviewer as a separate trusted identity.
                from tests.test_diagnostic_identity import configured,token
                # Existing auth session retains the original authenticated reviewer.
                from diagnostic_contracts.identity import TrustedPrincipal
                reviewer=next(TrustedPrincipal(reviewer_id,sid) for sid,c in au.identities._sessions.items() if c['sub']==str(reviewer_id))
                v=await registry.revise(o,p.tenant_id,p.contract_id,current.revision,p.endpoint_revision,payload())
                for who,target in ((o,'owner_verified'),(reviewer,'security_reviewed'),(a,'approved')):
                    v=await registry.transition(who,p.tenant_id,p.contract_id,2,target,v['revision'])
                await registry.transition(a,p.tenant_id,p.contract_id,1,'superseded',v['revision'],superseded_by=2)
            if change=='endpoint_revision':
                from persistence.tables import ApiRegistryRow
                async with rt.database.transaction() as db:
                    row=await db.get(ApiRegistryRow,p.api_id,with_for_update=True);row.revision+=1
            with pytest.raises(DiagnosticDenied):await other.evaluate(o,permit,**args)
            assert calls==[]
    asyncio.run(run())


def test_concurrent_use_and_committed_revocation(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,second,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            results=await asyncio.gather(svc.evaluate(o,permit,**args),second.evaluate(o,permit,**args),return_exceptions=True)
            assert sum(isinstance(x,dict) for x in results)==1
            assert sum(isinstance(x,DiagnosticDenied) for x in results)==1
            fresh=await svc.issue(o,p.envelope_id,**args)
            await second.revoke(a,p.api_id,expected_generation=1)
            with pytest.raises(DiagnosticDenied):await svc.evaluate(o,fresh,**args)
            async with rt.database.transaction() as db:
                assert await db.get(PermitConsumption,fresh.permit_id) is None
    asyncio.run(run())


@pytest.mark.parametrize('revoke_before_recovery',[False,True])
def test_crash_rolls_back_consumption_recovery_revalidates(settings,revoke_before_recovery):
    async def run():
        async with open_persistence(settings) as rt:
            svc,second,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            def crash(*_):raise RuntimeError('synthetic process failure')
            svc.evaluator=crash
            with pytest.raises(RuntimeError):await svc.evaluate(o,permit,**args)
            async with rt.database.transaction() as db:assert await db.get(PermitConsumption,permit.permit_id) is None
            # Simulated restart uses a separate service and repeats every check.
            if revoke_before_recovery:
                await second.revoke(a,p.api_id,expected_generation=1)
                with pytest.raises(DiagnosticDenied):await second.evaluate(o,permit,**args)
            else:
                assert await second.evaluate(o,permit,**args)==pure(None,None)
                with pytest.raises(DiagnosticDenied):await second.evaluate(o,permit,**args)
    asyncio.run(run())


def test_durable_key_revocation_bypasses_provider_cache(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,second,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            async with rt.database.transaction() as db:envelope=await db.get(__import__('persistence.ingestion',fromlist=['IngestionEnvelope']).IngestionEnvelope,p.envelope_id)
            await svc.revoke(a,p.api_id,expected_generation=1,key_id='test-key',key_fingerprint=envelope.key_fingerprint)
            # Mock key provider still reports valid: durable DB revocation wins.
            with pytest.raises(DiagnosticDenied):await second.evaluate(o,permit,**args)
            with pytest.raises(DiagnosticDenied):await second.issue(o,p.envelope_id,**args)
    asyncio.run(run())


def test_revocation_race_waits_for_pure_evaluation_transaction(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,second,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            entered=asyncio.Event();release=asyncio.Event()
            original=svc._context
            async def pause(db,actor,row):
                context=await original(db,actor,row)
                entered.set();await release.wait();return context
            svc._context=pause
            use=asyncio.create_task(svc.evaluate(o,permit,**args))
            await asyncio.wait_for(entered.wait(),2)
            revoke=asyncio.create_task(second.revoke(a,p.api_id,expected_generation=1))
            # A second DB session can inspect a still-uncommitted revocation.
            await asyncio.sleep(.05)
            assert not revoke.done()
            release.set()
            assert await use==pure(None,None)
            assert await revoke==2
            with pytest.raises(DiagnosticDenied):await second.evaluate(o,permit,**args)
    asyncio.run(run())


def test_database_connection_crash_recovery_with_separate_pool(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,_,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            started=asyncio.Event();resume=asyncio.Event();pid=[]
            original=svc._context
            async def pause(db,actor,row):
                context=await original(db,actor,row)
                pid.append(await db.scalar(text('SELECT pg_backend_pid()')))
                started.set();await resume.wait();return context
            svc._context=pause
            use=asyncio.create_task(svc.evaluate(o,permit,**args))
            await asyncio.wait_for(started.wait(),2)
            from persistence.database import Database
            independent=Database(settings)
            try:
                async with independent.transaction() as db:
                    assert await db.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid[0]})
                resume.set()
                with pytest.raises(PersistenceError):await use
                recovered=PermitService(IngestionService(independent,au,keys=keys),evaluator=pure)
                assert await recovered.evaluate(o,permit,**args)==pure(None,None)
                with pytest.raises(DiagnosticDenied):await recovered.evaluate(o,permit,**args)
            finally:await independent.close()
    asyncio.run(run())


def test_unavailable_evaluator_and_forged_capability(settings):
    async def run():
        from pydantic import SecretStr
        async with open_persistence(settings) as rt:
            svc,second,permit,args,ing,p,keys,o,a,au=await arrange(rt)
            fake=Permit(permit.permit_id,SecretStr('fabricated'))
            with pytest.raises(DiagnosticDenied):await second.evaluate(o,fake,**args)
            second.evaluator=None
            with pytest.raises(DiagnosticDenied):await second.evaluate(o,permit,**args)
            second.evaluator=lambda *_:{'diagnostic_checks':['Please paste the complete JWT'], 'password':'unsafe'}
            result=await second.evaluate(o,permit,**args)
            assert result['password']=='[REDACTED]'
            assert 'OUTPUT SAFETY' in result['diagnostic_checks'][0]
    asyncio.run(run())
