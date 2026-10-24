"""Manual live acceptance. Disposable PG only; synthetic providers; no app fixes."""
import asyncio,json,os,sys,time,socket,hashlib
from pathlib import Path
from contextlib import suppress
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from io import StringIO
from dotenv import dotenv_values
import httpx,uvicorn
from sqlalchemy import select,text,func
from sqlalchemy.engine import make_url
from backend.app import create_app
from registry.authorization import AdminAuthorization
from registry.service import RegistryService
from registry.execution import ApprovedApiExecutor
from diagnostic_contracts.authority import production_registry
from diagnostic_contracts.ingestion import IngestionService,Payload,ProducerKey,digest
from diagnostic_contracts.integration import SemanticIntegration,Binding
from diagnostic_contracts.identity import TrustedPrincipal
from tests.test_diagnostic_identity import configured,token
from tests.test_diagnostic_authority_integration import ProofProvider
from tests.test_diagnostic_registry import payload as contract_document
from tests.test_ingestion import Keys,sign
from tests.test_postgres_integration import settings as settings_fixture,migrate
from persistence.runtime import open_persistence
from persistence.tables import ApiRegistryRow,JobRow,ProgressEventRow,InvestigationRow,ReportRow
from persistence.permits import AuthorizationPermit,PermitConsumption
from persistence.diagnostic_authority import AuthorityEventRow
from persistence.diagnostic_contracts import DiagnosticEventRow
from tools.evidence_catalog import build_catalog
from models.evidence import Evidence,EvidenceCollection
from models.diagnosis import Classification,Diagnosis,Hypothesis,ProposedFix
from models.validation import ValidationResult
from worker.runner import Worker
from mock_api.server import app as mock_app
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/phase14_6_1-acceptance'/os.getenv('PHASE14_6_ATTEMPT','run-1')
NODE=__import__('tests.runtime_tools',fromlist=['node_executable']).node_executable()

async def wait_file(name,limit=60):
    for _ in range(int(limit*20)):
        p=OUT/name
        if p.exists():return json.loads(p.read_text())
        await asyncio.sleep(.05)
    raise RuntimeError('Acceptance orchestration deadline')

class Egress:
    """Application-level socket restriction, not an OS/container sandbox."""
    def __enter__(self):
        self.original=socket.socket.connect
        allowed={'127.0.0.1','::1','localhost'}
        original=self.original
        def guarded(sock,address):
            if isinstance(address,tuple) and (address[0] not in allowed or address[1] not in {55432,8000,8001,5173}):
                raise PermissionError('Acceptance egress blocked')
            return original(sock,address)
        socket.socket.connect=guarded
        return self
    def __exit__(self,*_):socket.socket.connect=self.original

