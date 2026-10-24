"""Manual isolated live OS-process crash acceptance. No production application hooks."""
import asyncio,json,os,sys,time,signal,hashlib,socket
from pathlib import Path
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from contextlib import suppress
from io import StringIO
from dotenv import dotenv_values
from sqlalchemy import select,text,func
from sqlalchemy.engine import make_url
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from tests.test_diagnostic_identity import configured,token
from tests.test_diagnostic_authority_integration import ProofProvider
from tests.test_diagnostic_registry import payload as contract_document
from tests.test_ingestion import sign
from tests.test_postgres_integration import settings as settings_fixture,migrate
from tests.phase14_6_2a_process import emit,auth_from
from registry.authorization import AdminAuthorization
from diagnostic_contracts.authority import production_registry
from diagnostic_contracts.ingestion import IngestionService,Payload,ProducerKey,digest
from persistence.runtime import open_persistence
from persistence.tables import JobRow,ReportRow,InvestigationRow,ProgressEventRow
from persistence.permits import AuthorizationPermit,PermitConsumption
from persistence.diagnostic_authority import AuthorityEventRow
from persistence.diagnostic_contracts import DiagnosticEventRow
from worker.queue import Claim,JobQueue,LeaseLost
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/phase14_6_2a-acceptance'/os.getenv('PHASE14_6_2A_ATTEMPT','run-1')
NODE=__import__('tests.runtime_tools',fromlist=['node_executable']).node_executable()
SCENARIOS=(('claim','claimed',None),('investigator','investigator_checkpoint',None),('semantic','semantic_committed',None),('publication','published',None),('contract_revocation','investigator_checkpoint','contract'),('endpoint_revocation','resolution_checkpoint','endpoint'),('producer_revocation','classifier_checkpoint','producer'))

async def wait_file(path,timeout=30):
    end=asyncio.get_running_loop().time()+timeout
    while asyncio.get_running_loop().time()<end:
        if path.exists():
            try:return json.loads(path.read_text())
            except json.JSONDecodeError:pass
        await asyncio.sleep(.05)
    raise TimeoutError('Controlled crash point not reached')

