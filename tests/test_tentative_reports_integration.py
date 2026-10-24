"""Publication authority and report/job atomicity in disposable PostgreSQL."""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from tests.test_postgres_integration import settings
from tests.test_trusted_semantic_integration import setup,agents
from persistence.runtime import open_persistence
from persistence.repository import InvestigationRepository
from persistence.tables import ReportRow,JobRow,InvestigationRow
from persistence.permits import PermitConsumption
from diagnostic_contracts.integration import publication_for
from diagnostic_contracts.service import DiagnosticRegistry
from persistence.diagnostic_contracts import DiagnosticContractRow
from worker.queue import JobQueue
from worker.runner import Worker
from graph.workflow import build_workflow
from graph.state import initialize_state
from models.diagnosis import ProposedFix
from models.validation import ValidationResult
pytestmark=pytest.mark.integration


def nodes():
    result,calls=agents();old=result['root_cause_analyst']
    def analyze(state):
        value=old(state)
        ids=[e.evidence_id for e in state['evidence'].items if e.reference.endswith(('/routed_epoch','/current_epoch','/rejection'))]
        value['hypotheses'][0]=value['hypotheses'][0].model_copy(update={'supporting_evidence_ids':ids})
        value['diagnosis']=value['diagnosis'].model_copy(update={'discriminating_evidence_ids':ids,'uncertainty':['Upstream routing origin remains unconfirmed']})
        return value
    def resolve(state):return {'proposed_fix':ProposedFix(plan_kind='diagnostic',summary='Review routing observations',steps=['Inspect sanitized epoch authority'],verification_steps=['Obtain human-approved controlled validation'],evidence_ids=state['diagnosis'].discriminating_evidence_ids)}
    result.update(root_cause_analyst=analyze,resolution_generator=resolve,validator=lambda state:{'validation_results':[*state['validation_results'],ValidationResult(status='NOT_TESTED')]})
    return result,calls

async def arrange(rt):
    integration,p,o,a,au=await setup(rt);dependencies,calls=nodes()
    options={'agents':dependencies,'trusted_semantics_enabled':True,'trusted_semantics':integration}
    state=initialize_state(p.snapshot,str(uuid4()))
    identifier=await InvestigationRepository(rt.database).create(state,owner_id=o.subject_id)
    return integration,p,o,a,au,options,state,identifier


def test_worker_persists_tentative_report_atomically_and_once(settings):
    async def run():
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='tentative-report')
            worker=Worker(rt,queue=queue,workflow_options=options)
            assert await worker.run_once()
            saved=await InvestigationRepository(rt.database).get(identifier)
            assert saved.status=='completed' and saved.state['stop_reason']=='tentative_diagnostic_report'
            report=await InvestigationRepository(rt.database).get_report(identifier)
            assert report.validation_results[-1].status=='NOT_TESTED' and report.diagnosis.status=='tentative'
            assert report.proposed_fix.requires_human_approval
            async with rt.database.transaction() as s:
                row=await s.scalar(select(JobRow).where(JobRow.investigation_id==identifier));assert row.status=='succeeded' and row.attempts==1
                assert await s.scalar(select(func.count()).select_from(ReportRow))==1
            assert not await worker.run_once()
            _,_,_,events=await queue.events(identifier,principal=o)
            _,_,_,replay=await queue.events(identifier,principal=o,after=1)
            assert replay==events[1:]
            assert (await InvestigationRepository(rt.database).get_report(identifier))==report
    asyncio.run(run())


def test_revocation_after_final_checkpoint_before_database_publication(settings):
    async def run():
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='revoked-publication');claim=await queue.claim()
            graph=build_workflow(**options,checkpointer=rt.saver)
            completed=await graph.ainvoke(state,{'configurable':{'thread_id':state['thread_id']}})
            assert completed['final_report'] and completed['status']=='completed'
            clean,ticket=await publication_for(completed,options)
            assert ticket is not None
            registry=DiagnosticRegistry(rt.database,tenants=au,admins=au.global_admins,ownership=au,security=au)
            async with rt.database.transaction() as s:row=await s.get(DiagnosticContractRow,p.contract_id);revision=row.revision
            await registry.transition(a,p.tenant_id,p.contract_id,1,'revoked',revision)
            await queue.finish(claim,state=clean,publication=ticket)
            saved=await InvestigationRepository(rt.database).get(identifier)
            assert saved.status=='awaiting_evidence' and saved.state['final_report'] is None and saved.state['proposed_fix'] is None
            assert await InvestigationRepository(rt.database).get_report(identifier) is None
            # Original authorized checkpoint remains immutable; recovery rechecks.
            checkpoint=await graph.aget_state({'configurable':{'thread_id':state['thread_id']}})
            assert checkpoint.values['final_report']
    asyncio.run(run())


