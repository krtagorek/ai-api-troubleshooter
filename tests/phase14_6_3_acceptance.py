"""Real HTTP/browser admin acceptance; synthetic providers; disposable PG only."""
import asyncio,json,os,sys,socket,time
from pathlib import Path
from uuid import UUID,uuid4
from datetime import datetime,timezone,timedelta
from contextlib import suppress
from io import StringIO
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy import select,func,text
import uvicorn,httpx
from tests.test_diagnostic_admin import environment,binding
from tests.test_diagnostic_registry import payload as document
from tests.test_ingestion import sample,sign
from tests.test_postgres_integration import settings as fixture,migrate
from tests.phase14_6_2a_acceptance import primary_readonly_snapshot,wait_file
from tests.phase14_6_2a_process import emit,restrict
from persistence.runtime import open_persistence
from persistence.ingestion import IngestionEnvelope,IngestionGrant
from persistence.diagnostic_contracts import DiagnosticContractRow,DiagnosticVersionRow,DiagnosticEventRow
from persistence.diagnostic_authority import AuthorityEventRow
from diagnostic_contracts.ingestion import Payload,digest
from diagnostic_contracts.integration import SemanticIntegration,Binding
from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
from models.incident import Incident
from graph.state import initialize_state
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase14_6_3-acceptance'/os.getenv('PHASE14_6_3_ATTEMPT','run-1')
NODE=__import__('tests.runtime_tools',fromlist=['node_executable']).node_executable()
async def run(settings):
    result={'mode':'real_HTTP_Chromium_synthetic_providers','checks':{},'http':[],'paid_calls':0,'security_stop':None};servers=[];tasks=[];processes=[]
    async with open_persistence(settings,setup_checkpoints=True) as rt,environment(rt) as e:
        # Fixture bootstraps one unrelated endpoint. The actual accepted endpoint
        # is separately registered and bound through Chromium and real HTTP.
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG') if k in os.environ};env['DIAGNOSTIC_CONTRACTS_ENABLED']='false'
        tokens=[h['Authorization'][7:] for h in e['headers']]
        try:
            from mock_api.server import app as mock
            for app,port in ((e['app'],8000),(mock,8001)):
                s=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='critical',log_config=None));servers.append(s);tasks.append(asyncio.create_task(s.serve()))
                for _ in range(100):
                    if s.started:break
                    await asyncio.sleep(.05)
                assert s.started
            log=(OUT/'frontend.log').open('w');frontend=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_2a_preview.mjs'),cwd=ROOT,env=env,stdout=log,stderr=log);processes.append(frontend)
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',trust_env=False,timeout=20) as client:
                for _ in range(100):
                    try:
                        if (await client.get('http://127.0.0.1:5173')).status_code==200:break
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.05)
                browser=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_3_browser.mjs'),cwd=ROOT,env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL);processes.append(browser)
                browser.stdin.write(json.dumps({'tokens':tokens,'tenant':str(e['tenant']),'out':str(OUT),'document':document()}).encode());await browser.stdin.drain();browser.stdin.close()
                active=await wait_file(OUT/'active.json',60);v=active['contract'];ep=active['endpoint']
                from dataclasses import replace
                captured=datetime.now(timezone.utc);p,key,keys=sample()
                fields=dict(routed_epoch=41,current_epoch=43,routed_term=2,current_term=2,domain='demo-domain',resource='test',correlation='admin-evidence',producer='owner-coordinator',observed_at=captured.isoformat(),rejection='stale-fencing-epoch',reachability=True)
                incident=Incident(incident_id=str(uuid4()),service='Signed administration fixture',endpoint='/health',http_method='GET',status_code=503,actual_behavior='Fencing rejection',expected_behavior='Use authoritative epoch',logs=[{'message':'Fencing rejection','correlation_id':'admin-evidence','fields':fields}])
                snapshot=incident.model_dump(mode='json')
                p=Payload.model_validate({**p.model_dump(),'tenant_id':e['tenant'],'api_id':UUID(ep['api_id']),'producer_id':e['actors'][0].subject_id,'contract_id':UUID(v['contract_id']),'contract_digest':v['digest'],'endpoint_revision':ep['revision'],'captured_at':captured,'correlation_id':'admin-evidence','snapshot':snapshot,'snapshot_digest':digest(snapshot)})
                e['keys'].key=replace(keys.key,tenant_id=e['tenant'],api_id=p.api_id,producer_id=p.producer_id,not_before=captured-timedelta(seconds=5),expires_at=captured+timedelta(minutes=2),checked_until=captured+timedelta(seconds=20))
                original=e['keys']
                # Synthetic key service refreshes its revocation freshness; it
                # never returns signing secrets or consults client-controlled URLs.
                class CurrentKeys:
                    async def resolve(self,kid):return replace(original.key,checked_until=datetime.now(timezone.utc)+timedelta(seconds=10)) if kid=='test-key' else None
                e['admin'].ingestion.keys=CurrentKeys()
                emit(OUT,'public-signed-scope.json',{'payload':p.model_dump(mode='json'),'envelope':sign(p,key).model_dump(mode='json')})
                await wait_file(OUT/'ingested.json',60)
                path=f'/api/v1/diagnostic-admin/tenants/{e["tenant"]}/contracts/{p.contract_id}'
                async def request(method,url,**kwargs):
                    response=await client.request(method,url,**kwargs);result['http'].append({'method':method,'path':url,'status':response.status_code})
                    assert not any(t in response.text for t in tokens) and 'PRIVATE KEY' not in response.text
                    return response
                assert (await request('GET',path)).status_code==401
                assert (await request('GET',path,headers=e['headers'][3])).status_code==404
                assert (await request('POST',path+'/approve',headers=e['headers'][0],json=binding(v))).status_code==403
                assert (await request('GET',path.replace(str(e['tenant']),str(uuid4())),headers=e['headers'][0])).status_code==404
                result['checks']['real_http_authentication_and_isolation']='PASS'
                await wait_file(OUT/'revoked.json',60)
                async def resolver(thread,snapshot_digest):return Binding(e['actors'][0],e['tenant'],p.api_id,p.envelope_id)
                integration=SemanticIntegration(e['admin'].ingestion,binding_resolver=resolver,producer_labels={(p.producer_id,'service_logs'):'owner-coordinator'})
                try:await integration.evaluate(initialize_state(snapshot,str(uuid4())))
                except (DiagnosticDenied,DiagnosticConflict):result['checks']['fresh_downstream_authorization_denied']='PASS'
                else:result['security_stop']='Revocation bypass';raise AssertionError('Security stop')
                await asyncio.wait_for(browser.wait(),30);assert browser.returncode==0
                result['browser']=json.loads((OUT/'browser-evidence.json').read_text());assert result['browser']['result']=='PASS' and not result['browser'].get('securityStop')
                async with rt.database.transaction() as s:
                    result['database']={'revision':await s.scalar(text('SELECT version_num FROM app.alembic_version')),'contracts':await s.scalar(select(func.count()).select_from(DiagnosticContractRow)),'versions':await s.scalar(select(func.count()).select_from(DiagnosticVersionRow)),'envelopes':await s.scalar(select(func.count()).select_from(IngestionEnvelope)),'grants':await s.scalar(select(func.count()).select_from(IngestionGrant)),'contract_events':await s.scalar(select(func.count()).select_from(DiagnosticEventRow)),'authority_events':await s.scalar(select(func.count()).select_from(AuthorityEventRow))}
                result['contract_audit']=await e['admin'].registry.history(e['actors'][0],e['tenant'],p.contract_id)
                await asyncio.to_thread(migrate,settings,'check');result['checks']['schema_consistency']='PASS'
        finally:
            for proc in processes:
                if proc.returncode is None:proc.terminate()
            for proc in processes:
                with suppress(Exception):await asyncio.wait_for(proc.wait(),5)
                if proc.returncode is None:proc.kill();await proc.wait()
            for server in servers:server.should_exit=True
            await asyncio.gather(*tasks,return_exceptions=True)
            result['process_cleanup']=all(p.returncode is not None for p in processes)
            emit(OUT,'backend-evidence.json',result)
    return result

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    raw=os.getenv('TEST_DATABASE_URL')
    if not raw:
        line=next(l for l in (ROOT/'.env').read_text().splitlines() if l.startswith('TEST_DATABASE_URL='));raw=dotenv_values(stream=StringIO(line))['TEST_DATABASE_URL']
    u=make_url(raw);assert u.host in ('localhost','127.0.0.1') and u.port==55432 and u.database.endswith('_test')
    for port in (8000,8001,5173):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port))==0:raise RuntimeError('Acceptance ports occupied')
    before=primary_readonly_snapshot(raw)
    from evaluation.quality_pilot import sha
    private_hash=sha('.env')
    for key in list(os.environ):
        if key not in ('PATH','HOME','TMPDIR','LANG'):del os.environ[key]
    os.environ['TEST_DATABASE_URL']=raw;os.environ['DIAGNOSTIC_CONTRACTS_ENABLED']='false';restrict()
    gen=fixture.__wrapped__();settings=next(gen)
    try:asyncio.run(run(settings))
    finally:
        with suppress(StopIteration):next(gen)
        emit(OUT,'cleanup.json',{'disposable_database_removed':True,'primary_readonly_fingerprint_unchanged':primary_readonly_snapshot(raw)==before,'normal_env_unchanged':sha('.env')==private_hash,'normal_flag':'OFF'})
    print('Real HTTP/browser diagnostic administration acceptance completed')
if __name__=='__main__':
    try:main()
    except BaseException as exc:print('Acceptance stopped: '+type(exc).__name__+'; sanitized artifacts retained, credentials withheld');sys.exit(1)
