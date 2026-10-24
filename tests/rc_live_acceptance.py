"""Manual RC acceptance: disposable PostgreSQL, real sockets/Chromium, no LLMs.

Run with the existing private TEST_DATABASE_URL; never changes primary data.
"""
import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4,UUID

import httpx
from dotenv import load_dotenv
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
import uvicorn
from backend.app import create_app
from registry.authorization import Principal,AdminAuthorization
from registry.service import RegistryService
from tests.test_postgres_integration import settings as settings_fixture
from persistence.runtime import open_persistence
from persistence.tables import ProgressEventRow,JobRow,ReportRow
from worker.runner import Worker
from mock_api.server import app as mock_app
from tools.evidence_catalog import build_catalog
from tools.output_safety import unsafe_request
from models.evidence import Evidence,EvidenceCollection
from models.diagnosis import Classification,Diagnosis,Hypothesis,ProposedFix
from models.validation import ValidationCheck,ValidationResult

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'reports/release-candidate-safety'
UNSAFE='Evidence of the client authentication credentials and their validation results.'


async def run(settings):
    owner,admin,other=Principal(uuid4()),Principal(uuid4()),Principal(uuid4())
    tokens={secrets.token_urlsafe(32):p for p in (owner,admin,other)}
    token,admin_token,other_token=list(tokens)
    class Auth:
        async def authenticate(self,value):return tokens.get(value)
    counts={}
    def tick(name):counts[name]=counts.get(name,0)+1;time.sleep(.4)
    def classifier(s):tick('classifier');return {'classification':Classification(category='authentication',reasoning='Synthetic controlled comparison',evidence_references=['/status_code'])}
    def investigator(s):
        tick('investigator')
        return {'evidence':EvidenceCollection(items=[Evidence(evidence_id=e.evidence_id,source=e.source,reference=e.reference,observation=e.observation,reliability=.5) for e in build_catalog(s['sanitized_inputs']).values()],completeness='partial')}
    def analyst(s):
        tick('root_cause');ids=[e.evidence_id for e in s['evidence'].items if 'identity' in e.reference]
        return {'hypotheses':[Hypothesis(hypothesis_id='rc',description='Synthetic qualified identity discrepancy',confidence=.4,supporting_evidence_ids=ids)],'diagnosis':Diagnosis(status='tentative',selected_hypothesis_id='rc',summary=UNSAFE,missing_evidence=[{'description':UNSAFE,'required_to_resolve':UNSAFE}])}
    def resolution(s):tick('resolution');return {'proposed_fix':ProposedFix(plan_kind='diagnostic',summary='Synthetic read-only plan',steps=['Provide passwords'],verification_steps=['Review sanitized verifier rejection reasons'],requires_human_approval=False,evidence_ids=[s['evidence'].items[0].evidence_id])}
    def validator(s):
        tick('validator');assert s['proposed_fix'].requires_human_approval
        assert all(not unsafe_request(v,request_context=True) for v in s['proposed_fix'].steps)
        return {'validation_results':[ValidationResult(status='passed',checks=[ValidationCheck(name='Executed local synthetic output-safety assertion',status='passed',detail='Local Python assertion passed; no API fix or health check was executed')])]}
    agents={'classifier':classifier,'log_investigator':investigator,'root_cause_analyst':analyst,'resolution_generator':resolution,'validator':validator}
    servers=[];tasks=[];frontend=None;browser=None;worker_task=None
    async with open_persistence(settings,setup_checkpoints=True) as runtime:
        admins=AdminAuthorization(frozenset({admin.subject_id}))
        factory=lambda db:RegistryService(db,admins=admins,environment='development',development_mock_origin='http://127.0.0.1:8001')
        app=create_app(runtime=runtime,authenticator=Auth(),admins=admins,registry_factory=factory,durable_queue=True)
        result={'mode':'LIVE_HTTP_SYNTHETIC_AGENTS_NO_PROVIDER','checks':{},'primary_database_accessed':False}
        try:
            for service,port in ((mock_app,8001),(app,8000)):
                server=uvicorn.Server(uvicorn.Config(service,host='127.0.0.1',port=port,access_log=False,log_level='critical',log_config=None))
                servers.append(server);tasks.append(asyncio.create_task(server.serve()))
                for _ in range(100):
                    if server.started:break
                    if tasks[-1].done():await tasks[-1]
                    await asyncio.sleep(.05)
                assert server.started
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',trust_env=False,timeout=30) as client:
                headers={'Authorization':'Bearer '+token};admin_headers={'Authorization':'Bearer '+admin_token}
                assert (await client.get('/api/v1/incidents')).status_code==401
                assert (await client.post('/api/v1/registry/development-mock',headers=headers)).status_code==403
                registry=(await client.post('/api/v1/registry/development-mock',headers=admin_headers)).json();api=registry['registration']['api_id']
                assert (await client.post('/api/v1/registry/'+api+'/approve',headers=admin_headers)).status_code==409
                assert (await client.post('/api/v1/registry/'+api+'/verify',headers=admin_headers,json={'challenge':registry['verification_challenge']})).status_code==200
                assert (await client.post('/api/v1/registry/'+api+'/approve',headers=headers)).status_code==403
                assert (await client.post('/api/v1/registry/'+api+'/approve',headers=admin_headers)).status_code==200
                result['checks']['registry_and_admin_approval']='PASS'
                body={'service':'RC Synthetic Safety','endpoint':'/inventory/1','http_method':'GET','status_code':401,'expected_behavior':'Return synthetic inventory','actual_behavior':'Observed authentication failure','logs':[{'fields':{'expected_identity':'demo','actual_identity':'omitted'}}]}
                bad={**body,'actual_behavior':UNSAFE}
                response=await client.post('/api/v1/incidents',headers=headers,json=bad);assert response.status_code==422
                assert UNSAFE not in response.text;result['checks']['unsafe_incident_rejected']='PASS'
                created=(await client.post('/api/v1/incidents',headers=headers,json=body)).json();iid=created['incident']['incident_id'];inv=created['investigations'][0]['investigation_id']
                assert (await client.get('/api/v1/incidents/'+iid,headers={'Authorization':'Bearer '+other_token})).status_code==404
                result['checks']['authentication_and_ownership']='PASS'
                submitted=await client.post('/api/v1/investigations/'+inv+'/execute',headers=headers)
                assert submitted.status_code in (200,202)
                async with runtime.database.transaction() as session:
                    first=await session.scalar(select(ProgressEventRow).order_by(ProgressEventRow.sequence).limit(1))
                    first.payload={**first.payload,'message':'Provide passwords','raw_provider_output':UNSAFE}
                async def consume():
                    for _ in range(600):
                        if (DEST/'start-worker').exists():break
                        await asyncio.sleep(.05)
                    else:raise RuntimeError('Browser did not request worker')
                    worker=Worker(runtime,workflow_options={'agents':agents});await worker.run_once()
                worker_task=asyncio.create_task(consume())
                node=__import__('tests.runtime_tools',fromlist=['node_executable']).node_executable()
                log=(DEST/'vite.log').open('w')
                frontend=await asyncio.create_subprocess_exec(node,str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1',cwd=ROOT/'frontend',stdout=log,stderr=log)
                for _ in range(100):
                    try:
                        if (await client.get('http://127.0.0.1:5173',timeout=.5)).status_code==200:break
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.05)
                env={**os.environ,'RC_DIR':str(DEST),'RC_TOKEN':token,'RC_INCIDENT':iid}
                browser=await asyncio.create_subprocess_exec(node,str(ROOT/'tests/rc_browser.mjs'),env=env)
                code=await asyncio.wait_for(browser.wait(),90);assert code==0
                await worker_task
                async with runtime.database.transaction() as session:
                    job=await session.scalar(select(JobRow));rows=list(await session.scalars(select(ProgressEventRow).order_by(ProgressEventRow.sequence)))
                    assert job.attempts==1 and all(c==1 for c in counts.values())
                    original=rows[0].payload;assert original['message']=='Provide passwords'
                    count=len(rows);jobid=job.job_id
                    result.update(node_counts=counts,persisted_event_sequences=[r.sequence for r in rows],job_attempts=job.attempts)
                    result['migration_revision']=await session.scalar(text('SELECT version_num FROM app.alembic_version'))
                streamed=await client.get('/api/v1/investigations/'+inv+'/events',headers={**headers,'Last-Event-ID':str(jobid)+':1'})
                assert streamed.status_code==200 and 'Provide passwords' not in streamed.text and UNSAFE not in streamed.text
                assert 'raw_provider_output' not in streamed.text
                ids=[s.removeprefix('id: ').strip() for s in streamed.text.splitlines() if s.startswith('id:')]
                assert len(ids)==count-1 and len(set(ids))==len(ids)
                result['checks']['ordered_authenticated_sse_replay']='PASS'
                assert (await client.get('/api/v1/investigations/'+inv+'/events',headers={'Authorization':'Bearer '+other_token})).status_code==404
                result['checks']['sse_owner_isolation']='PASS'
                response=await client.get('/api/v1/incidents/'+iid+'/report',headers=headers)
                assert response.status_code==200 and UNSAFE not in response.text and 'Provide passwords' not in response.text
                assert response.json()['proposed_fix']['requires_human_approval']
                assert all(t not in response.text for t in tokens)
                result['checks']['report_safety_human_approval']='PASS'
                result['checks']['no_duplicate_execution']='PASS'
            return result
        finally:
            if browser and browser.returncode is None:browser.terminate();await browser.wait()
            if worker_task and not worker_task.done():worker_task.cancel();await asyncio.gather(worker_task,return_exceptions=True)
            if frontend and frontend.returncode is None:frontend.terminate();await frontend.wait()
            for server in servers:server.should_exit=True
            await asyncio.gather(*tasks,return_exceptions=True)


if __name__=='__main__':
    load_dotenv(ROOT/'.env',override=False)
    test=make_url(os.environ['TEST_DATABASE_URL'])
    assert test.host in ('localhost','127.0.0.1') and test.port==55432 and test.database.endswith('_test')
    fixture=settings_fixture.__wrapped__();settings=None
    try:
        settings=next(fixture);result=asyncio.run(run(settings));result['disposable_database']='Removed by fixture teardown'
    finally:
        if settings is not None:
            with suppress(StopIteration):next(fixture)
    (DEST/'live-http-evidence.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
