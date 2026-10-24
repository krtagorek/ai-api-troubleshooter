"""Disposable PG only: legacy publication, recovery, HTTP isolation and replay."""
import asyncio
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select,func,text
from backend.app import create_app
from registry.authorization import Principal
from persistence.runtime import open_persistence
from persistence.repository import InvestigationRepository
from persistence.tables import ReportRow,JobRow
from graph.state import initialize_state
from graph.workflow import build_workflow
from worker.queue import JobQueue
from worker.runner import Worker
from tests.test_postgres_integration import settings,migrate
from tests.test_legacy_tentative_reports import sample,nodes
from tools.output_safety import SAFE_REQUEST
pytestmark=pytest.mark.integration


@pytest.mark.parametrize('mode',['normal','checkpoint_recovery','rollback','unsafe_checkpoint','report_mismatch','false_summary','absent_validation','failed_validation'])
def test_atomic_legacy_report_with_recovery_and_owner_isolation(settings,monkeypatch,mode):
    monkeypatch.setattr('agents.common.ChatOpenAI',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No paid calls')))
    async def run():
        async with open_persistence(settings,setup_checkpoints=True) as rt:
            s=sample();initial=initialize_state(s['sanitized_inputs'],s['thread_id']);owner=Principal(uuid4());stranger=Principal(uuid4())
            repo=InvestigationRepository(rt.database);identifier=await repo.create(initial,owner_id=owner.subject_id)
            queue=JobQueue(rt.database);await queue.submit(identifier,principal=owner,correlation_id='legacy-offline')
            options={'agents':nodes(s),'trusted_semantics_enabled':False}
            if mode=='normal':
                assert await Worker(rt,queue=queue,workflow_options=options).run_once()
            else:
                claim=await queue.claim()
                completed=await build_workflow(**options,checkpointer=rt.saver).ainvoke(initial,{'configurable':{'thread_id':s['thread_id']}})
                assert completed['final_report'] is not None
                if mode=='checkpoint_recovery':
                    # Deterministic expired-lease fixture; real OS crash behavior
                    # remains covered by the existing process acceptance suite.
                    async with rt.database.transaction() as session:
                        await session.execute(text("UPDATE app.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=:id"),{'id':claim.job_id})
                    assert await Worker(rt,queue=queue,workflow_options=options).run_once()
                else:
                    if mode=='unsafe_checkpoint':completed['proposed_fix']=completed['proposed_fix'].model_copy(update={'steps':[SAFE_REQUEST]})
                    if mode=='report_mismatch':completed['final_report']=completed['final_report'].model_copy(update={'diagnosis':completed['diagnosis'].model_copy(update={'summary':'Different unreviewed narrative'})})
                    if mode=='false_summary':completed['final_report']=completed['final_report'].model_copy(update={'summary':'Validation passed; signed provenance verified.'})
                    if mode in ('absent_validation','failed_validation'):
                        from models.validation import ValidationResult
                        values=[] if mode=='absent_validation' else [ValidationResult(status='failed')]
                        completed['validation_results']=values
                        completed['final_report']=completed['final_report'].model_copy(update={'validation_results':values})
                    if mode=='rollback':
                        original=queue._event
                        def crash(*args,**kwargs):raise RuntimeError('Synthetic precommit crash')
                        monkeypatch.setattr(queue,'_event',crash)
                        with pytest.raises(RuntimeError):await queue.finish(claim,state=completed)
                        async with rt.database.transaction() as session:
                            assert await session.scalar(select(func.count()).select_from(ReportRow))==0
                            assert (await session.get(JobRow,claim.job_id)).status=='running'
                        monkeypatch.setattr(queue,'_event',original)
                    await queue.finish(claim,state=completed)
            saved=await repo.get(identifier);held=mode in ('unsafe_checkpoint','report_mismatch','false_summary','absent_validation','failed_validation')
            assert saved.status==('awaiting_evidence' if held else 'completed')
            report=await repo.get_report(identifier)
            assert (report is None)==held
            if report:
                assert report.diagnosis.status=='tentative' and report.validation_results[-1].status=='NOT_TESTED'
                assert report.proposed_fix.requires_human_approval and saved.state.get('trusted_semantic') is None
            assert not await Worker(rt,queue=queue,workflow_options=options).run_once()
            async with rt.database.transaction() as session:
                assert await session.scalar(select(func.count()).select_from(ReportRow))==(0 if held else 1)
            job,status,high,events=await queue.events(identifier,principal=owner)
            assert status==('awaiting_evidence' if held else 'succeeded')
            assert [e['sequence'] for e in events]==list(range(1,high+1))
            assert sum(e['event'] in ('succeeded','awaiting_evidence') for e in events)==1
            assert (await queue.events(identifier,principal=owner,after=1))[3]==events[1:]
            class Identities:
                async def authenticate(self,token):return {'owner':owner,'stranger':stranger}.get(token)
            app=create_app(runtime=rt,authenticator=Identities(),durable_queue=True)
            async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                url='/api/v1/incidents/'+s['incident_id']+'/report'
                assert (await client.get(url,headers={'Authorization':'Bearer owner'})).status_code==(409 if held else 200)
                assert (await client.get(url,headers={'Authorization':'Bearer stranger'})).status_code==404
                assert (await client.get(url)).status_code==401
                response=await client.get('/api/v1/investigations/'+str(identifier)+'/events',headers={'Authorization':'Bearer owner'})
                assert response.status_code==200 and ('event: awaiting_evidence' if held else 'event: succeeded') in response.text
            assert '0008' in migrate(settings,'current')
            migrate(settings,'check')
    asyncio.run(run())
