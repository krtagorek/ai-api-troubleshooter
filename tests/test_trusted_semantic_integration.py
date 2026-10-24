"""Real disposable PG and synthetic active contracts. No production activation."""
import asyncio
from dataclasses import replace
from uuid import UUID,uuid4
import json
import pytest
from sqlalchemy import select,text
from tests.test_postgres_integration import settings
from tests.test_ingestion_integration import fixture
from tests.test_ingestion import sign
from tests.test_trusted_semantic import sample
from persistence.runtime import open_persistence
from diagnostic_contracts.ingestion import Payload,digest
from diagnostic_contracts.integration import SemanticIntegration,Binding
from diagnostic_contracts.service import DiagnosticRegistry
from persistence.diagnostic_contracts import DiagnosticContractRow
from graph.state import initialize_state,serialize_state
from graph.workflow import build_workflow
from models.evidence import Evidence,EvidenceCollection
from models.diagnosis import Diagnosis,Hypothesis,Classification
from tools.evidence_catalog import build_catalog
from langgraph.checkpoint.memory import InMemorySaver
pytestmark=pytest.mark.integration

async def setup(rt,*,active=True):
    ingestion,p,key,keys,o,a,authority=await fixture(rt)
    snapshot,_,_=sample()
    # Captured observation timestamp is exactly signature-bound.
    snapshot['logs'][0]['fields']['observed_at']=p.captured_at.isoformat()
    p=Payload.model_validate({**p.model_dump(),'snapshot':snapshot,'snapshot_digest':digest(snapshot)})
    if active:
        registry=DiagnosticRegistry(rt.database,tenants=authority,admins=authority.global_admins,ownership=authority,security=authority)
        async with rt.database.transaction() as s:parent=await s.get(DiagnosticContractRow,p.contract_id);revision=parent.revision
        await registry.transition(a,p.tenant_id,p.contract_id,1,'active',revision)
    await ingestion.ingest(o,sign(p,key))
    binding=Binding(o,p.tenant_id,p.api_id,p.envelope_id)
    async def resolver(thread,snapshot_digest):return binding
    integration=SemanticIntegration(ingestion,binding_resolver=resolver,producer_labels={(p.producer_id,p.source):'owner-coordinator'})
    return integration,p,o,a,authority


def agents():
    calls=[]
    def classifier(state):return {'classification':Classification(reasoning='Synthetic')}
    def investigator(state):
        entry=next(e for e in build_catalog(state['sanitized_inputs']).values() if e.reference.endswith('/rejection'))
        return {'evidence':EvidenceCollection(items=[Evidence(evidence_id=entry.evidence_id,reference=entry.reference,
            source=entry.source,observation=entry.observation,reliability=1)],completeness='partial')}
    def analyst(state):
        calls.append(state)
        from diagnostic_contracts.integration import current_analysis
        assert current_analysis(state['sanitized_inputs'])['qualified_analysis']
        ident=state['evidence'].items[0].evidence_id
        return {'hypotheses':[Hypothesis(hypothesis_id='h',description='Qualified fencing rejection',confidence=.6,supporting_evidence_ids=[ident])],
            'diagnosis':Diagnosis(status='supported',summary='Synthetic mechanism',selected_hypothesis_id='h',confidence=.6)}
    def resolution(state):return {'proposed_fix':None}
    return {'classifier':classifier,'log_investigator':investigator,'root_cause_analyst':analyst,'resolution_generator':resolution},calls


def test_valid_active_contract_promotes_and_checkpoints_only_refs(settings):
    async def run():
        async with open_persistence(settings) as rt:
            integration,p,o,a,au=await setup(rt)
            nodes,calls=agents();state=initialize_state(p.snapshot,'contract-test')
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration)
            result=await graph.ainvoke(state)
            assert len(calls)==1 and result['diagnosis'].status=='tentative'
            assert result['status']=='awaiting_evidence' and result['proposed_fix'] is None
            assert result['trusted_semantic'].status=='qualified'
            payload=serialize_state(result)
            for forbidden in ('capability','session_id','signature','key_fingerprint','jwt','private_key'):
                assert forbidden not in payload.lower()
            assert not result['validation_results']
    asyncio.run(run())