async def run(settings):
    result={'mode':'REAL_HTTP_CHROMIUM_DETERMINISTIC_AGENTS','checks':{},'http':[],'securityStop':None,'productionAccess':False}
    servers=[];tasks=[];frontend=None;browser=None;counts={}
    import logging
    handler=logging.FileHandler(OUT/'runtime.log');handler.setFormatter(logging.Formatter('%(message)s'));logging.getLogger().addHandler(handler);logging.getLogger().setLevel(logging.INFO)
    auth,rsa,_,_=configured()
    jwt_tokens=[token(rsa,exp=int(time.time())+3600) for _ in range(4)]
    owner,admin,reviewer,stranger=[await auth.authenticate(t) for t in jwt_tokens]
    admins=AdminAuthorization(frozenset({owner.subject_id,admin.subject_id}))
    headers={'Authorization':'Bearer '+jwt_tokens[0]};other={'Authorization':'Bearer '+jwt_tokens[3]}
    signing=Ed25519PrivateKey.generate()
    async with open_persistence(settings,setup_checkpoints=True) as rt:
        factory=lambda db:RegistryService(db,admins=admins,environment='development',development_mock_origin='http://127.0.0.1:8001',resolver=lambda host,*_:{'127.0.0.1'} if host=='127.0.0.1' else set())
        app=create_app(runtime=rt,authenticator=auth,admins=admins,registry_factory=factory,durable_queue=True,cors_origins=[])
        try:
            for application,port in ((mock_app,8001),(app,8000)):
                server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,access_log=False,log_level='critical',log_config=None))
                servers.append(server);tasks.append(asyncio.create_task(server.serve()))
                for _ in range(100):
                    if server.started:break
                    if tasks[-1].done():await tasks[-1]
                    await asyncio.sleep(.05)
                assert server.started
            cleanenv={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG') if k in os.environ}
            log=(OUT/'vite.log').open('w')
            frontend=await asyncio.create_subprocess_exec(NODE,str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1',cwd=ROOT/'frontend',env=cleanenv,stdout=log,stderr=log)
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',trust_env=False,timeout=30) as client:
                async def request(method,path,**kwargs):
                    response=await client.request(method,path,**kwargs)
                    result['http'].append({'method':method,'path':path,'status':response.status_code})
                    assert not any(t in response.text for t in jwt_tokens)
                    return response
                assert (await request('GET','/api/v1/incidents')).status_code==401
                for _ in range(100):
                    try:
                        if (await client.get('http://127.0.0.1:5173',timeout=.5)).status_code==200:break
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.05)
                browser=await asyncio.create_subprocess_exec(NODE,str(ROOT/'tests/phase14_6_1_browser.mjs'),env={**cleanenv,'ACCEPT_DIR':str(OUT),'ACCEPT_TOKEN':jwt_tokens[0]},stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await wait_file('registry-ready.json')
                async with rt.database.transaction() as s:ep=await s.scalar(select(ApiRegistryRow))
                assert ep.status=='approved' and ep.owner_id==owner.subject_id
                result['checks']['real_ui_registry_approval']='PASS'
                assert (await request('POST',f'/api/v1/registry/{ep.api_id}/approve',headers=other)).status_code==403
                executed=await ApprovedApiExecutor(factory(rt.database),principal=owner).aexecute(ep.api_id,'health')
                assert executed.ok
                result['checks']['approved_local_mock_execution']='PASS'
                svc,authority=production_registry(rt.database,identities=auth,global_admins=admins,evidence_provider=ProofProvider())
                tenant=uuid4()
                for actor,role in ((owner,'owner'),(admin,'contract_admin'),(reviewer,'security_reviewer')):
                    await authority.set_membership(admin,tenant,actor.subject_id,[role],expected_revision=0)
                await authority.bind_endpoint(admin,tenant,ep.api_id,owner.subject_id,expected_revision=0,endpoint_revision=ep.revision)
                v=await svc.register(owner,tenant,ep.api_id,ep.revision,contract_document())
                for actor,status in ((owner,'owner_verified'),(reviewer,'security_reviewed'),(admin,'approved'),(admin,'active')):
                    v=await svc.transition(actor,tenant,v['contract_id'],1,status,v['revision'])
                result['checks']['independent_contract_lifecycle_service_interface']='PASS'
                captured=datetime.now(timezone.utc)
                fields=dict(routed_epoch=41,current_epoch=43,routed_term=2,current_term=2,domain='demo-domain',resource='resource-1',correlation='acceptance-1',producer='owner-coordinator',observed_at=captured.isoformat(),rejection='stale-fencing-epoch',reachability=True)
                body=dict(service='Phase 14.6 Synthetic Fencing',endpoint='/health',http_method='GET',status_code=503,actual_behavior='Dependency rejected an obsolete fencing epoch.',expected_behavior='Route with an authoritative current fencing epoch.',logs=[{'message':'Fencing rejection','correlation_id':'acceptance-1','fields':fields}])
                (OUT/'incident-form.json').write_text(json.dumps(body))
                created=await wait_file('incident-created.json');iid=created['incident_id']
                detail=(await request('GET',f'/api/v1/incidents/{iid}',headers=headers)).json();inv=detail['investigations'][0]
                snapshot=detail['incident']
                key=ProducerKey(signing.public_key(),owner.subject_id,tenant,ep.api_id,captured-timedelta(seconds=5),captured+timedelta(minutes=2),datetime.now(timezone.utc)+timedelta(seconds=20))
                # Key status is refreshed by the synthetic trusted key service, not cached locally.
                class KeyService:
                    async def resolve(self,kid):return replace(key,checked_until=datetime.now(timezone.utc)+timedelta(seconds=10)) if kid=='test-key' else None
                ingestion=IngestionService(rt.database,authority,keys=KeyService())
                p=Payload(version='signed-observations-1',envelope_id=uuid4(),grant_id=uuid4(),tenant_id=tenant,api_id=ep.api_id,producer_id=owner.subject_id,contract_id=UUID(v['contract_id']),contract_version=1,contract_digest=v['digest'],endpoint_revision=ep.revision,binding_revision=1,source='service_logs',resource='resource-1',correlation_id='acceptance-1',captured_at=captured,snapshot=snapshot,snapshot_digest=digest(snapshot))
                await ingestion.authorize_producer(admin,p)
                await ingestion.ingest(owner,sign(p,signing))
                verified=await ingestion.verify_provenance(owner,p.envelope_id,tenant_id=tenant,api_id=ep.api_id,contract_id=p.contract_id,contract_version=1,snapshot_digest=p.snapshot_digest)
                result['signed_evidence_verification']=verified
                async def binding(thread,snapshot_hash):
                    if thread!=inv['thread_id'] or snapshot_hash!=p.snapshot_digest:return None
                    return Binding(owner,tenant,ep.api_id,p.envelope_id)
                integration=SemanticIntegration(ingestion,binding_resolver=binding,producer_labels={(owner.subject_id,'service_logs'):'owner-coordinator'})
                def tick(name):counts[name]=counts.get(name,0)+1
                def classify(state):tick('classifier');return {'classification':Classification(category='dependency_failure',reasoning='Synthetic dependency rejection')}
                def investigate(state):
                    tick('log_investigator');e=next(e for e in build_catalog(state['sanitized_inputs']).values() if e.reference.endswith('/rejection'))
                    return {'evidence':EvidenceCollection(items=[Evidence(**{k:getattr(e,k) for k in ('evidence_id','source','reference','observation')},reliability=1)],completeness='partial')}
                def analyze(state):
                    tick('root_cause_analyst');ids=[e.evidence_id for e in state['evidence'].items if e.reference.endswith(('/routed_epoch','/current_epoch','/rejection'))]
                    return {'hypotheses':[Hypothesis(hypothesis_id='fencing',description='Qualified stale fencing epoch rejection',confidence=.6,supporting_evidence_ids=ids)],'diagnosis':Diagnosis(status='tentative',summary='Qualified stale fencing rejection; stale routing origin remains unconfirmed',selected_hypothesis_id='fencing',confidence=.6,uncertainty=['The source of obsolete routing is not established.'],discriminating_evidence_ids=ids)}
                def resolve(state):tick('resolution_generator');return {'proposed_fix':ProposedFix(plan_kind='diagnostic',summary='Inspect authoritative routing metadata',steps=['Inspect sanitized coordinator epoch authority and routing diagnostics'],verification_steps=['Request a human-approved controlled verification'],requires_human_approval=True,evidence_ids=state['diagnosis'].discriminating_evidence_ids)}
                def validate(state):tick('validator');return {'validation_results':[*state['validation_results'],ValidationResult(status='NOT_TESTED')]}
                # Execute the actual five agent implementations with an injected
                # deterministic structured-output transport, never a provider SDK.
                from agents import error_classifier,log_investigator,root_cause,resolution,validator,common
                from tools.evidence_catalog import handle_catalog
                from tests.test_agents import FakeLLM
                def forbidden_provider(*args,**kwargs):raise AssertionError('Paid provider initialization prohibited')
                common.ChatOpenAI=forbidden_provider
                provider_outputs=[]
                def actual(module,name,builder):
                    def invoke(state):
                        started=time.monotonic();response=builder(state)
                        model=FakeLLM(response)
                        update=module.create_agent(llm=model)(state)
                        provider_outputs.append({'node':name,'schema':model.schema.__name__,'options':model.options,
                            'response':response,'latency_ms':round((time.monotonic()-started)*1000,3),
                            'input_sha256':hashlib.sha256(model.calls[0][1].content.encode()).hexdigest(),
                            'real_provider':False,'token_usage':None})
                        return update
                    return invoke
                def envelope(state):
                    e=handle_catalog(state['sanitized_inputs'])
                    return {k:e[k] for k in ('evidence_contract','evidence_snapshot')},e['evidence_catalog']
                def classification_response(state):
                    tick('classifier');meta,catalog=envelope(state)
                    return {**meta,'classification':{'category':'dependency_failure','confidence':.8,
                        'reasoning':'Synthetic dependency rejection','evidence_references':[e['handle'] for e in catalog if e['reference']=='/status_code']}}
                def investigator_response(state):
                    tick('log_investigator');meta,catalog=envelope(state)
                    return {**meta,'selected_evidence_handles':[e['handle'] for e in catalog if e['reference'].endswith('/rejection')],
                        'completeness':'partial','diagnostic_gaps':[]}
                def with_handles(state,value):
                    meta,catalog=envelope(state)
                    byref={e['reference']:e['handle'] for e in catalog}
                    aliases={e.evidence_id:byref[e.reference] for e in state['evidence'].items}
                    def replace(v):
                        if isinstance(v,dict):return {k:replace(x) for k,x in v.items()}
                        if isinstance(v,list):return [replace(x) for x in v]
                        return aliases.get(v,v) if isinstance(v,str) else v
                    return {**meta,**replace(value)}
                def root_response(state):
                    return with_handles(state,{k:[h.model_dump(mode='json') for h in val] if k=='hypotheses' else val.model_dump(mode='json') for k,val in analyze(state).items()})
                def resolution_response(state):return with_handles(state,{'proposed_fix':resolve(state)['proposed_fix'].model_dump(mode='json')})
                def validation_response(state):tick('validator');return {'validation':{'status':'NOT_TESTED','checks':[]}}
                nodes={'classifier':actual(error_classifier,'classifier',classification_response),
                    'log_investigator':actual(log_investigator,'log_investigator',investigator_response),
                    'root_cause_analyst':actual(root_cause,'root_cause_analyst',root_response),
                    'resolution_generator':actual(resolution,'resolution_generator',resolution_response),
                    'validator':actual(validator,'validator',validation_response)}
                result['provider_outputs']=provider_outputs
                result['real_agent_implementations']=True
                (OUT/'provider-ready.json').write_text('{"ready":true}')
                await wait_file('execute-requested.json')
                from worker.queue import JobQueue
                worker=Worker(rt,queue=JobQueue(rt.database,max_attempts=1),workflow_options={'agents':nodes,'trusted_semantics_enabled':True,'trusted_semantics':integration})
                assert await worker.run_once()
                code=await asyncio.wait_for(browser.wait(),45)
                result['browser_exit_code']=code
                browser_data=json.loads((OUT/'browser-evidence.json').read_text())
                if browser_data.get('securityFailure'):
                    result['securityStop']=browser_data['securityFailure'];raise RuntimeError('SECURITY_STOP')
                async with rt.database.transaction() as s:
                    job=await s.scalar(select(JobRow).where(JobRow.investigation_id==UUID(inv['investigation_id'])))
                    investigation=await s.get(InvestigationRow,UUID(inv['investigation_id']))
                    events=list(await s.scalars(select(ProgressEventRow).where(ProgressEventRow.job_id==job.job_id).order_by(ProgressEventRow.sequence)))
                    result['database']={'revision':await s.scalar(text('SELECT version_num FROM app.alembic_version')),'job_status':job.status,'attempts':job.attempts,'node_counts':dict(counts),'event_sequences':[e.sequence for e in events],'nodes':[e.payload.get('node') for e in events if e.payload.get('node')], 'permit_count':await s.scalar(select(func.count()).select_from(AuthorizationPermit)), 'consumption_count':await s.scalar(select(func.count()).select_from(PermitConsumption)),'authority_audit_count':await s.scalar(select(func.count()).select_from(AuthorityEventRow)),'contract_audit_count':await s.scalar(select(func.count()).select_from(DiagnosticEventRow)),'checkpoint_count':await s.scalar(text('SELECT count(*) FROM langgraph.checkpoints')),'reports':await s.scalar(select(func.count()).select_from(ReportRow))}
                    state=investigation.diagnostic_state
                    permit_rows=list(await s.scalars(select(AuthorizationPermit)))
                    result['permit_records']=[{'permit_id':str(x.permit_id),'issued_at':x.issued_at.isoformat(),'expires_at':x.expires_at.isoformat(),'context_digest':digest(x.context),'operation':x.context['operation'],'snapshot_digest':x.context['snapshot_digest']} for x in permit_rows]
                    audit=list(await s.scalars(select(AuthorityEventRow).order_by(AuthorityEventRow.created_at)))
                    result['authority_audit']=[{'event_id':str(x.event_id),'action':x.action,'subject_ref':str(x.subject_ref),'details':x.details,'timestamp':x.created_at.isoformat()} for x in audit]
                    contract_audit=list(await s.scalars(select(DiagnosticEventRow).order_by(DiagnosticEventRow.sequence)))
                    result['contract_audit']=[{'sequence':x.sequence,'action':x.action,'event_hash':x.event_hash,'previous_hash':x.previous_hash,'timestamp':x.created_at.isoformat()} for x in contract_audit]
                    blobs=(await s.execute(text('SELECT blob FROM langgraph.checkpoint_blobs WHERE blob IS NOT NULL'))).scalars().all()
                    assert all(not any(t.encode() in bytes(blob) for t in jwt_tokens) for blob in blobs)
                    result['checkpoint_blob_digests']=[hashlib.sha256(bytes(blob)).hexdigest() for blob in blobs]
                    result['checks']['checkpoint_no_bearer_leak']='PASS' 
                result['persisted_state']=state
                assert state['diagnosis']['status']=='tentative' and state['proposed_fix']['requires_human_approval'] and state['validation_results'][-1]['status']=='NOT_TESTED'
                assert counts.get('root_cause_analyst')==1 and result['database']['consumption_count']>=3
                result['checks']['qualified_only_human_approval_not_tested']='PASS'
                assert (await request('GET',f'/api/v1/incidents/{iid}',headers=other)).status_code==404
                assert (await request('GET',f'/api/v1/investigations/{inv["investigation_id"]}/events',headers=other)).status_code==404
                result['checks']['real_http_owner_isolation']='PASS'
                response=await request('GET',f'/api/v1/incidents/{iid}/report',headers=headers)
                result['checks']['persisted_report_http']='PASS' if response.status_code==200 else 'FAIL'
                assert response.status_code==200
                public_report=response.json();result['persisted_public_report']=public_report
                assert public_report['diagnosis']['status']=='tentative' and public_report['validation_results'][-1]['status']=='NOT_TESTED'
                assert (await request('GET',f'/api/v1/incidents/{iid}/report',headers=other)).status_code==404
                assert (await request('GET',f'/api/v1/incidents/{iid}/report')).status_code==401
                replay=await request('GET',f'/api/v1/investigations/{inv["investigation_id"]}/events',headers={**headers,'Last-Event-ID':str(job.job_id)+':1'})
                ids=[l[4:].strip() for l in replay.text.splitlines() if l.startswith('id:')]
                assert len(ids)==len(events)-1 and len(ids)==len(set(ids))
                result['checks']['real_http_ordered_sse_replay']='PASS'
                async with rt.database.transaction() as s:
                    assert await s.scalar(select(func.count()).select_from(ReportRow))==1
                assert not await worker.run_once()
                result['checks']['no_duplicate_worker_execution']='PASS'
                await asyncio.to_thread(migrate,settings,'check');result['checks']['schema_consistency']='PASS'
                from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
                async def must_deny(name,operation):
                    try:await operation
                    except (DiagnosticDenied,DiagnosticConflict,ValueError):result['checks'][name]='PASS';return
                    result['securityStop']=name+' accepted unauthorized operation'
                    raise RuntimeError('SECURITY_STOP')
                await must_deny('invalid_signature_rejected',ingestion.ingest(owner,sign(p,Ed25519PrivateKey.generate())))
                await must_deny('cross_tenant_contract_denied',svc.history(owner,uuid4(),p.contract_id))
                await must_deny('owner_self_approval_denied',svc.transition(owner,tenant,p.contract_id,1,'approved',v['revision']))
                await must_deny('snapshot_mismatch_denied',ingestion.verify_provenance(owner,p.envelope_id,tenant_id=tenant,api_id=ep.api_id,contract_id=p.contract_id,contract_version=1,snapshot_digest='a'*64))
                original_keys=ingestion.keys;ingestion.keys=None
                await must_deny('missing_key_provider_denied',ingestion.verify_provenance(owner,p.envelope_id,tenant_id=tenant,api_id=ep.api_id,contract_id=p.contract_id,contract_version=1,snapshot_digest=p.snapshot_digest))
                ingestion.keys=original_keys
                from diagnostic_contracts.permits import PermitService
                permits=PermitService(ingestion,evaluator=lambda *_:{'pure_local_test':True},require_active=True)
                args=dict(tenant_id=tenant,api_id=ep.api_id,snapshot_digest=p.snapshot_digest)
                permit=await permits.issue(owner,p.envelope_id,**args)
                await permits.evaluate(owner,permit,**args)
                await must_deny('replayed_permit_denied',permits.evaluate(owner,permit,**args))
                expired=await permits.issue(owner,p.envelope_id,**args)
                await asyncio.sleep(5.1)
                await must_deny('expired_permit_denied',permits.evaluate(owner,expired,**args))
                await svc.transition(admin,tenant,p.contract_id,1,'revoked',v['revision'])
                before=counts.get('root_cause_analyst',0)
                from graph.workflow import build_workflow
                from graph.state import initialize_state
                held=await build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration).ainvoke(initialize_state(snapshot,inv['thread_id']))
                if counts.get('root_cause_analyst',0)!=before:
                    result['securityStop']='revoked_contract_downstream_analysis';raise RuntimeError('SECURITY_STOP')
                assert held['trusted_semantic'].status=='denied'
                result['checks']['revoked_contract_blocks_new_analysis']='PASS'
                assert (await request('POST',f'/api/v1/registry/{ep.api_id}/revoke',headers=headers)).status_code==200
                assert not (await ApprovedApiExecutor(factory(rt.database),principal=owner).aexecute(ep.api_id,'health')).ok
                result['checks']['revoked_endpoint_execution_blocked']='PASS'
                from persistence.database import Database
                from persistence.config import DatabaseSettings
                from types import SimpleNamespace
                bad=make_url(settings.database_url.get_secret_value()).set(database='missing_phase14_6_'+uuid4().hex+'_test')
                missing=Database(DatabaseSettings(database_url=bad.render_as_string(hide_password=False),checkpoint_database_url=settings.checkpoint_database_url))
                original_runtime=app.state.runtime
                try:
                    app.state.runtime=SimpleNamespace(database=missing)
                    unhealthy=await request('GET','/api/v1/health/ready')
                    failed=await request('GET','/api/v1/incidents',headers=headers)
                    result['checks']['real_database_outage_health_503']='PASS' if unhealthy.status_code==503 else 'FAIL'
                    result['database_outage_http']={'health':unhealthy.status_code,'incidents':failed.status_code,'response_details':[unhealthy.json().get('detail'),failed.json().get('detail')]}
                    if failed.status_code==200:
                        result['securityStop']='database_outage_returned_data';raise RuntimeError('SECURITY_STOP')
                finally:
                    app.state.runtime=original_runtime;await missing.close()
                result['report_gap']=None
                return result
        finally:
            (OUT/'backend-evidence.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
            if browser and browser.returncode is None:browser.terminate();await browser.wait()
            if frontend and frontend.returncode is None:frontend.terminate();await frontend.wait()
            for s in servers:s.should_exit=True
            await asyncio.gather(*tasks,return_exceptions=True)
            logging.getLogger().removeHandler(handler);handler.close()


def main():
    OUT.mkdir(exist_ok=True,parents=True)
    for name in ('registry-ready.json','incident-form.json','incident-created.json','provider-ready.json','execute-requested.json'):
        if (OUT/name).exists():raise RuntimeError('Acceptance directory already contains run markers')
    # Read only the explicitly authorized TEST_DATABASE_URL setting, never load
    # .env into application or child process environments.
    raw=os.getenv('TEST_DATABASE_URL')
    if not raw:
        line=next(l for l in (ROOT/'.env').read_text().splitlines() if l.startswith('TEST_DATABASE_URL='))
        raw=dotenv_values(stream=StringIO(line))['TEST_DATABASE_URL']
    url=make_url(raw);assert url.host in ('localhost','127.0.0.1') and url.port==55432 and url.database.endswith('_test')
    for k in list(os.environ):
        if k not in ('PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'):del os.environ[k]
    os.environ['TEST_DATABASE_URL']=raw
    os.environ['DIAGNOSTIC_CONTRACTS_ENABLED']='false' # explicit workflow override ONLY
    for port in (8000,8001,5173):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0:raise RuntimeError('Acceptance ports already occupied; no process stopped')
    fixture=settings_fixture.__wrapped__();settings=None
    result={}
    try:
        settings=next(fixture)
        with Egress():
            with socket.socket() as s:
                try:s.connect(('192.0.2.1',443));raise AssertionError('Egress guard failed')
                except PermissionError:pass
            result=asyncio.run(run(settings))
        result['isolation']={'new_disposable_database':True,'primary_migration':False,'private_env_not_loaded':True,'normal_env_flag':'false','external_socket_probe':'BLOCKED','browser_external_requests':'BLOCKED','limits':'Application socket/browser routing guards, not an OS-level sandbox. No production credentials injected; only local disposable PostgreSQL administration credential used by controller.'}
    except BaseException as exc:
        if (OUT/'backend-evidence.json').exists():result=json.loads((OUT/'backend-evidence.json').read_text())
        result.update(acceptance_error=type(exc).__name__,error_detail='Acceptance stopped; inspect sanitized evidence, no application correction performed')
        raise
    finally:
        if settings is not None:
            with suppress(StopIteration):next(fixture)
        result['disposable_database_removed']=settings is not None
        (OUT/'backend-evidence.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({'acceptance_complete':True,'checks':result['checks'],'securityStop':result['securityStop']}))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('Acceptance stopped; inspect sanitized reports/phase14_6_1-acceptance evidence. Credentials withheld.')
        sys.exit(1)
