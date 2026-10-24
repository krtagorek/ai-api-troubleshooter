"""Configured-only HTTP boundary, synthetic IdP/proofs/keys and disposable PG."""
import asyncio,time,json
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
import httpx,pytest
from sqlalchemy import select,text
from tests.test_postgres_integration import settings,migrate
from tests.test_diagnostic_identity import configured,token
from tests.test_diagnostic_registry import payload as document
from tests.test_diagnostic_authority_integration import ProofProvider
from tests.test_ingestion import sample,sign,Keys
from backend.app import create_app
from backend.diagnostic_admin import Administration
from diagnostic_contracts.authority import production_registry
from diagnostic_contracts.ingestion import IngestionService,Payload,digest
from registry.authorization import AdminAuthorization
from registry.service import RegistryService
from persistence.runtime import open_persistence
from persistence.diagnostic_contracts import DiagnosticContractRow,DiagnosticVersionRow
from persistence.ingestion import IngestionGrant,IngestionEnvelope

@asynccontextmanager
async def environment(rt,*,rate=120):
    auth,key,identity_keys,revocations=configured();tokens=[token(key,exp=int(time.time())+3600) for _ in range(4)]
    actors=[await auth.authenticate(t) for t in tokens];o,a,r,x=actors
    admins=AdminAuthorization(frozenset({o.subject_id,a.subject_id}));proof=ProofProvider()
    registry,authority=production_registry(rt.database,identities=auth,global_admins=admins,evidence_provider=proof)
    keys=Keys(None);ingestion=IngestionService(rt.database,authority,keys=keys)
    admin=Administration(registry,ingestion,rate)
    factory=lambda db:RegistryService(db,admins=admins,environment='development',development_mock_origin='http://127.0.0.1:8001',resolver=lambda *_:{'127.0.0.1'})
    app=create_app(runtime=rt,authenticator=auth,admins=admins,registry_factory=factory,diagnostic_admin_factory=lambda db:admin)
    tenant=uuid4()
    for actor,role in ((o,'owner'),(a,'contract_admin'),(r,'security_reviewer')):await authority.set_membership(a,tenant,actor.subject_id,[role],expected_revision=0)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        headers=[{'Authorization':'Bearer '+t} for t in tokens]
        response=await client.post('/api/v1/registry/development-mock',headers=headers[0]);assert response.status_code==201
        ch=response.json();api=ch['registration']['api_id']
        assert (await client.post(f'/api/v1/registry/{api}/verify',headers=headers[0],json={'challenge':ch['verification_challenge']})).status_code==200
        ep=(await client.post(f'/api/v1/registry/{api}/approve',headers=headers[0])).json()
        base=f'/api/v1/diagnostic-admin/tenants/{tenant}'
        assert (await client.post(base+f'/endpoint-bindings/{api}',headers=headers[1],json={'endpoint_revision':ep['revision'],'expected_revision':0})).status_code==200
        yield dict(app=app,client=client,headers=headers,actors=actors,tenant=tenant,base=base,ep=ep,admin=admin,proof=proof,keys=keys,revocations=revocations,identity_keys=identity_keys,authority=authority,identity_signer=key)

async def register(e):
    r=await e['client'].post(e['base']+'/contracts',headers=e['headers'][0],json={'api_id':e['ep']['api_id'],'endpoint_revision':e['ep']['revision'],'document':document()});assert r.status_code==201,r.text
    return r.json()
def binding(v):return {k:v[k] for k in ('version','digest','endpoint_revision')}|{'expected_revision':v['revision']}
async def action(e,v,name,actor):return await e['client'].post(e['base']+'/contracts/'+v['contract_id']+'/'+name,headers=e['headers'][actor],json=binding(v))
async def active(e):
    v=await register(e)
    for name,actor in (('verify-owner',0),('security-review',2),('approve',1),('activate',1)):
        response=await action(e,v,name,actor);assert response.status_code==200,response.text;v=response.json()
    return v

