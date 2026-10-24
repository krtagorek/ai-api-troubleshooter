"""Real PostgreSQL queue races, lease fencing, checkpoints and authenticated SSE."""
import asyncio
from datetime import timedelta
from uuid import uuid4, UUID
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select, func, update
from backend.app import create_app
from graph.state import initialize_state
from persistence.repository import InvestigationRepository
from persistence.runtime import open_persistence
from persistence.tables import JobRow, ProgressEventRow, InvestigationRow
from registry.authorization import Principal, AdminAuthorization
from tests.test_postgres_integration import settings
from tests.test_backend_integration import payload
from tests.test_workflow import fixtures
from worker.queue import JobQueue, LeaseLost
from worker.runner import Worker
from worker.checkpoints import LeasedPostgresSaver

pytestmark = pytest.mark.integration


async def create(runtime, actor):
    record = payload()
    record['incident_id'] = str(uuid4())
    state = initialize_state(record, str(uuid4()))
    identifier = await InvestigationRepository(runtime.database).create(state, owner_id=actor.subject_id)
    return identifier, state


async def expire(runtime, claim):
    async with runtime.database.transaction() as session:
        await session.execute(update(JobRow).where(JobRow.job_id == claim.job_id).values(
            lease_expires_at=func.clock_timestamp()-timedelta(seconds=1)))


async def available(runtime, job_id):
    async with runtime.database.transaction() as session:
        await session.execute(update(JobRow).where(JobRow.job_id == job_id).values(available_at=func.clock_timestamp()))


async def job(runtime, identifier):
    async with runtime.database.transaction() as session:
        return await session.scalar(select(JobRow).where(JobRow.investigation_id == identifier))


def test_submission_claim_races_ordered_events_and_cancellation(settings):
    async def run():
        actor, stranger = Principal(uuid4()), Principal(uuid4())
        async with open_persistence(settings) as runtime:
            identifier, _ = await create(runtime, actor)
            queue = JobQueue(runtime.database)
            receipts = await asyncio.gather(*[queue.submit(identifier, principal=actor, correlation_id=str(uuid4())) for _ in range(8)])
            assert len({r.job_id for r in receipts}) == 1
            with pytest.raises(LookupError): await queue.submit(identifier, principal=stranger, correlation_id='test')
            claims = await asyncio.gather(*[queue.claim() for _ in range(8)])
            claimed = [c for c in claims if c]
            assert len(claimed) == 1
            claim = claimed[0]
            await asyncio.gather(*[queue.progress(claim, 'classifier') for _ in range(8)])
            with pytest.raises(ValueError): await queue.progress(claim, 'password=never-store-this')
            with pytest.raises(LookupError): await queue.cancel(identifier, principal=stranger)
            await queue.cancel(identifier, principal=actor)
            assert await queue.heartbeat(claim)
            await queue.finish(claim, outcome='cancelled')
            _, status, highest, events = await queue.events(identifier, principal=actor)
            assert status == 'cancelled'
            assert [e['sequence'] for e in events] == list(range(1, highest+1))
            assert 'never-store' not in str(events)
            _, _, _, replay = await queue.events(identifier, principal=actor, after=2)
            assert replay == events[2:]
            assert (await queue.submit(identifier, principal=actor, correlation_id='retry')).job_id == claim.job_id
            row = await InvestigationRepository(runtime.database).get(identifier)
            assert row.status == 'cancelled'
            with pytest.raises(ValueError, match='fenced'):
                await InvestigationRepository(runtime.database).save(identifier, row.state)
    asyncio.run(run())


def test_expired_lease_fences_old_workers_and_exhausts_attempts(settings):
    async def run():
        actor = Principal(uuid4())
        async with open_persistence(settings) as runtime:
            identifier, _ = await create(runtime, actor)
            queue = JobQueue(runtime.database, max_attempts=2)
            await queue.submit(identifier, principal=actor, correlation_id='test')
            first = await queue.claim()
            await expire(runtime, first)
            second = await queue.claim()
            assert second.lease_id != first.lease_id
            for operation in (queue.heartbeat(first),queue.progress(first,'classifier'),queue.finish(first,outcome='failed'),queue.retry(first)):
                with pytest.raises(LeaseLost): await operation
            saver = LeasedPostgresSaver(runtime.checkpoint_pool, queue, first)
            with pytest.raises(LeaseLost): await saver.aput({}, {}, {}, {})
            with pytest.raises(LeaseLost): await saver.aput_writes({}, [], 'classifier')
            await expire(runtime, second)
            assert await queue.claim() is None
            assert (await job(runtime,identifier)).status == 'failed'
            assert (await InvestigationRepository(runtime.database).get(identifier)).status == 'failed'
    asyncio.run(run())


