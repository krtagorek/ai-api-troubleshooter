"""Immutable governance contracts. Credential fields are intentionally absent."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from registry.security import origin, path
from tools.redaction import redact

Status = Literal["pending", "verified", "approved", "suspended", "revoked"]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always")


class Operation(Contract):
    operation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    method: Literal["GET", "HEAD"] = "GET"
    path: str
    demo_auth: bool = False

    @field_validator("path")
    @classmethod
    def exact_path(cls, value):
        result = path(value)
        if redact(result) != result:
            raise ValueError("Credentials and personal identifiers are not allowed in approved paths")
        return result


class ApiPolicy(Contract):
    origin: str
    kind: Literal["external", "development_mock"] = "external"
    operations: tuple[Operation, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def valid_policy(self):
        canonical = origin(self.origin, development=self.kind == "development_mock")
        if redact(self.origin) != self.origin:
            raise ValueError("Credentials are not allowed in an origin")
        if self.origin != canonical:
            raise ValueError("Origin must already be canonical (no trailing slash, case changes, or default port)")
        if len({op.operation_id for op in self.operations}) != len(self.operations):
            raise ValueError("Operation IDs must be unique")
        if self.kind == "external" and any(op.demo_auth for op in self.operations):
            raise ValueError("Demo authentication is limited to the development mock")
        return self


class ApiRegistration(Contract):
    api_id: UUID
    owner_id: UUID
    policy: ApiPolicy
    pinned_ips: tuple[str, ...]
    status: Status
    revision: int = Field(ge=1)
    verified_revision: int | None
    approved_revision: int | None
    approval_digest: str | None
    challenge_expires_at: datetime


class RegistrationChallenge(Contract):
    registration: ApiRegistration
    challenge: SecretStr


class AuthorizationDecision(Contract):
    allowed: bool
    reason: Literal["approved", "unapproved", "policy_changed", "operation_denied", "development_disabled", "invalid_destination", "actor_denied"]
    operation: Operation | None = None