async def signed(e,v):
    p,key,keys=sample();from dataclasses import replace
    p=Payload.model_validate({**p.model_dump(),'tenant_id':e['tenant'],'api_id':UUID(e['ep']['api_id']),'producer_id':e['actors'][0].subject_id,'contract_id':UUID(v['contract_id']),'contract_digest':v['digest'],'endpoint_revision':e['ep']['revision']})
    e['keys'].key=replace(keys.key,tenant_id=e['tenant'],api_id=UUID(e['ep']['api_id']),producer_id=p.producer_id)
    path=e['base']+'/contracts/'+v['contract_id']
    response=await e['client'].post(path+'/grants',headers=e['headers'][1],json={**binding(v),'payload':p.model_dump(mode='json')});assert response.status_code==201,response.text
    return p,key,sign(p,key),path

@pytest.mark.integration
def test_http_lifecycle_ingestion_audit_and_revocation(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await active(e);p,key,envelope,path=await signed(e,v)
            body={**binding(v),'envelope':envelope.model_dump(mode='json')}
            response=await e['client'].post(path+'/envelopes',headers=e['headers'][0],json=body);assert response.status_code==201,response.text
            assert (await e['client'].post(path+'/envelopes',headers=e['headers'][0],json=body)).status_code==403
            status=(await e['client'].get(path+'/envelopes/'+str(p.envelope_id),headers=e['headers'][0])).json();assert status['signature_valid'] and status['diagnostic_truth'] is False
            for endpoint in ('versions','grants','envelopes','audit','authority-audit'):
                response=await e['client'].get(path+'/'+endpoint+'?limit=1&offset=0',headers=e['headers'][0]);assert response.status_code==200 and len(response.json()['items'])==1
                assert 'signature"' not in response.text and 'capability' not in response.text
            assert (await e['client'].get(path+'/authorization',headers=e['headers'][0])).json()['allowed']
            assert (await action(e,v,'revoke',1)).status_code==200
            assert (await e['client'].get(path+'/authorization',headers=e['headers'][0])).json()['allowed'] is False
            assert (await e['client'].get(path+'/envelopes/'+str(p.envelope_id),headers=e['headers'][0])).json()['currently_verified'] is False
            events=(await e['client'].get(path+'/authority-audit?limit=100',headers=e['headers'][0])).json()['items']
            assert {'verification','ingestion_grant','ingestion_accepted'}<={item['action'] for item in events}
            assert all('details' not in item and 'key_fingerprint' not in item for item in events)
            assert len(await e['admin'].registry.history(e['actors'][0],e['tenant'],v['contract_id']))==6
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())