def test_checkpoint_recovery_avoids_repeating_completed_nodes(settings):
    async def run():
        actor = Principal(uuid4())
        agents, calls = fixtures()
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            identifier, state = await create(runtime,actor)
            queue = JobQueue(runtime.database)
            await queue.submit(identifier, principal=actor, correlation_id='test')
            worker = Worker(runtime,queue=queue,workflow_options={'agents':agents,'interrupt_before':['root_cause_analyst']})
            # Simulate process loss after earlier nodes committed durable checkpoints.
            first = await queue.claim()
            config = {'configurable':{'thread_id':state['thread_id']}}
            await worker.graph.ainvoke(state,config)
            assert calls['classifier'] == calls['investigator'] == 1
            await expire(runtime,first)
        # New connections/process state, same thread and job.
        async with open_persistence(settings) as runtime:
            worker = Worker(runtime,workflow_options={'agents':agents})
            assert await worker.run_once()
            assert calls['classifier'] == calls['investigator'] == 1
            assert (await job(runtime,identifier)).status == 'succeeded'
            row = await InvestigationRepository(runtime.database).get(identifier)
            assert row.status == 'completed'
            assert await InvestigationRepository(runtime.database).get_report(identifier)
            _, _, _, events = await worker.queue.events(identifier, principal=actor)
            assert events[-1]['event'] == 'succeeded'
    asyncio.run(run())


def test_retry_errors_safe_and_cancellation_stops_active_agent(settings):
    async def run():
        actor = Principal(uuid4())
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            identifier,_ = await create(runtime,actor)
            queue = JobQueue(runtime.database,max_attempts=2,lease_seconds=1)
            await queue.submit(identifier,principal=actor,correlation_id='test')
            agents,_ = fixtures()
            async def broken(_): raise RuntimeError('password=never-store-failure')
            agents['classifier'] = broken
            worker = Worker(runtime,queue=queue,workflow_options={'agents':agents})
            await worker.run_once()
            assert (await job(runtime,identifier)).status == 'queued'
            await available(runtime,(await job(runtime,identifier)).job_id)
            await worker.run_once()
            assert (await job(runtime,identifier)).status == 'failed'
            _,_,_,events = await queue.events(identifier,principal=actor)
            assert 'never-store-failure' not in str(events)
            second,_ = await create(runtime,actor)
            await queue.submit(second,principal=actor,correlation_id='test')
            entered = asyncio.Event()
            stopped = asyncio.Event()
            async def slow(_):
                entered.set()
                try: await asyncio.Event().wait()
                finally: stopped.set()
            agents['classifier'] = slow
            worker = Worker(runtime,queue=queue,workflow_options={'agents':agents})
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(),3)
            await queue.cancel(second,principal=actor)
            await asyncio.wait_for(task,3)
            assert stopped.is_set()
            assert (await job(runtime,second)).status == 'cancelled'
    asyncio.run(run())