async def run(settings):
    result={'controller_pid':os.getpid(),'harness_source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),ROOT/'tests/phase14_6_2a_process.py',ROOT/'tests/phase14_6_2a_browser.mjs',ROOT/'tests/phase14_6_2a_preview.mjs')},'mode':'independent_OS_processes_real_HTTP_Chromium_deterministic_LLM','scenarios':{},'security_stop':None,'paid_calls':0,'primary_migration':False,'processes':[]}
    processes=[];secrets=[]
    auth,rsa,keys,_=configured();tokens=[token(rsa,exp=int(time.time())+3600) for _ in range(4)];secrets+=tokens
    owner,admin,reviewer,stranger=[await auth.authenticate(t) for t in tokens]
    admins=AdminAuthorization(frozenset({owner.subject_id,admin.subject_id}))
    cleanenv={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG') if k in os.environ};cleanenv['DIAGNOSTIC_CONTRACTS_ENABLED']='false'
    base={'database_url':settings.database_url.get_secret_value(),'checkpoint_database_url':settings.checkpoint_database_url.get_secret_value(),'jwks':keys.key,'admins':[str(owner.subject_id),str(admin.subject_id)]}
    secrets.extend([base['database_url'],base['checkpoint_database_url']])
    async def spawn(config,module='tests.phase14_6_2a_process'):
        folder=Path(config['out']);log=(folder/(config['role']+'-process-'+uuid4().hex[:8]+'.log')).open('w')
        p=await asyncio.create_subprocess_exec(sys.executable,'-m',module,cwd=ROOT,env=cleanenv,stdin=asyncio.subprocess.PIPE,stdout=log,stderr=log)
        processes.append(p);p.stdin.write(json.dumps(config).encode());await p.stdin.drain();p.stdin.close()
        result['processes'].append({'role':config['role'],'pid':p.pid,'out':str(folder.relative_to(ROOT))})
        return p
    async def browser(config):
        p=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_2a_browser.mjs'),cwd=ROOT,env=cleanenv,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        processes.append(p);p.stdin.write(json.dumps(config).encode());await p.stdin.drain();p.stdin.close();return p
    try:
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            backend=await spawn({**base,'role':'backend','out':str(OUT)})
            mock=await spawn({**base,'role':'mock','out':str(OUT)})
            log=(OUT/'frontend.log').open('w')
            frontend=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_2a_preview.mjs'),cwd=ROOT/'frontend',env=cleanenv,stdout=log,stderr=log);processes.append(frontend)
            result['processes'].append({'role':'frontend','pid':frontend.pid,'normal_flag':'false','configuration':'real compiled React preview/proxy; no HMR client; clean environment; no private .env loaded','configuration_fingerprint':hashlib.sha256((ROOT/'tests/phase14_6_2a_preview.mjs').read_bytes()).hexdigest()})
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',trust_env=False,timeout=25) as client:
                for url in ('/api/v1/health/ready','http://127.0.0.1:8001/health','http://127.0.0.1:5173'):
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        try:
                            if (await client.get(url,timeout=.5)).status_code==200:break
                        except httpx.HTTPError:pass
                        await asyncio.sleep(.1)
                    else:raise TimeoutError('Isolated service startup failed')
                headers={'Authorization':'Bearer '+tokens[0]};other={'Authorization':'Bearer '+tokens[3]}
                svc,authority=production_registry(rt.database,identities=auth,global_admins=admins,evidence_provider=ProofProvider())
                for name,point,revoke in SCENARIOS:
                    folder=OUT/name;folder.mkdir();entry={'status':'RUNNING','point':point,'revocation':revoke};result['scenarios'][name]=entry
                    async def request(method,path,**kwargs):
                        response=await client.request(method,path,**kwargs)
                        entry.setdefault('http',[]).append({'method':method,'path':path,'status':response.status_code})
                        if any(s in response.text for s in secrets):raise AssertionError('SECURITY_STOP response secret')
                        return response
                    registered=(await request('POST','/api/v1/registry/development-mock',headers=headers)).json();api=registered['registration']['api_id']
                    verified=await request('POST',f'/api/v1/registry/{api}/verify',headers=headers,json={'challenge':registered['verification_challenge']});assert verified.status_code==200
                    ep=(await request('POST',f'/api/v1/registry/{api}/approve',headers=headers)).json();assert ep['status']=='approved'
                    tenant=uuid4()
                    for actor,role in ((owner,'owner'),(admin,'contract_admin'),(reviewer,'security_reviewer')):await authority.set_membership(admin,tenant,actor.subject_id,[role],expected_revision=0)
                    await authority.bind_endpoint(admin,tenant,UUID(api),owner.subject_id,expected_revision=0,endpoint_revision=ep['revision'])
                    v=await svc.register(owner,tenant,UUID(api),ep['revision'],contract_document())
                    for actor,status in ((owner,'owner_verified'),(reviewer,'security_reviewed'),(admin,'approved'),(admin,'active')):v=await svc.transition(actor,tenant,v['contract_id'],1,status,v['revision'])
                    captured=datetime.now(timezone.utc);correlation='crash-'+name
                    fields=dict(routed_epoch=41,current_epoch=43,routed_term=2,current_term=2,domain='demo-domain',resource='resource-1',correlation=correlation,producer='owner-coordinator',observed_at=captured.isoformat(),rejection='stale-fencing-epoch',reachability=True)
                    body=dict(service='Process recovery '+name,endpoint='/health',http_method='GET',status_code=503,actual_behavior='Dependency rejected an obsolete fencing epoch.',expected_behavior='Use authoritative fencing metadata.',logs=[{'message':'Fencing rejection','correlation_id':correlation,'fields':fields}])
                    response=await request('POST','/api/v1/incidents',headers=headers,json=body);assert response.status_code==201
                    created=response.json();iid=created['incident']['incident_id'] if 'incident' in created else created['incident_id']
                    detail=(await request('GET',f'/api/v1/incidents/{iid}',headers=headers)).json();inv=detail['investigations'][0];snapshot=detail['incident']
                    signing=Ed25519PrivateKey.generate();public=signing.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
                    key=ProducerKey(signing.public_key(),owner.subject_id,tenant,UUID(api),captured-timedelta(seconds=5),captured+timedelta(minutes=2),captured+timedelta(seconds=10))
                    class Keys:
                        async def resolve(self,kid):
                            from dataclasses import replace
                            return replace(key,checked_until=datetime.now(timezone.utc)+timedelta(seconds=10)) if kid=='test-key' else None
                    ingestion=IngestionService(rt.database,authority,keys=Keys())
                    p=Payload(version='signed-observations-1',envelope_id=uuid4(),grant_id=uuid4(),tenant_id=tenant,api_id=UUID(api),producer_id=owner.subject_id,contract_id=UUID(v['contract_id']),contract_version=1,contract_digest=v['digest'],endpoint_revision=ep['revision'],binding_revision=1,source='service_logs',resource='resource-1',correlation_id=correlation,captured_at=captured,snapshot=snapshot,snapshot_digest=digest(snapshot))
                    await ingestion.authorize_producer(admin,p);await ingestion.ingest(owner,sign(p,signing))
                    entry['signature_verification']=await ingestion.verify_provenance(owner,p.envelope_id,tenant_id=tenant,api_id=UUID(api),contract_id=p.contract_id,contract_version=1,snapshot_digest=p.snapshot_digest)
                    cfg={**base,'role':'worker','out':str(folder),'actor_token':tokens[0],'producer_id':str(owner.subject_id),'tenant_id':str(tenant),'api_id':api,'envelope_id':str(p.envelope_id),'public_signing_key':public,'key_from':key.not_before.isoformat(),'key_until':key.expires_at.isoformat(),'thread_id':inv['thread_id'],'snapshot_digest':p.snapshot_digest,'crash_point':point}
                    b=await browser({'out':str(folder),'token':tokens[0],'incident_id':iid,'hold':bool(revoke),'terminal':point=='published'})
                    await wait_file(folder/'browser-submitted.json')
                    w1=await spawn(cfg)
                    try:bar=await wait_file(folder/'barrier.json',20)
                    except TimeoutError:
                        entry['status']='BLOCKED';entry['reason']='Deterministic crash point not reached';raise
                    assert bar['pid']==w1.pid
                    async with rt.database.transaction() as s:
                        job=await s.scalar(select(JobRow).where(JobRow.investigation_id==UUID(inv['investigation_id'])))
                        oldclaim=Claim(job.job_id,job.investigation_id,job.lease_id) if job.lease_id else None
                        entry['before_kill']={'job_id':str(job.job_id),'status':job.status,'attempts':job.attempts,'lease_id':str(job.lease_id) if job.lease_id else None,'lease_expires_at':str(job.lease_expires_at),'reports':await s.scalar(select(func.count()).select_from(ReportRow).where(ReportRow.investigation_id==job.investigation_id))}
                    os.kill(w1.pid,signal.SIGKILL);await asyncio.wait_for(w1.wait(),5)
                    entry['termination']={'pid':w1.pid,'signal':'SIGKILL','returncode':w1.returncode,'at':datetime.now(timezone.utc).isoformat()};assert w1.returncode==-signal.SIGKILL
                    emit(folder,'crashed.json',{'pid':w1.pid});await wait_file(folder/'browser-offline.json')
                    if revoke=='contract':await svc.transition(admin,tenant,p.contract_id,1,'revoked',v['revision'])
                    if revoke=='endpoint':assert (await request('POST',f'/api/v1/registry/{api}/revoke',headers=headers)).status_code==200
                    if revoke=='producer':await ingestion.revoke_producer(admin,p.grant_id)
                    if revoke:entry['revocation_committed_at']=datetime.now(timezone.utc).isoformat()
                    # Restart only our Vite process to force a real proxy socket
                    # disconnect and authenticated browser Last-Event-ID replay.
                    frontend.terminate();await asyncio.wait_for(frontend.wait(),5)
                    proxylog=(folder/'proxy-restart.log').open('w')
                    frontend=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_2a_preview.mjs'),cwd=ROOT/'frontend',env=cleanenv,stdout=proxylog,stderr=proxylog);processes.append(frontend)
                    emit(folder,'proxy-restarted.json',{'pid':frontend.pid})
                    await wait_file(folder/'browser-reconnected.json',20)
                    # No lease updates: the new process waits for PostgreSQL expiry.
                    w2=await spawn({**cfg,'crash_point':None,'terminal_expected':point=='published'})
                    await asyncio.wait_for(w2.wait(),25);assert w2.returncode==0
                    entry['replacement_pid']=w2.pid
                    entry['claim_history']=[json.loads(x.read_text()) for x in folder.glob('claim-*.json')]
                    if point!='published':
                        assert len(entry['claim_history'])==2 and len({x['lease_id'] for x in entry['claim_history']})==2
                    else:assert len(entry['claim_history'])==1
                    if oldclaim:
                        try:await JobQueue(rt.database).heartbeat(oldclaim)
                        except LeaseLost:entry['old_lease_fenced']=True
                        else:raise AssertionError('SECURITY_STOP old lease accepted')
                    async with rt.database.transaction() as s:
                        recovered_events=list(await s.scalars(select(ProgressEventRow).where(ProgressEventRow.job_id==UUID(entry['before_kill']['job_id'])).order_by(ProgressEventRow.sequence)))
                    emit(folder,'recovered.json',{'pid':w2.pid,'wrapper_nodes':list({e.payload['node'] for e in recovered_events if e.event_type=='node_completed' and 'node' in e.payload}),'attempts':sorted({e.payload['attempt'] for e in recovered_events if e.payload['attempt']>0})})
                    await asyncio.wait_for(b.wait(),30);browser_result=json.loads((folder/'browser-evidence.json').read_text());entry['browser']=browser_result
                    if b.returncode!=0:raise AssertionError('Browser acceptance failed outside terminal reconciliation')
                    assert browser_result['result']=='PASS'
                    if browser_result.get('consoleSecretLeak') or browser_result.get('responseSecretLeak'):raise AssertionError('SECURITY_STOP browser credentials')
                    async with rt.database.transaction() as s:
                        job=await s.scalar(select(JobRow).where(JobRow.investigation_id==UUID(inv['investigation_id'])))
                        investigation=await s.get(InvestigationRow,job.investigation_id)
                        events=list(await s.scalars(select(ProgressEventRow).where(ProgressEventRow.job_id==job.job_id).order_by(ProgressEventRow.sequence)))
                        reports=list(await s.scalars(select(ReportRow).where(ReportRow.investigation_id==job.investigation_id)))
                        permits=list(await s.scalars(select(AuthorizationPermit).where(AuthorizationPermit.envelope_id==p.envelope_id)))
                        consumed=list(await s.scalars(select(PermitConsumption).where(PermitConsumption.permit_id.in_([q.permit_id for q in permits]))))
                        checkpoints=await s.scalar(text('SELECT count(*) FROM langgraph.checkpoints WHERE thread_id=:thread'),{'thread':inv['thread_id']})
                        entry['after_recovery']={'job_status':job.status,'investigation_status':investigation.status,'attempts':job.attempts,'lease_id':str(job.lease_id) if job.lease_id else None,'reports':len(reports),'checkpoints':checkpoints,'events':[{'sequence':e.sequence,'type':e.event_type,'payload':e.payload,'at':e.created_at.isoformat()} for e in events],'permit_records':[{'id':str(q.permit_id),'operation':q.context['operation'],'issued_at':q.issued_at.isoformat()} for q in permits],'consumption_count':len(consumed),'state':investigation.diagnostic_state}
                        assert [e.sequence for e in events]==list(range(1,len(events)+1))
                        terminals=[e for e in events if e.event_type in ('succeeded','awaiting_evidence','failed','cancelled','interrupted')];assert len(terminals)==1
                        assert job.attempts==(1 if point=='published' else 2)
                        if oldclaim:
                            reclaimed=next(e for e in events if e.event_type=='claimed' and e.payload['attempt']==2)
                            assert reclaimed.created_at>=datetime.fromisoformat(entry['before_kill']['lease_expires_at'])
                        assert len(reports)==(0 if revoke else 1)
                        assert job.status==('awaiting_evidence' if revoke else 'succeeded')
                        if not revoke:assert reports[0].payload['validation_results'][-1]['status']=='NOT_TESTED' and reports[0].payload['proposed_fix']['requires_human_approval']
                        if revoke:assert investigation.diagnostic_state['final_report'] is None
                        # Exact synthetic credentials must never reach any checkpoint blob.
                        blobs=(await s.execute(text('SELECT blob FROM langgraph.checkpoint_blobs WHERE thread_id=:thread AND blob IS NOT NULL'),{'thread':inv['thread_id']})).scalars().all()
                        assert not any(secret.encode() in bytes(blob) for blob in blobs for secret in secrets)
                        entry['checkpoint_blob_sha256']=[hashlib.sha256(bytes(blob)).hexdigest() for blob in blobs]
                    report_path=f'/api/v1/incidents/{iid}/report'
                    assert (await request('GET',report_path,headers=headers)).status_code==(409 if revoke else 200)
                    assert (await request('GET',report_path,headers=other)).status_code==404
                    assert (await request('GET',report_path)).status_code==401
                    replay=await request('GET',f'/api/v1/investigations/{inv["investigation_id"]}/events',headers={**headers,'Last-Event-ID':str(job.job_id)+':1'})
                    ids=[l[4:].strip() for l in replay.text.splitlines() if l.startswith('id:')];assert len(ids)==len(events)-1 and len(ids)==len(set(ids));entry['replay_ids']=ids
                    entry['agent_calls']=[json.loads(line) for path in folder.glob('agent-*.jsonl') for line in path.read_text().splitlines()]
                    if revoke=='contract':assert not any(c['node']=='root_cause_analyst' and c['pid']==w2.pid for c in entry['agent_calls'])
                    if revoke=='endpoint':assert not any(c['node']=='validator' and c['pid']==w2.pid for c in entry['agent_calls'])
                    if revoke=='producer':assert not any(c['node']=='root_cause_analyst' for c in entry['agent_calls'])
                    entry['worker_result']='PASS'
                    entry['status']='PASS' if browser_result['result']=='PASS' else 'FAIL'
                    emit(OUT,'backend-evidence.json',result)
                await asyncio.to_thread(migrate,settings,'check')
                async with rt.database.transaction() as s:
                    result['migration_revision']=await s.scalar(text('SELECT version_num FROM app.alembic_version'))
                    result['contract_audit']=[{'sequence':r.sequence,'action':r.action,'hash':r.event_hash,'previous':r.previous_hash} for r in await s.scalars(select(DiagnosticEventRow))]
                    result['authority_audit']=[{'action':r.action,'details':r.details} for r in await s.scalars(select(AuthorityEventRow))]
                result['schema_check']='PASS'
    except BaseException as exc:
        result['failure_type']=type(exc).__name__
        for entry in result['scenarios'].values():
            if entry['status']=='RUNNING':entry['status']='FAIL'
        if 'SECURITY_STOP' in str(exc):result['security_stop']='Acceptance security invariant failed'
        raise
    finally:
        for p in processes:
            if p.returncode is None:p.terminate()
        for p in processes:
            with suppress(Exception):await asyncio.wait_for(p.wait(),5)
            if p.returncode is None:p.kill();await p.wait()
        result['cleanup']={'all_child_processes_exited':all(p.returncode is not None for p in processes)}
        serialized=json.dumps(result,default=str)
        assert not any(s in serialized for s in secrets)
        emit(OUT,'backend-evidence.json',result)
    return result


def primary_readonly_snapshot(raw):
    import psycopg
    from psycopg import sql
    url=make_url(raw).set(database='api_troubleshooter')
    with psycopg.connect(url.render_as_string(hide_password=False),connect_timeout=5) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        target=connection.execute('SELECT current_database(), inet_server_port()').fetchone()
        tables=connection.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('app','langgraph') ORDER BY 1,2").fetchall()
        counts={schema+'.'+table:connection.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier(schema),sql.Identifier(table))).fetchone()[0] for schema,table in tables}
        columns=connection.execute("SELECT table_schema,table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema IN ('app','langgraph') ORDER BY 1,2,ordinal_position").fetchall()
        return {'database':target[0],'server_port':target[1],'read_only':True,'table_counts':counts,'schema_sha256':hashlib.sha256(json.dumps(columns,default=str).encode()).hexdigest()}


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    raw=os.getenv('TEST_DATABASE_URL')
    if not raw:
        line=next(l for l in (ROOT/'.env').read_text().splitlines() if l.startswith('TEST_DATABASE_URL='));raw=dotenv_values(stream=StringIO(line))['TEST_DATABASE_URL']
    url=make_url(raw);assert url.host in ('localhost','127.0.0.1') and url.port==55432 and url.database.endswith('_test')
    for port in (8000,8001,5173):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0:raise RuntimeError('Isolated ports occupied; no existing process terminated')
    before=hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest()
    primary_before=primary_readonly_snapshot(raw)
    emit(OUT,'primary-before.json',primary_before)
    for k in list(os.environ):
        if k not in ('PATH','HOME','TMPDIR','LANG','LC_ALL'):del os.environ[k]
    os.environ['TEST_DATABASE_URL']=raw
    fixture=settings_fixture.__wrapped__();settings=next(fixture)
    try:asyncio.run(run(settings))
    finally:
        with suppress(StopIteration):next(fixture)
        primary_after=primary_readonly_snapshot(raw);emit(OUT,'primary-after.json',primary_after)
        cleanup={'primary_readonly_fingerprint_unchanged':primary_before==primary_after,'disposable_database_removed':True,'primary_database_migrated':False,'normal_env_unchanged':hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest()==before,'normal_env_sha256':before}
        emit(OUT,'cleanup.json',cleanup)
    print('Isolated process acceptance complete; inspect sanitized evidence')

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print('Acceptance stopped: '+type(exc).__name__+'; inspect sanitized evidence. No credentials printed.');sys.exit(1)
