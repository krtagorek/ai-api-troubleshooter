"""Isolated acceptance child processes only. Never imported by application entrypoints.

Configuration arrives via stdin, not command arguments or on-disk credentials.
Barriers expire and are available only in this explicit test executable.
"""
import asyncio,json,os,sys,socket,time,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
from uuid import UUID
from sqlalchemy.engine import make_url
from diagnostic_contracts.identity import JWTAuthenticator
from tests.test_diagnostic_identity import Keys,Revocations
from persistence.config import DatabaseSettings
from persistence.runtime import open_persistence
from registry.authorization import AdminAuthorization
from registry.service import RegistryService
from diagnostic_contracts.authority import production_registry
from tests.test_diagnostic_authority_integration import ProofProvider
from diagnostic_contracts.ingestion import IngestionService,ProducerKey
from diagnostic_contracts.integration import SemanticIntegration,Binding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from worker.queue import JobQueue
from worker.runner import Worker


def auth_from(c):
    return JWTAuthenticator(issuer='https://identity.example.invalid',audience='troubleshooter',keys=Keys(c['jwks']),revocations=Revocations())

def restrict():
    original=socket.socket.connect
    def guarded(sock,address):
        if isinstance(address,tuple) and (address[0] not in ('127.0.0.1','localhost','::1') or address[1] not in (55432,8000,8001,5173)):
            raise PermissionError('Test process external access denied')
        return original(sock,address)
    socket.socket.connect=guarded
    from agents import common
    def forbidden(*a,**kw):raise AssertionError('Paid provider initialization prohibited')
    common.ChatOpenAI=forbidden


def emit(directory,name,value):
    p=Path(directory)/name
    # Atomic publication; no partial marker can be mistaken for a reached barrier.
    temporary=p.with_suffix('.tmp');temporary.write_text(json.dumps(value,default=str));os.replace(temporary,p)


async def barrier(c,point,**details):
    if c.get('crash_point')!=point:return
    emit(c['out'],'barrier.json',{'point':point,'pid':os.getpid(),'at':datetime.now(timezone.utc).isoformat(),**details})
    # Bounded event wait leaves DB locks and transaction semantics untouched.
    await asyncio.wait_for(asyncio.Event().wait(),60)


def agents(c):
    from agents import error_classifier,log_investigator,root_cause,resolution,validator
    from tests.test_agents import FakeLLM
    from tools.evidence_catalog import handle_catalog
    from diagnostic_contracts.ingestion import digest
    def call(module,name):
        def invoke(state):
            catalog=handle_catalog(state['sanitized_inputs']);meta={k:catalog[k] for k in ('evidence_contract','evidence_snapshot')}
            byref={e['reference']:e['handle'] for e in catalog['evidence_catalog']}
            ids=[byref[e.reference] for e in state['evidence'].items if e.reference.endswith(('/routed_epoch','/current_epoch','/rejection'))]
            if name=='classifier':response={**meta,'classification':{'category':'dependency_failure','confidence':.8,'reasoning':'Synthetic dependency rejection','evidence_references':[byref['/status_code']]}}
            elif name=='log_investigator':response={**meta,'selected_evidence_handles':[v for k,v in byref.items() if k.endswith('/rejection')],'completeness':'partial','diagnostic_gaps':[]}
            elif name=='root_cause_analyst':response={**meta,'hypotheses':[{'hypothesis_id':'fencing','description':'Qualified stale fencing rejection','confidence':.6,'supporting_evidence_ids':ids}], 'diagnosis':{'status':'tentative','summary':'Qualified stale fencing rejection; stale routing origin remains unconfirmed','selected_hypothesis_id':'fencing','confidence':.6,'uncertainty':['The upstream routing origin remains unconfirmed.'],'discriminating_evidence_ids':ids}}
            elif name=='resolution_generator':response={**meta,'proposed_fix':{'plan_kind':'diagnostic','summary':'Inspect authoritative routing metadata','steps':['Inspect sanitized coordinator epoch authority and routing diagnostics'],'verification_steps':['Request a human-approved controlled verification'],'requires_human_approval':True,'evidence_ids':ids}}
            else:response={'validation':{'status':'NOT_TESTED','checks':[]}}
            llm=FakeLLM(response);update=module.create_agent(llm=llm)(state)
            with (Path(c['out'])/('agent-'+str(os.getpid())+'.jsonl')).open('a') as f:f.write(json.dumps({'node':name,'pid':os.getpid(),'response':response,'schema':llm.schema.__name__,'schema_options':llm.options,'prompt_digest':digest(llm.calls[0][1].content),'provider':'deterministic_fake_llm','token_usage':None})+'\n')
            return update
        return invoke
    return {name:call(module,name) for module,name in ((error_classifier,'classifier'),(log_investigator,'log_investigator'),(root_cause,'root_cause_analyst'),(resolution,'resolution_generator'),(validator,'validator'))}


