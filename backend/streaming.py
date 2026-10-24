"""Authenticated persisted progress replay; no database transaction spans a yield."""
import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import StreamingResponse
from backend.auth import identity
from worker.queue import JobQueue, TERMINAL, JobConflict

router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"])


def queue(request):
    return JobQueue(request.app.state.runtime.database, admins=request.app.state.admins)


@router.post("/{investigation_id}/cancel")
async def cancel(investigation_id: UUID, request: Request, actor=Depends(identity)):
    return await queue(request).cancel(investigation_id, principal=actor)


@router.get("/{investigation_id}/events", responses={200: {"content": {"text/event-stream": {}}}})
async def events(investigation_id: UUID, request: Request, actor=Depends(identity),
                 last_event_id: str | None = Header(None, alias="Last-Event-ID", max_length=64)):
    after, expected = 0, None
    if last_event_id is not None:
        try:
            value, sequence = last_event_id.split(":")
            expected, after = UUID(value), int(sequence)
            if str(after) != sequence or after < 0: raise ValueError
        except Exception:
            raise HTTPException(400, "Invalid Last-Event-ID") from None
    source = queue(request)
    try:
        initial = await source.events(investigation_id, principal=actor, after=after, expected_job=expected)
    except JobConflict:
        raise HTTPException(400, "Invalid Last-Event-ID") from None

    async def stream():
        nonlocal after
        batch = initial
        while True:
            job_id, status, highest, records = batch
            for record in records:
                if await request.is_disconnected(): return
                after = record["sequence"]
                payload = json.dumps({"data":record["data"],"timestamp":record["timestamp"]}, separators=(",", ":"))
                yield f'id: {job_id}:{after}\nevent: {record["event"]}\ndata: {payload}\n\n'
            if status in TERMINAL and after >= highest: return
            if await request.is_disconnected(): return
            if after >= highest:
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
            try:
                batch = await source.events(investigation_id, principal=actor, after=after, expected_job=job_id)
            except Exception:
                # No error text or synthetic event IDs. Client reconnects from last durable ID.
                return
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-store", "X-Accel-Buffering":"no"})