@pytest.mark.parametrize('failure',['inactive','revoked','tenant','endpoint','snapshot','key_outage','missing_authority'])
def test_authorization_failure_never_calls_analyst(settings,failure):
    async def run():
        async with open_persistence(settings) as rt:
            integration,p,o,a,au=await setup(rt,active=failure!='inactive')
            if failure=='revoked':
                registry=DiagnosticRegistry(rt.database,tenants=au,admins=au.global_admins,ownership=au,security=au)
                async with rt.database.transaction() as s:row=await s.get(DiagnosticContractRow,p.contract_id);revision=row.revision
                await registry.transition(a,p.tenant_id,p.contract_id,1,'revoked',revision)
            if failure in ('tenant','endpoint'):
                async def bad(*_):return Binding(o,uuid4() if failure=='tenant' else p.tenant_id,uuid4() if failure=='endpoint' else p.api_id,p.envelope_id)
                integration.binding_resolver=bad
            if failure=='key_outage':integration.ingestion.keys=None
            if failure=='missing_authority':integration.producer_labels={}
            state=initialize_state(p.snapshot,'contract-fail')
            if failure=='snapshot':state['sanitized_inputs']=state['sanitized_inputs'].model_copy(update={'actual_behavior':'different snapshot'})
            nodes,calls=agents()
            result=await build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration).ainvoke(state)
            assert not calls and result['trusted_semantic'].status=='denied'
            assert result['status']=='awaiting_evidence' and result['final_report'] is None
    asyncio.run(run())


def test_checkpoint_resume_reacquires_and_denies_revoked_contract(settings):
    async def run():
        async with open_persistence(settings) as rt:
            integration,p,o,a,au=await setup(rt)
            saver=InMemorySaver();nodes,calls=agents();config={'configurable':{'thread_id':'resumed-contract'}}
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,
                checkpointer=saver,interrupt_before=['root_cause_analyst'])
            first=await graph.ainvoke(initialize_state(p.snapshot,'resumed-contract'),config)
            assert first['trusted_semantic'].status=='qualified' and not calls
            await integration.ingestion.revoke_producer(a,p.grant_id)
            # New graph instance represents worker/lease recovery. Old checkpoint
            # contains a qualified result, but the new permit must be denied.
            recovered=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,checkpointer=saver)
            result=await recovered.ainvoke(None,config)
            assert not calls and result['trusted_semantic'].status=='denied'
            assert result['status']=='awaiting_evidence'
    asyncio.run(run())


def test_real_postgres_checkpoint_model_recovery_and_revocation(settings):
    async def run():
        thread=str(uuid4())
        config={'configurable':{'thread_id':thread}}
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            integration,p,o,a,au=await setup(rt)
            nodes,calls=agents()
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,
                checkpointer=rt.saver,interrupt_before=['root_cause_analyst'])
            result=await graph.ainvoke(initialize_state(p.snapshot,thread),config)
            assert result['trusted_semantic'].status=='qualified' and not calls
            await integration.ingestion.revoke_producer(a,p.grant_id)
        async with open_persistence(settings) as recovered:
            from diagnostic_contracts.ingestion import IngestionService
            ingestion=IngestionService(recovered.database,au,keys=integration.ingestion.keys)
            integration=SemanticIntegration(ingestion,binding_resolver=integration.binding_resolver,producer_labels=integration.producer_labels)
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,checkpointer=recovered.saver)
            final=await graph.ainvoke(None,config)
            assert not calls and final['trusted_semantic'].status=='denied'
            assert final['status']=='awaiting_evidence'
    asyncio.run(run())


def test_recovery_at_resolution_rechecks_and_validation_stays_not_tested(settings):
    async def run():
        from models.diagnosis import ProposedFix
        from models.validation import ValidationResult,ValidationCheck
        async with open_persistence(settings) as rt:
            integration,p,o,a,au=await setup(rt);nodes,calls=agents()
            def propose(state):return {'proposed_fix':ProposedFix(summary='Read-only proposal',steps=['Inspect routing metadata'],verification_steps=['Request controlled verification'],evidence_ids=[state['evidence'].items[0].evidence_id])}
            nodes['resolution_generator']=propose
            nodes['validator']=lambda state:{'validation_results':[*state['validation_results'],ValidationResult(status='passed',checks=[ValidationCheck(name='fabricated',status='passed',detail='Must not be trusted')])]}
            result=await build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration).ainvoke(initialize_state(p.snapshot,'validation-truth'))
            assert result['validation_results'][-1].status=='NOT_TESTED'
            assert result['proposed_fix'].requires_human_approval
            # Pausing after analysis cannot preserve authority for resolution.
            saver=InMemorySaver();config={'configurable':{'thread_id':'resolution-resume'}}
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,checkpointer=saver,interrupt_before=['resolution_generator'])
            await graph.ainvoke(initialize_state(p.snapshot,'resolution-resume'),config)
            await integration.ingestion.revoke_producer(a,p.grant_id)
            resumed=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration,checkpointer=saver)
            result=await resumed.ainvoke(None,config)
            assert result['trusted_semantic'].status=='denied' and result['proposed_fix'] is None
    asyncio.run(run())