def validate_config(c):
    if c.get('role') not in ('worker','backend','mock'):raise ValueError('Unsupported acceptance role')
    urls=[make_url(c[k]) for k in ('database_url','checkpoint_database_url')]
    for url in urls:
        if not (url.host in ('localhost','127.0.0.1') and url.port==55432 and url.database.startswith('phase8_') and url.database.endswith('_test')):raise ValueError('Disposable local database required')
    if urls[0].database!=urls[1].database:raise ValueError('Checkpoint database mismatch')
    allowed=Path(__file__).resolve().parents[1]/'reports/phase14_6_2a-acceptance'
    if not Path(c['out']).resolve().is_relative_to(allowed):raise ValueError('Acceptance output scope required')
    if c.get('crash_point') not in (None,'claimed','investigator_checkpoint','semantic_committed','published','resolution_checkpoint','classifier_checkpoint'):raise ValueError('Unsupported crash point')


async def main(c):
    validate_config(c);restrict()
    settings=DatabaseSettings(database_url=c['database_url'],checkpoint_database_url=c['checkpoint_database_url'])
    auth=auth_from(c);admins=AdminAuthorization(frozenset(UUID(x) for x in c['admins']))
    emit(c['out'],'process-'+str(os.getpid())+'.json',{'pid':os.getpid(),'role':c['role'],'normal_flag':os.getenv('DIAGNOSTIC_CONTRACTS_ENABLED'),'explicit_semantic_override':c['role']=='worker','configuration_fingerprint':hashlib.sha256(json.dumps({k:v for k,v in c.items() if k not in ('database_url','checkpoint_database_url','actor_token')},sort_keys=True).encode()).hexdigest(),'environment_keys':sorted(os.environ),'private_configuration_loaded':False})
    if c['role']=='mock':
        import uvicorn
        from mock_api.server import app
        await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8001,access_log=False,log_level='critical')).serve();return
    async with open_persistence(settings) as rt:
        if c['role']=='backend':
            from backend.app import create_app
            import uvicorn
            factory=lambda db:RegistryService(db,admins=admins,environment='development',development_mock_origin='http://127.0.0.1:8001',resolver=lambda h,*_:{'127.0.0.1'} if h=='127.0.0.1' else set())
            app=create_app(runtime=rt,authenticator=auth,admins=admins,registry_factory=factory,durable_queue=True,cors_origins=[])
            await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8000,access_log=False,log_level='critical')).serve();return
        assert c['role']=='worker'
        actor=await auth.authenticate(c['actor_token']);assert actor
        _,authority=production_registry(rt.database,identities=auth,global_admins=admins,evidence_provider=ProofProvider())
        class KeyService:
            async def resolve(self,kid):
                if kid!='test-key':return None
                return ProducerKey(Ed25519PublicKey.from_public_bytes(bytes.fromhex(c['public_signing_key'])),UUID(c['producer_id']),UUID(c['tenant_id']),UUID(c['api_id']),datetime.fromisoformat(c['key_from']),datetime.fromisoformat(c['key_until']),datetime.now(timezone.utc)+timedelta(seconds=10))
        ingestion=IngestionService(rt.database,authority,keys=KeyService())
        async def binding(thread,snapshot):
            if thread==c['thread_id'] and snapshot==c['snapshot_digest']:return Binding(actor,UUID(c['tenant_id']),UUID(c['api_id']),UUID(c['envelope_id']))
        class Integration(SemanticIntegration):
            evaluations=0
            async def evaluate(self,state):
                result=await super().evaluate(state);self.evaluations+=1
                if self.evaluations==2:await barrier(c,'semantic_committed',result_ref=result[0]['result_ref'])
                return result
        integration=Integration(ingestion,binding_resolver=binding,producer_labels={(UUID(c['producer_id']),'service_logs'):'owner-coordinator'})
        class Queue(JobQueue):
            async def claim(self):
                claim=await super().claim()
                if claim:
                    emit(c['out'],'claim-'+str(os.getpid())+'.json',{'pid':os.getpid(),'lease_id':str(claim.lease_id),'job_id':str(claim.job_id),'at':datetime.now(timezone.utc).isoformat()})
                    await barrier(c,'claimed',lease_id=str(claim.lease_id),job_id=str(claim.job_id))
                return claim
            async def progress(self,claim,node):
                result=await super().progress(claim,node)
                if node=='log_investigator':await barrier(c,'investigator_checkpoint')
                if node=='resolution_generator':await barrier(c,'resolution_checkpoint')
                if node=='classifier':await barrier(c,'classifier_checkpoint')
                return result
            async def finish(self,*a,**kw):
                await super().finish(*a,**kw)
                await barrier(c,'published')
        worker=Worker(rt,queue=Queue(rt.database,lease_seconds=2,max_attempts=3),workflow_options={'agents':agents(c),'trusted_semantics_enabled':True,'trusted_semantics':integration})
        # Poll for a bounded period, permitting PostgreSQL-clock expiry, not
        # forced lease updates. Exit after one claimed attempt or terminal absence.
        deadline=asyncio.get_running_loop().time()+15
        count=0
        while asyncio.get_running_loop().time()<deadline:
            if await worker.run_once():count+=1;break
            if c.get('terminal_expected'):break
            await asyncio.sleep(.1)
        emit(c['out'],'exit-'+str(os.getpid())+'.json',{'pid':os.getpid(),'claimed_attempts':count})

if __name__=='__main__':
    try:asyncio.run(main(json.load(sys.stdin)))
    except BaseException as exc:
        print('Isolated acceptance child stopped: '+type(exc).__name__,flush=True)
        sys.exit(1)
