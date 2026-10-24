"""Identity comes from trusted application authentication, never LLM output."""

import os
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    subject_id: UUID

    def __post_init__(self):
        if not isinstance(self.subject_id, UUID):
            raise ValueError("An authenticated opaque UUID subject is required")


@dataclass(frozen=True)
class AdminAuthorization:
    admin_ids: frozenset[UUID] = frozenset()

    def is_admin(self, principal):
        return isinstance(principal, Principal) and principal.subject_id in self.admin_ids

    @classmethod
    def from_env(cls):
        try:
            return cls(frozenset(UUID(value.strip()) for value in os.getenv("REGISTRY_ADMIN_IDS", "").split(",") if value.strip()))
        except Exception:
            raise ValueError("REGISTRY_ADMIN_IDS must contain opaque UUID subjects") from None
