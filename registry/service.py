"""Transactionally governed registration; no LLMs and no credential storage."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import os
from uuid import UUID, uuid4

from sqlalchemy import select

from persistence.tables import ApiRegistryRow, ApiRegistryEventRow
from registry.authorization import Principal, AdminAuthorization
from registry.models import ApiPolicy, ApiRegistration, RegistrationChallenge, Operation
from registry.policy import policy_digest
from registry.security import destination_ip, origin
from tools.api_client import resolve_dns
from urllib.parse import urlsplit


class RegistryDenied(PermissionError):
    pass


class RegistryTransitionError(ValueError):
    pass


def snapshot(row):
    return ApiRegistration(api_id=row.api_id, owner_id=row.owner_id,
        policy=ApiPolicy.model_validate(row.policy), pinned_ips=tuple(row.pinned_ips),
        status=row.status, revision=row.revision, verified_revision=row.verified_revision,
        approved_revision=row.approved_revision, approval_digest=row.approval_digest,
        challenge_expires_at=row.challenge_expires_at)


def audit(session, actor, row, action, *, outcome="allowed", reason="ok"):
    if (action not in {"register_mock", "registered", "read_history", "update_policy", "policy_changed",
                       "verification", "verified", "transition", "approved", "suspended", "revoked", "request"}
        or outcome not in {"allowed", "denied"}
        or reason not in {"ok", "admin_required", "owner_required", "revoked_terminal", "ownership_unverified",
                          "invalid_transition", "not_registered", "unapproved", "policy_changed", "operation_denied",
                          "development_disabled", "invalid_destination", "actor_denied", "mock_origin_changed",
                          "executed", "execution_blocked"}):
        raise ValueError("Registry audit events require controlled action and reason codes")
    session.add(ApiRegistryEventRow(api_id=row.api_id if row else None,
        actor_id=actor.subject_id, action=action, outcome=outcome, reason=reason,
        revision=row.revision if row else None))


class RegistryService:
    def __init__(self, database, *, admins, ownership_verifier=None, resolver=resolve_dns,
                 environment="production", development_mock_origin="http://127.0.0.1:8001"):
        if environment not in {"production", "development"}:
            raise ValueError("Unknown registry environment")
        self.database, self.admins = database, admins
        self.ownership_verifier, self.resolver = ownership_verifier, resolver
        self.environment, self.development_mock_origin = environment, development_mock_origin
        if origin(development_mock_origin, development=True) != development_mock_origin:
            raise ValueError("Development mock origin must be canonical")

    @classmethod
    def from_env(cls, database, *, ownership_verifier=None):
        return cls(database, admins=AdminAuthorization.from_env(), ownership_verifier=ownership_verifier,
            environment=os.getenv("APP_ENV", "production"),
            development_mock_origin=os.getenv("REGISTRY_DEVELOPMENT_MOCK_ORIGIN", "http://127.0.0.1:8001"))

    def _principal(self, actor):
        if not isinstance(actor, Principal):
            raise RegistryDenied("Authenticated application principal required")

    async def _admin(self, actor, api_id, action):
        self._principal(actor)
        if self.admins.is_admin(actor):
            return
        async with self.database.transaction() as session:
            row = await session.get(ApiRegistryRow, api_id) if api_id else None
            audit(session, actor, row, action, outcome="denied", reason="admin_required")
        raise RegistryDenied("Administrator authorization required")

    def _check_kind(self, policy):
        if policy.kind == "development_mock" and (
            self.environment != "development" or policy.origin != self.development_mock_origin
        ):
            raise RegistryDenied("Development mock registration is disabled or origin is not configured")

    async def _pins(self, policy):
        parsed = urlsplit(policy.origin)
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self.resolver, parsed.hostname,
                parsed.port or 443), timeout=2)
            if not result or len(result) > 16:
                raise ValueError
            return sorted({destination_ip(ip, development=policy.kind == "development_mock") for ip in result})
        except Exception:
            raise RegistryDenied("Destination enforcement is unavailable or destination is forbidden") from None

    @staticmethod
    def _challenge():
        value = secrets.token_urlsafe(32)
        return value, hashlib.sha256(value.encode()).hexdigest(), datetime.now(timezone.utc) + timedelta(minutes=15)

    async def register(self, actor, policy):
        self._principal(actor)
        policy = ApiPolicy.model_validate(policy)
        self._check_kind(policy)
        if policy.kind == "development_mock":
            await self._admin(actor, None, "register_mock")
        pins = await self._pins(policy)
        challenge, digest, expiry = self._challenge()
        async with self.database.transaction() as session:
            row = ApiRegistryRow(api_id=uuid4(), owner_id=actor.subject_id, policy=policy.model_dump(mode="json"),
                pinned_ips=pins, status="pending", revision=1, challenge_digest=digest, challenge_expires_at=expiry)
            session.add(row)
            await session.flush()
            audit(session, actor, row, "registered")
            registration = snapshot(row)
        return RegistrationChallenge(registration=registration, challenge=challenge)

    async def register_development_mock(self, actor):
        return await self.register(actor, ApiPolicy(origin=self.development_mock_origin,
            kind="development_mock", operations=(Operation(operation_id="health", path="/health"),)))

    async def get(self, actor, api_id):
        self._principal(actor)
        async with self.database.transaction() as session:
            row = await session.get(ApiRegistryRow, UUID(str(api_id)))
            if row is None or (row.owner_id != actor.subject_id and not self.admins.is_admin(actor)):
                raise RegistryDenied("Registration is unavailable to this principal")
            return snapshot(row)

    async def history(self, actor, api_id):
        await self._admin(actor, UUID(str(api_id)), "read_history")
        async with self.database.transaction() as session:
            rows = await session.scalars(select(ApiRegistryEventRow).where(
                ApiRegistryEventRow.api_id == UUID(str(api_id))).order_by(ApiRegistryEventRow.created_at, ApiRegistryEventRow.event_id))
            return [{"action": item.action, "outcome": item.outcome, "reason": item.reason,
                     "revision": item.revision, "actor_id": str(item.actor_id),
                     "event_id": str(item.event_id), "timestamp": item.created_at.isoformat()} for item in rows]

    async def list_registrations(self, actor, *, limit=50):
        self._principal(actor)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")
        query = select(ApiRegistryRow).order_by(ApiRegistryRow.created_at.desc(), ApiRegistryRow.api_id).limit(limit)
        if not self.admins.is_admin(actor):
            query = query.where(ApiRegistryRow.owner_id == actor.subject_id)
        async with self.database.transaction() as session:
            return [snapshot(row) for row in await session.scalars(query)]

    async def update_policy(self, actor, api_id, policy):
        self._principal(actor)
        policy = ApiPolicy.model_validate(policy)
        self._check_kind(policy)
        if policy.kind == "development_mock":
            await self._admin(actor, UUID(str(api_id)), "update_policy")
        challenge, digest, expiry = self._challenge()
        denied = False
        async with self.database.transaction() as session:
            row = await session.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id == UUID(str(api_id))).with_for_update())
            if row is None:
                raise RegistryDenied("Registration not found")
            if row.owner_id != actor.subject_id and not self.admins.is_admin(actor):
                audit(session, actor, row, "update_policy", outcome="denied", reason="owner_required")
                denied = True
            elif row.status == "revoked":
                audit(session, actor, row, "update_policy", outcome="denied", reason="revoked_terminal")
                denied = True
            else:
                pins = await self._pins(policy)
                row.policy, row.pinned_ips = policy.model_dump(mode="json"), pins
                row.revision += 1
                row.status, row.verified_revision, row.approved_revision, row.approval_digest = "pending", None, None, None
                row.challenge_digest, row.challenge_expires_at = digest, expiry
                audit(session, actor, row, "policy_changed")
                result = snapshot(row)
        if denied:
            raise RegistryDenied("Policy change is not permitted")
        return RegistrationChallenge(registration=result, challenge=challenge)

    async def verify(self, actor, api_id, challenge):
        self._principal(actor)
        denied = False
        async with self.database.transaction() as session:
            row = await session.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id == UUID(str(api_id))).with_for_update())
            if row is None:
                raise RegistryDenied("Registration not found")
            valid = (row.owner_id == actor.subject_id or self.admins.is_admin(actor)) and row.status == "pending"
            value = challenge.get_secret_value() if hasattr(challenge, "get_secret_value") else challenge
            valid = valid and isinstance(value, str) and len(value) <= 128 and row.challenge_expires_at > datetime.now(timezone.utc)
            valid = valid and hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), row.challenge_digest)
            policy = ApiPolicy.model_validate(row.policy)
            if valid:
                try:
                    self._check_kind(policy)
                    valid = await self._pins(policy) == row.pinned_ips
                    if policy.kind == "external":
                        valid = valid and self.ownership_verifier is not None and (
                            await asyncio.wait_for(self.ownership_verifier.verify(policy.origin, value), 5)) is True
                except Exception:
                    valid = False
            if not valid:
                audit(session, actor, row, "verification", outcome="denied", reason="ownership_unverified")
                denied = True
            else:
                row.status, row.verified_revision = "verified", row.revision
                audit(session, actor, row, "verified")
                result = snapshot(row)
        if denied:
            raise RegistryDenied("Ownership verification failed or is unavailable; refresh policy for a new challenge")
        return result

    async def transition(self, actor, api_id, target):
        api_id = UUID(str(api_id))
        await self._admin(actor, api_id, "transition")
        if not isinstance(target, str) or target not in {"approved", "suspended", "revoked"}:
            raise RegistryTransitionError("Unknown approval transition")
        denied = False
        async with self.database.transaction() as session:
            row = await session.scalar(select(ApiRegistryRow).where(ApiRegistryRow.api_id == api_id).with_for_update())
            if row is None:
                raise RegistryDenied("Registration not found")
            valid = (row.status, target) in {("verified", "approved"), ("approved", "suspended"),
                                           ("approved", "revoked"), ("suspended", "revoked")}
            if valid and target == "approved":
                try:
                    policy = ApiPolicy.model_validate(row.policy)
                    self._check_kind(policy)
                    valid = row.verified_revision == row.revision and row.challenge_expires_at > datetime.now(timezone.utc)
                    valid = valid and await self._pins(policy) == row.pinned_ips
                except Exception:
                    valid = False
            if not valid:
                audit(session, actor, row, "transition", outcome="denied", reason="invalid_transition")
                denied = True
            else:
                row.status = target
                if target == "approved":
                    row.approved_revision = row.revision
                    row.approval_digest = policy_digest(policy, row.pinned_ips, row.revision)
                else:
                    row.approved_revision, row.approval_digest = None, None
                audit(session, actor, row, target)
                result = snapshot(row)
        if denied:
            raise RegistryTransitionError("Transition denied; verify the current policy before approval")
        return result
