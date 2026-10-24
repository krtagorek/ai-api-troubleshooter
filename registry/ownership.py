"""Plug in a trusted DNS-TXT/well-known proof adapter; no automatic external trust."""

from typing import Protocol


class OwnershipVerifier(Protocol):
    async def verify(self, canonical_origin: str, challenge: str) -> bool:
        """Check the published challenge on the EXACT registered origin.

        Implementations must apply SSRF-safe DNS/HTTP enforcement themselves.
        They must not trust owner-submitted claims, redirects, or LLM decisions.
        """
        ...
