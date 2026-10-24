"""Fresh database authorization for every bounded, read-only API request."""

import asyncio
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select

from persistence.tables import ApiRegistryRow
from registry.authorization import Principal
from registry.policy import authorize
from registry.security import destination_ip
from registry.service import audit, snapshot
from tools.api_client import ApiResult, DestinationBlocked, send_pinned_request
from tools.redaction import install_redaction_filters
from tools.validation_tools import validation_from_result


@dataclass(frozen=True)
class ApprovedTarget:
    base_url: str
    allowed_ips: tuple[str, ...]

    @property
    def host(self):
        return urlsplit(self.base_url).hostname

    @property
    def port(self):
        parsed = urlsplit(self.base_url)
        return parsed.port or 443


class ApprovedApiExecutor:
    def __init__(self, service, *, principal: Principal):
        service._principal(principal)
        self.service, self.principal = service, principal
        self._slots = asyncio.Semaphore(4)
        install_redaction_filters()

    async def aexecute(self, api_id, operation_id) -> ApiResult:
        acquired = False
        try:
            api_id = UUID(str(api_id))
            await asyncio.wait_for(self._slots.acquire(), timeout=1)
            acquired = True
            async with self.service.database.transaction() as session:
                # Share-lock the current policy through the bounded request.
                # Policy updates/revocations serialize after already-authorized work.
                row = await session.scalar(select(ApiRegistryRow).where(
                    ApiRegistryRow.api_id == api_id).with_for_update(read=True))
                if row is None:
                    audit(session, self.principal, None, "request", outcome="denied", reason="not_registered")
                    return ApiResult(ok=False, error="blocked")
                registration = snapshot(row)
                decision = authorize(registration, operation_id, subject_id=self.principal.subject_id,
                    admin_ids=self.service.admins.admin_ids, environment=self.service.environment)
                if not decision.allowed:
                    audit(session, self.principal, row, "request", outcome="denied", reason=decision.reason)
                    return ApiResult(ok=False, error="blocked")
                if registration.policy.kind == "development_mock" and registration.policy.origin != self.service.development_mock_origin:
                    audit(session, self.principal, row, "request", outcome="denied", reason="mock_origin_changed")
                    return ApiResult(ok=False, error="blocked")
                def checked_ip(value):
                    try:
                        return destination_ip(value, development=registration.policy.kind == "development_mock")
                    except Exception:
                        raise DestinationBlocked("Destination is not permitted") from None
                operation = decision.operation
                task = asyncio.create_task(asyncio.to_thread(partial(send_pinned_request,
                    ApprovedTarget(registration.policy.origin, registration.pinned_ips), operation.path,
                    method=operation.method, demo_auth=operation.demo_auth, resolver=self.service.resolver,
                    ip_validator=checked_ip)))
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Do not release the policy lock while a socket request continues.
                    await task
                    raise
                audit(session, self.principal, row, "request", outcome="allowed" if result.error is None else "denied",
                      reason="executed" if result.error is None else "execution_blocked")
                return result
        except Exception:
            # Missing enforcement, invalid data, and DB failures never fall back
            # to a cached grant or the old mock client's static configuration.
            return ApiResult(ok=False, error="blocked")
        finally:
            if acquired:
                self._slots.release()

    def evidence_tool(self, api_id, operation_id):
        async def collect(_incident):
            result = await self.aexecute(api_id, operation_id)
            if result.error or result.status_code is None:
                return []
            return [{"message": "Untrusted observation from registry-approved API",
                     "tool": "approved_api", "result": result.model_dump(mode="json")}]
        return collect

    def validation_tool(self, api_id, operation_id, response_schema):
        async def validate(_state):
            result = await self.aexecute(api_id, operation_id)
            return validation_from_result(result, response_schema)
        return validate