def test_publication_race_holds_revocation_until_atomic_report_commit(settings,monkeypatch):
    async def run():
        from diagnostic_contracts.integration import Publication
        async with open_persistence(settings) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='publication-race');claim=await queue.claim()
            completed=await build_workflow(**options).ainvoke(state)
            clean,ticket=await publication_for(completed,options)
            entered=asyncio.Event();release=asyncio.Event();original=Publication.check
            async def paused(self,s,value):
                await original(self,s,value);entered.set();await release.wait()
            monkeypatch.setattr(Publication,'check',paused)
            saving=asyncio.create_task(queue.finish(claim,state=clean,publication=ticket))
            await asyncio.wait_for(entered.wait(),2)
            revoked=asyncio.create_task(integration.ingestion.revoke_producer(a,p.grant_id))
            await asyncio.sleep(.05);assert not revoked.done()
            release.set();await saving;await revoked
            assert await InvestigationRepository(rt.database).get_report(identifier)
            # Publication linearized before revocation; no later use is allowed.
            held,next_ticket=await publication_for(clean,options)
            assert next_ticket is None and held['final_report'] is None
    asyncio.run(run())


@pytest.mark.parametrize('failure',['missing_ticket','provider_outage','changed_state'])
def test_no_bypass_or_technical_failure_can_publish(settings,failure):
    async def run():
        async with open_persistence(settings) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='denied');claim=await queue.claim()
            completed=await build_workflow(**options).ainvoke(state)
            ticket=None;clean=completed
            if failure=='provider_outage':integration.ingestion.keys=None;clean,ticket=await publication_for(completed,options)
            elif failure=='changed_state':
                clean,ticket=await publication_for(completed,options)
                clean={**clean,'stop_reason':'tampered_after_issue'}
            await queue.finish(claim,state=clean,publication=ticket)
            assert await InvestigationRepository(rt.database).get_report(identifier) is None
            saved=await InvestigationRepository(rt.database).get(identifier)
            assert saved.status=='awaiting_evidence' and saved.state['proposed_fix'] is None
    asyncio.run(run())


def test_report_and_consumption_rollback_together_then_recover_once(settings,monkeypatch):
    async def run():
        async with open_persistence(settings) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=o,correlation_id='atomic');claim=await queue.claim()
            completed=await build_workflow(**options).ainvoke(state)
            clean,ticket=await publication_for(completed,options)
            original=queue._event
            def crash(*args,**kwargs):raise RuntimeError('synthetic crash before commit')
            monkeypatch.setattr(queue,'_event',crash)
            with pytest.raises(RuntimeError):await queue.finish(claim,state=clean,publication=ticket)
            async with rt.database.transaction() as s:
                assert await s.get(PermitConsumption,ticket.permit.permit_id) is None
                assert await s.scalar(select(func.count()).select_from(ReportRow))==0
                job=await s.get(JobRow,claim.job_id);assert job.status=='running'
            monkeypatch.setattr(queue,'_event',original)
            await queue.finish(claim,state=clean,publication=ticket)
            assert await InvestigationRepository(rt.database).get_report(identifier)
    asyncio.run(run())


def test_revocation_before_final_report_node_preserves_hold(settings):
    async def run():
        from langgraph.checkpoint.memory import InMemorySaver
        async with open_persistence(settings) as rt:
            integration,p,o,a,au,options,state,identifier=await arrange(rt)
            saver=InMemorySaver();config={'configurable':{'thread_id':state['thread_id']}}
            graph=build_workflow(**options,checkpointer=saver,interrupt_before=['final_report'])
            paused=await graph.ainvoke(state,config)
            assert paused['validation_results'][-1].status=='NOT_TESTED'
            await integration.ingestion.revoke_producer(a,p.grant_id)
            result=await build_workflow(**options,checkpointer=saver).ainvoke(None,config)
            assert result['status']=='awaiting_evidence' and result['final_report'] is None
            assert result['diagnosis'].status=='inconclusive'
    asyncio.run(run())