def test_durable_api_sse_replay_authorization_and_idempotent_submission(settings):
    async def run():
        actor, other = Principal(uuid4()),Principal(uuid4())
        class Auth:
            async def authenticate(self,token): return {'owner':actor,'other':other}.get(token)
        headers = {'Authorization':'Bearer owner'}
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            app = create_app(runtime=runtime,authenticator=Auth(),durable_queue=True)
            async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.post('/api/v1/incidents',json=payload(),headers=headers)
                assert response.status_code == 201
                identifier = response.json()['investigations'][0]['investigation_id']
                prefix = '/api/v1/investigations/'+identifier
                assert (await client.get(prefix+'/events')).status_code == 401
                results = await asyncio.gather(*[client.post(prefix+'/execute',headers=headers) for _ in range(4)])
                assert all(r.status_code == 202 for r in results)
                job_id = results[0].json()['job_id']
                assert len({r.json()['job_id'] for r in results}) == 1
                assert (await client.post(prefix+'/cancel',headers={'Authorization':'Bearer other'})).status_code == 404
                assert (await client.get(prefix+'/events',headers={'Authorization':'Bearer other'})).status_code == 404
                agents,_ = fixtures()
                await Worker(runtime,workflow_options={'agents':agents}).run_once()
                response = await client.get(prefix+'/events',headers=headers)
                assert response.status_code == 200
                assert response.headers['content-type'].startswith('text/event-stream')
                assert response.headers['x-accel-buffering'] == 'no'
                assert f'id: {job_id}:1' in response.text
                assert 'event: succeeded' in response.text
                assert 'never-store' not in response.text
                replay = await client.get(prefix+'/events',headers={**headers,'Last-Event-ID':f'{job_id}:1'})
                assert f'id: {job_id}:1\n' not in replay.text
                assert 'event: succeeded' in replay.text
                for cursor in ('bad',f'{uuid4()}:1',f'{job_id}:999999',f'{job_id}:-1'):
                    assert (await client.get(prefix+'/events',headers={**headers,'Last-Event-ID':cursor})).status_code == 400
                # Retrying a terminal submission returns the existing durable receipt, without another run.
                assert (await client.post(prefix+'/execute',headers=headers)).json()['job_id'] == job_id
    asyncio.run(run())


def test_shutdown_lease_recovery_queued_cancel_and_atomic_rollback(settings):
    async def run():
        actor = Principal(uuid4())
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            queue = JobQueue(runtime.database,lease_seconds=1)
            identifier, _ = await create(runtime,actor)
            # An event insertion failure rolls back job and investigation together.
            with patch.object(JobQueue,'_event',side_effect=RuntimeError('simulated failure')):
                with pytest.raises(RuntimeError): await queue.submit(identifier,principal=actor,correlation_id='test')
            assert await job(runtime,identifier) is None
            assert (await InvestigationRepository(runtime.database).get(identifier)).status == 'pending'
            await queue.submit(identifier,principal=actor,correlation_id='test')
            entered = asyncio.Event()
            agents,_ = fixtures()
            async def slow(_):
                entered.set()
                await asyncio.Event().wait()
            agents['classifier'] = slow
            running = asyncio.create_task(Worker(runtime,queue=queue,workflow_options={'agents':agents}).run_once())
            await asyncio.wait_for(entered.wait(),3)
            running.cancel()
            with pytest.raises(asyncio.CancelledError): await running
            row = await job(runtime,identifier)
            assert row.status == 'running'
            from worker.queue import Claim
            await expire(runtime,Claim(row.job_id,identifier,row.lease_id))
            agents,calls = fixtures()
            await Worker(runtime,queue=queue,workflow_options={'agents':agents}).run_once()
            assert (await job(runtime,identifier)).status == 'succeeded'
            second,_ = await create(runtime,actor)
            await queue.submit(second,principal=actor,correlation_id='test')
            await queue.cancel(second,principal=actor)
            await queue.cancel(second,principal=actor)
            assert await queue.claim() is None
            assert (await job(runtime,second)).status == 'cancelled'
            _,_,_,events = await queue.events(second,principal=actor)
            assert [e['event'] for e in events] == ['queued','cancelled']
    asyncio.run(run())


def test_worker_timeout_and_fenced_completion_rollback(settings):
    async def run():
        actor=Principal(uuid4())
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            identifier,_=await create(runtime,actor)
            queue=JobQueue(runtime.database,lease_seconds=1,max_attempts=1)
            await queue.submit(identifier,principal=actor,correlation_id='test')
            agents,_=fixtures()
            async def slow(_): await asyncio.Event().wait()
            agents['classifier']=slow
            await Worker(runtime,queue=queue,workflow_options={'agents':agents},execution_timeout=1).run_once()
            assert (await job(runtime,identifier)).status == 'failed'
            second,_=await create(runtime,actor)
            await queue.submit(second,principal=actor,correlation_id='test')
            claim=await queue.claim()
            with patch.object(JobQueue,'_event',side_effect=RuntimeError('simulated failure')):
                with pytest.raises(RuntimeError): await queue.finish(claim,outcome='failed')
            assert (await job(runtime,second)).status == 'running'
            assert (await InvestigationRepository(runtime.database).get(second)).status == 'running'
    asyncio.run(run())


