"""Separate async worker. Checkpoints survive cancellation, retries and process loss."""
import asyncio
from contextlib import suppress

from graph.workflow import build_workflow
from graph.state import validate_state
from persistence.repository import InvestigationRepository
from worker.queue import JobQueue, LeaseLost
from worker.checkpoints import LeasedPostgresSaver


class Worker:
    def __init__(self, runtime, *, queue=None, workflow_options=None, execution_timeout=300):
        if not 1 <= execution_timeout <= 1800: raise ValueError("Invalid execution timeout")
        self.runtime = runtime
        self.queue = queue or JobQueue(runtime.database)
        self.repository = InvestigationRepository(runtime.database)
        self.workflow_options = workflow_options or {}
        self.graph = build_workflow(checkpointer=runtime.saver, **self.workflow_options)
        self.execution_timeout = execution_timeout

    async def _finish(self,claim,*,state=None,outcome=None):
        publication=None
        if state:
            from diagnostic_contracts.integration import publication_for
            state,publication=await publication_for(validate_state(state),self.workflow_options)
            if state['stop_reason']=='report_publication_denied':outcome=None
        await self.queue.finish(claim,state=state,outcome=outcome,publication=publication)

    async def _execute(self, claim):
        graph = build_workflow(checkpointer=LeasedPostgresSaver(
            self.runtime.checkpoint_pool, self.queue, claim), **self.workflow_options)
        row = await self.repository.get(claim.investigation_id)
        config = {"configurable": {"thread_id": row.thread_id}, "recursion_limit": 100}
        async with self.runtime.thread_lock(row.thread_id):
            if await self.queue.heartbeat(claim):
                await self.queue.finish(claim, outcome="cancelled")
                return
            snapshot = await graph.aget_state(config)
            if snapshot.values and not snapshot.next:
                await self._finish(claim, state=snapshot.values)
                return
            if snapshot.values and any(task.interrupts for task in snapshot.tasks):
                await self._finish(claim, state=snapshot.values, outcome="interrupted")
                return
            initial = None if snapshot.values else row.state
            # Sync durability means progress is emitted after the node checkpoint commits.
            async for update in graph.compiled.astream(graph._input(initial, config), config,
                    stream_mode="updates", durability="sync"):
                for node in update:
                    if node == "__interrupt__": continue
                    if not await self.queue.progress(claim, node):
                        await self.queue.finish(claim, outcome="cancelled")
                        return
            snapshot = await graph.aget_state(config)
            await self._finish(claim, state=validate_state(snapshot.values),
                                    outcome="interrupted" if snapshot.next else None)

    async def _monitor(self, claim):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.execution_timeout
        interval = self.queue.lease_seconds / 3
        while loop.time() < deadline:
            if await self.queue.heartbeat(claim): return "cancelled"
            await asyncio.sleep(min(interval, max(0, deadline-loop.time())))
        return "timeout"

    async def run_once(self):
        claim = await self.queue.claim()
        if claim is None: return False
        task = asyncio.create_task(self._execute(claim))
        monitor = asyncio.create_task(self._monitor(claim))
        try:
            done, _ = await asyncio.wait({task, monitor}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
            else:
                reason = await monitor  # DB errors/lease loss abort work, with no stale projection.
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if reason == "cancelled": await self.queue.finish(claim, outcome="cancelled")
                else: await self.queue.retry(claim)
        except LeaseLost:
            pass
        except asyncio.CancelledError:
            raise  # Shutdown leaves the job recoverable after lease expiry.
        except Exception:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with suppress(LeaseLost):
                await self.queue.retry(claim)
        finally:
            task.cancel(); monitor.cancel()
            await asyncio.gather(task, monitor, return_exceptions=True)
        return True

    async def run(self, *, poll_seconds=1):
        while True:
            try:
                if not await self.run_once(): await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                # No raw driver/LLM errors or credentials in process logs.
                print("Worker temporarily unavailable; check PostgreSQL and configuration", flush=True)
                await asyncio.sleep(poll_seconds)
