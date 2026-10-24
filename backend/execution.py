"""Dispatch contract for a separately managed durable runner, never inline work."""
from typing import Protocol, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from registry.authorization import Principal


class ExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    status: Literal["accepted"] = "accepted"


class ExecutionUnavailable(RuntimeError):
    pass


class ExecutionService(Protocol):
    async def submit(self, investigation_id: UUID, *, principal: Principal,
                     correlation_id: str) -> ExecutionReceipt:
        """Return only after a real runner durably accepts the job, idempotently by investigation ID."""
        ...


class UnavailableExecutionService:
    async def submit(self, investigation_id, **kwargs):
        raise ExecutionUnavailable("No investigation runner is configured; execution has not started")
