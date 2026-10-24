"""Recoverable async investigations: checkpoints are execution source of truth."""

from uuid import uuid4
from langgraph.types import Command

from graph.state import initialize_state, validate_state
from graph.workflow import build_workflow
from persistence.repository import InvestigationRepository
from tools.redaction import redact

_MISSING = object()


class InvestigationExecutionError(RuntimeError):
    def __init__(self, investigation_id):
        self.investigation_id = investigation_id
        super().__init__(f"Investigation {investigation_id} stopped; resume it from its checkpoint")


class InvestigationService:
    def __init__(self, runtime, **workflow_options):
        self.runtime = runtime
        self.workflow_options=workflow_options
        self.repository = InvestigationRepository(runtime.database)
        self.graph = build_workflow(checkpointer=runtime.saver, **workflow_options)

    async def start(self, record, *, max_retries=2):
        row = await self.create(record, max_retries=max_retries)
        await self._execute(row.investigation_id, row.state)
        return await self.repository.get(row.investigation_id)

    async def create(self, record, *, max_retries=2):
        state = initialize_state(record, str(uuid4()), max_retries=max_retries)
        identifier = await self.repository.create(state)
        return await self.repository.get(identifier)

    async def resume(self, identifier, *, response=_MISSING):
        await self._execute(identifier, None, response=response)
        return await self.repository.get(identifier)

    async def _project(self, identifier, config, event):
        snapshot = await self.graph.aget_state(config)
        if not snapshot.values:
            return None
        state = validate_state(snapshot.values)
        status = "interrupted" if snapshot.next else state["status"]
        from diagnostic_contracts.integration import publication_for
        state,publication=await publication_for(state,self.workflow_options)
        if state['stop_reason']=='report_publication_denied':status=state['status']
        await self.repository.save(identifier, state, status=status, event_type=event,publication=publication)
        return snapshot

    async def _execute(self, identifier, initial, *, response=_MISSING):
        row = await self.repository.get(identifier)
        config = {"configurable": {"thread_id": row.thread_id}}
        async with self.runtime.thread_lock(row.thread_id):
            # If a process died after a checkpoint commit but before the app
            # projection, terminal recovery repairs the projection without reruns.
            if initial is None:
                snapshot = await self.graph.aget_state(config)
                if not snapshot.values:
                    if response is not _MISSING:
                        raise ValueError("No interrupted execution is available for a response")
                    initial = row.state  # Crash before the first input checkpoint.
                elif not snapshot.next:
                    if response is not _MISSING:
                        raise ValueError("Completed executions cannot accept resume responses")
                    await self._project(identifier, config, "recovered")
                    return
                else:
                    needs_response = any(task.interrupts for task in snapshot.tasks)
                    if needs_response and response is _MISSING:
                        raise ValueError("A resume response is required for this interruption")
                    if response is not _MISSING:
                        if not needs_response:
                            raise ValueError("This interruption does not accept a response")
                        initial = Command(resume=redact(response))
            await self.repository.save(identifier, row.state, status="running", event_type="execution_started")
            try:
                await self.graph.ainvoke(initial, config)
                await self._project(identifier, config, "execution_stopped")
            except Exception:
                # Never persist arbitrary exception messages. The saver also
                # suppresses error-channel contents before writing pending tasks.
                try:
                    snapshot = await self._project(identifier, config, "execution_failed")
                    if snapshot is None:
                        await self.repository.save(identifier, row.state, status="interrupted", event_type="execution_failed")
                except Exception:
                    pass  # A DB outage may prevent projection; durable checkpoints remain.
                raise InvestigationExecutionError(identifier) from None