def test_real_worker_events_replay_and_lease_recovery(settings):
    async def run():
        from persistence.repository import InvestigationRepository
        from worker.runner import Worker
        from worker.queue import JobQueue,NODES
        from tests.test_worker_integration import expire
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            integration,p,o,a,au=await setup(rt);nodes,calls=agents()
            state=initialize_state(p.snapshot,str(uuid4()))
            identifier=await InvestigationRepository(rt.database).create(state,owner_id=o.subject_id)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='synthetic-worker')
            first=await queue.claim()
            options={'agents':nodes,'trusted_semantics_enabled':True,'trusted_semantics':integration}
            worker=Worker(rt,queue=queue,workflow_options={**options,'interrupt_before':['root_cause_analyst']})
            await worker.graph.ainvoke(state,{'configurable':{'thread_id':state['thread_id']}})
            assert not calls
            await integration.ingestion.revoke_producer(a,p.grant_id)
            await expire(rt,first)
        async with open_persistence(settings) as recovered:
            from diagnostic_contracts.ingestion import IngestionService
            integration=SemanticIntegration(IngestionService(recovered.database,au,keys=integration.ingestion.keys),
                binding_resolver=integration.binding_resolver,producer_labels=integration.producer_labels)
            worker=Worker(recovered,workflow_options={'agents':nodes,'trusted_semantics_enabled':True,'trusted_semantics':integration})
            assert await worker.run_once()
            assert not calls
            row=await InvestigationRepository(recovered.database).get(identifier)
            assert row.status=='awaiting_evidence' and row.state['trusted_semantic'].status=='denied'
            _,status,highest,events=await worker.queue.events(identifier,principal=o)
            assert status=='awaiting_evidence' and [e['sequence'] for e in events]==list(range(1,highest+1))
            assert all(e['data'].get('node') in NODES for e in events if 'node' in e['data'])
            _,_,_,replay=await worker.queue.events(identifier,principal=o,after=2)
            assert replay==events[2:]
            assert not await worker.run_once()
            for text in ('capability','signature','session_id','key_fingerprint'):
                assert text not in json.dumps(events)
    asyncio.run(run())


def test_revocation_race_between_committed_evaluation_and_analyst(settings,monkeypatch):
    async def run():
        from diagnostic_contracts.permits import PermitService
        async with open_persistence(settings) as rt:
            integration,p,o,a,au=await setup(rt);nodes,calls=agents()
            async with rt.database.transaction() as s:parent=await s.get(DiagnosticContractRow,p.contract_id);revision=parent.revision
            entered=asyncio.Event();release=asyncio.Event();seen=[]
            original=PermitService._context
            async def barrier(self,s,actor,row):
                value=await original(self,s,actor,row);seen.append(1)
                if len(seen)==2:
                    entered.set();await release.wait()
                return value
            monkeypatch.setattr(PermitService,'_context',barrier)
            graph=build_workflow(agents=nodes,trusted_semantics_enabled=True,trusted_semantics=integration)
            task=asyncio.create_task(graph.ainvoke(initialize_state(p.snapshot,'semantic-race')))
            await asyncio.wait_for(entered.wait(),2)
            registry=DiagnosticRegistry(rt.database,tenants=au,admins=au.global_admins,ownership=au,security=au)
            revoking=asyncio.create_task(registry.transition(a,p.tenant_id,p.contract_id,1,'revoked',revision))
            await asyncio.sleep(.05);assert not revoking.done()
            release.set()
            await revoking
            result=await task
            assert not calls and result['trusted_semantic'].status=='denied'
            assert result['status']=='awaiting_evidence'
    asyncio.run(run())