def test_worker_retains_registry_denial_and_waits_for_evidence(settings,monkeypatch):
    from tests.test_registry_integration import service,approved
    from registry.execution import ApprovedApiExecutor
    async def run():
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            svc,owner,admin=service(runtime)
            registration=await approved(svc,owner,admin)
            await svc.transition(admin,registration.api_id,'revoked')
            # Any transport access would fail the test: revoked APIs must never reach it.
            transport_calls=[]
            def unexpected(*args,**kwargs):
                transport_calls.append(True)
                raise AssertionError('Revoked endpoint reached transport')
            monkeypatch.setattr('registry.execution.send_pinned_request',unexpected)
            identifier,_=await create(runtime,owner)
            queue=JobQueue(runtime.database)
            await queue.submit(identifier,principal=owner,correlation_id='test')
            executor=ApprovedApiExecutor(svc,principal=owner)
            agents,_=fixtures(sufficient=False)
            worker=Worker(runtime,queue=queue,workflow_options={'agents':agents,
                'evidence_tools':{'registered_health':executor.evidence_tool(registration.api_id,'health')}})
            await worker.run_once()
            assert (await job(runtime,identifier)).status == 'awaiting_evidence'
            assert transport_calls == []
            state=(await InvestigationRepository(runtime.database).get(identifier)).state
            assert state['final_report'] is None
            assert state['pending_user_questions']
    asyncio.run(run())


def test_live_sse_and_dynamic_interrupt_are_bounded(settings):
    from langgraph.types import interrupt
    async def run():
        actor=Principal(uuid4())
        class Auth:
            async def authenticate(self,token): return actor if token=='owner' else None
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            identifier,_=await create(runtime,actor)
            queue=JobQueue(runtime.database)
            await queue.submit(identifier,principal=actor,correlation_id='test')
            agents,_=fixtures()
            def need_input(_): return interrupt({'question':'Provide sanitized evidence','password':'never-stream-this'})
            agents['classifier']=need_input
            app=create_app(runtime=runtime,authenticator=Auth(),durable_queue=True)
            async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                streaming=asyncio.create_task(client.get(f'/api/v1/investigations/{identifier}/events',headers={'Authorization':'Bearer owner'}))
                await asyncio.sleep(0.1)
                await Worker(runtime,queue=queue,workflow_options={'agents':agents}).run_once()
                response=await asyncio.wait_for(streaming,3)
                assert ': heartbeat' in response.text
                assert 'event: interrupted' in response.text
                assert 'never-stream-this' not in response.text
                assert (await job(runtime,identifier)).status == 'interrupted'
                assert await Worker(runtime,queue=queue,workflow_options={'agents':agents}).run_once() is False
    asyncio.run(run())


def test_unsafe_legacy_checkpoint_recovery_keeps_user_projections_safe(settings):
    async def run():
        from tools.output_safety import SAFE_REQUEST
        actor=Principal(uuid4());agents,calls=fixtures()
        unsafe='Evidence of the client authentication credentials is required'
        original=agents['root_cause_analyst']
        def checked_analyst(state):
            assert state['pending_user_questions']==[SAFE_REQUEST]
            return original(state)
        agents['root_cause_analyst']=checked_analyst
        async with open_persistence(settings,setup_checkpoints=True) as runtime:
            identifier,state=await create(runtime,actor)
            queue=JobQueue(runtime.database)
            await queue.submit(identifier,principal=actor,correlation_id='rc-legacy')
            worker=Worker(runtime,queue=queue,workflow_options={'agents':agents,'interrupt_before':['root_cause_analyst']})
            claim=await queue.claim();config={'configurable':{'thread_id':state['thread_id']}}
            await worker.graph.ainvoke(state,config)
            # Emulate an old raw checkpoint without rewriting any historical fixture.
            await worker.graph.compiled.aupdate_state(config,{'pending_user_questions':[unsafe]})
            await expire(runtime,claim)
        async with open_persistence(settings) as runtime:
            worker=Worker(runtime,workflow_options={'agents':agents})
            assert await worker.run_once()
            projected=await InvestigationRepository(runtime.database).get(identifier)
            assert projected.state['pending_user_questions']==[SAFE_REQUEST]
            assert calls['classifier']==calls['investigator']==1
            assert calls['analyst']==1
    asyncio.run(run())