@pytest.mark.integration
def test_http_identity_tenant_owner_and_role_isolation(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            client=e['client'];v=await register(e);path=e['base']+'/contracts/'+v['contract_id']
            assert (await client.get(path)).status_code==401
            assert (await client.get(path,headers=e['headers'][3])).status_code==404
            wrong=path.replace(str(e['tenant']),str(uuid4()));assert (await client.get(wrong,headers=e['headers'][0])).status_code==404
            assert (await action(e,v,'approve',0)).status_code==403
            assert (await action(e,v,'verify-owner',2)).status_code==403
            assert (await client.post(path+'/approve',headers=e['headers'][0],json={**binding(v),'roles':['contract_admin']})).status_code==422
            await e['authority'].set_membership(e['actors'][1],e['tenant'],e['actors'][3].subject_id,['reader'],expected_revision=0)
            assert (await client.post(e['base']+'/contracts',headers=e['headers'][3],json={'api_id':e['ep']['api_id'],'endpoint_revision':e['ep']['revision'],'document':document()})).status_code==403
            e['revocations'].allowed=False;assert (await client.get(path,headers=e['headers'][0])).status_code==401
    asyncio.run(run())

@pytest.mark.integration
def test_http_digest_revision_races_and_supersession(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await register(e);path=e['base']+'/contracts/'+v['contract_id']
            bad={**binding(v),'digest':'a'*64}
            assert (await e['client'].post(path+'/verify-owner',headers=e['headers'][0],json=bad)).status_code==409
            calls=await asyncio.gather(action(e,v,'verify-owner',0),action(e,v,'verify-owner',0));assert sorted(r.status_code for r in calls)==[200,409]
            v=next(r.json() for r in calls if r.status_code==200)
            for name,actor in (('security-review',2),('approve',1),('activate',1)):v=(await action(e,v,name,actor)).json()
            newer=await e['client'].post(path+'/revisions',headers=e['headers'][0],json={**binding(v),'document':document()});assert newer.status_code==201
            n=newer.json()
            for name,actor in (('verify-owner',0),('security-review',2),('approve',1)):n=(await action(e,n,name,actor)).json()
            assert (await action(e,n,'activate',1)).status_code==409
            old={**binding(v),'expected_revision':n['revision'],'superseded_by':2}
            r=await e['client'].post(path+'/supersede',headers=e['headers'][1],json=old);assert r.status_code==200
            n['revision']=r.json()['revision'];assert (await action(e,n,'activate',1)).status_code==200
    asyncio.run(run())

@pytest.mark.integration
@pytest.mark.parametrize('failure',['key_outage','invalid_signature','grant_revoked','ownership_transfer','stale_revision'])
def test_http_ingestion_fail_closed(settings,failure):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await active(e);p,key,envelope,path=await signed(e,v)
            body={**binding(v),'envelope':envelope.model_dump(mode='json')}
            if failure=='key_outage':e['keys'].key=None
            if failure=='invalid_signature':body['envelope']['signature']='A'*86+'=='
            if failure=='grant_revoked':assert (await e['client'].post(path+'/grants/'+str(p.grant_id)+'/revoke',headers=e['headers'][1],json=binding(v))).status_code==200
            if failure=='ownership_transfer':await e['authority'].bind_endpoint(e['actors'][1],uuid4(),p.api_id,p.producer_id,expected_revision=1,endpoint_revision=p.endpoint_revision)
            if failure=='stale_revision':body['expected_revision']=1
            response=await e['client'].post(path+'/envelopes',headers=e['headers'][0],json=body);assert response.status_code in (403,409),response.text
            assert 'signature' not in response.text and 'password' not in response.text
            async with rt.database.transaction() as s:assert await s.scalar(select(IngestionEnvelope)) is None
    asyncio.run(run())

@pytest.mark.integration
def test_http_size_rate_missing_providers_and_proof_replay(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await register(e);path=e['base']+'/contracts/'+v['contract_id']
            e['proof'].reused=uuid4();v=(await action(e,v,'verify-owner',0)).json()
            response=await action(e,v,'security-review',2);assert response.status_code==403
            e['proof'].reused=None
            assert (await e['client'].get(path+'/audit?limit=101',headers=e['headers'][0])).status_code==422
            huge=await e['client'].post(path+'/revisions',headers=e['headers'][0],content=b'x'*262145);assert huge.status_code==413
            e['admin'].requests_per_minute=1
            assert (await e['client'].get(path,headers=e['headers'][0])).status_code==429
            e['app'].state.diagnostic_admin_factory=None
            assert (await e['client'].get(path,headers=e['headers'][0])).status_code==503
            assert (await e['client'].get(path)).status_code==401
    asyncio.run(run())

@pytest.mark.integration
def test_http_approval_revocation_race_and_shared_quota(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await register(e)
            for name,who in (('verify-owner',0),('security-review',2)):v=(await action(e,v,name,who)).json()
            responses=await asyncio.gather(action(e,v,'approve',1),action(e,v,'revoke',1));assert sorted(r.status_code for r in responses)==[200,409]
            history=await e['admin'].registry.history(e['actors'][0],e['tenant'],v['contract_id']);assert len(history)==4
            # Two service instances share PostgreSQL quota state, not local caches.
            from backend.diagnostic_admin import Administration
            other=Administration(e['admin'].registry,e['admin'].ingestion,requests_per_minute=1)
            e['admin'].requests_per_minute=1
            actor=e['actors'][3]
            outcomes=await asyncio.gather(e['admin'].quota(actor),other.quota(actor),return_exceptions=True)
            assert sum(getattr(o,'status_code',None)==429 for o in outcomes)==1
    asyncio.run(run())

def test_administration_defaults_unavailable_without_database_or_demo_bypass():
    async def run():
        from backend.auth import DemoAuthenticator
        value='synthetic-demo-credential-only-for-unit-test'
        app=create_app(authenticator=DemoAuthenticator(uuid4(),value))
        # No lifespan or database is opened: unconfigured routes fail before DB use.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            path='/api/v1/diagnostic-admin/tenants/'+str(uuid4())+'/contracts'
            assert (await client.get(path)).status_code==401
            assert (await client.get(path,headers={'Authorization':'Bearer '+value})).status_code==503
    asyncio.run(run())


@pytest.mark.integration
@pytest.mark.parametrize('failure',['expired','forged','wrong_audience','missing_keys'])
def test_http_rejects_invalid_identity(settings,failure):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            changes={'sub':str(e['actors'][0].subject_id)}
            signer=e['identity_signer']
            if failure=='expired':changes['exp']=0
            if failure=='forged':signer=configured()[1]
            if failure=='wrong_audience':changes['aud']='wrong-audience'
            if failure=='missing_keys':e['identity_keys'].key={}
            value=token(signer,**changes)
            response=await e['client'].get(e['base']+'/contracts',headers={'Authorization':'Bearer '+value})
            assert response.status_code==401 and value not in response.text
    asyncio.run(run())


@pytest.mark.integration
def test_http_lossless_signed_json_text_preserves_numeric_scalars(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as e:
            v=await active(e);p,key,envelope,path=await signed(e,v)
            snapshot={'incident_id':'synthetic-precision-fixture','logs':[{'ordinal':9007199254740993,'ratio':1.0}]}
            p=Payload.model_validate({**p.model_dump(),'snapshot':snapshot,'snapshot_digest':digest(snapshot)})
            wire=json.dumps(sign(p,key).model_dump(mode='json'))
            response=await e['client'].post(path+'/envelopes',headers=e['headers'][0],json={**binding(v),'envelope':wire})
            assert response.status_code==201,response.text
            result=await e['client'].get(path+'/envelopes/'+str(p.envelope_id),headers=e['headers'][0]);assert result.json()['signature_valid'] is True
    asyncio.run(run())


@pytest.mark.integration
def test_control_audit_does_not_join_cross_tenant_uuid_kinds(settings):
    async def run():
        async with open_persistence(settings) as rt,environment(rt) as first,environment(rt) as second:
            foreign=await active(second);p,key,envelope,foreign_path=await signed(second,foreign)
            assert (await second['client'].post(foreign_path+'/envelopes',headers=second['headers'][0],json={**binding(foreign),'envelope':envelope.model_dump(mode='json')})).status_code==201
            own=await active(first);own_p,own_key,own_envelope,own_path=await signed(first,own)
            own_p=own_p.model_copy(update={'grant_id':p.envelope_id})
            assert (await first['client'].post(own_path+'/grants',headers=first['headers'][1],json={**binding(own),'payload':own_p.model_dump(mode='json')})).status_code==201
            response=await first['client'].get(own_path+'/authority-audit?limit=100',headers=first['headers'][0]);assert response.status_code==200
            events=response.json()['items']
            assert not any(e['action']=='ingestion_accepted' for e in events)
            assert any(e['action']=='ingestion_grant' and e['subject_ref']==str(p.envelope_id) for e in events)
    asyncio.run(run())
