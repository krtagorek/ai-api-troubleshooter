"""Observed evidence and explicit gaps; absence is never treated as proof."""

from typing import Literal
from types import MappingProxyType
import re
import json
import math

from pydantic import Field

from models.incident import DomainModel

REFERENCE_SOURCES = MappingProxyType({
    "logs": "log", "response_body": "response", "request_metadata": "request",
    "status_code": "response", "expected_behavior": "user", "actual_behavior": "user",
})


def canonical_scalar(value) -> str:
    """Literal strings or exact JSON text for finite scalar values; no containers."""
    if isinstance(value, str):
        return value
    if value is None or type(value) in (bool, int):
        return json.dumps(value, allow_nan=False)
    if type(value) is float and math.isfinite(value):
        return json.dumps(value, allow_nan=False)
    raise ValueError("Evidence reference must resolve to a finite JSON scalar")


def observation_matches(observation: str, value) -> bool:
    text = canonical_scalar(value)
    return bool(observation) and (observation in text if isinstance(value, str) else observation == text)


def reference_source(reference: str | None) -> str:
    """Derive provenance from allowlisted incident roots, never model assertions.

    Existence and observed content must still be checked against the incident.
    Documentation is not an incident source: expected behavior is user evidence.
    """
    if not isinstance(reference, str) or not reference.startswith("/"):
        raise ValueError("Evidence must reference an incident JSON pointer")
    if re.search(r"~(?![01])", reference):
        raise ValueError("Invalid evidence JSON pointer escape")
    root = reference[1:].split("/")[0]
    if root not in REFERENCE_SOURCES:
        raise ValueError("Unsupported evidence source")
    return REFERENCE_SOURCES[root]


class Evidence(DomainModel):
    evidence_id: str = Field(min_length=1)
    source: Literal["request", "response", "log", "documentation", "user", "unknown"] = Field(
        description="Provenance hint only. Trusted code derives source from the reference: logs=log; response_body/status_code=response; request_metadata=request; expected_behavior/actual_behavior=user. Never documentation for incident fields.")
    observation: str = Field(min_length=1, description="Literal evidence only. Strings: exact nonempty substring. Integers, finite floats, booleans and null: entire canonical JSON scalar text (403, 1.0, true, false, null), no labels, whitespace or paraphrase. Reference scalar leaves, never objects or arrays.")
    reliability: float = Field(ge=0, le=1)
    reference: str | None = Field(default=None, description="Incident JSON pointer required for extracted observations; only logs, response_body, request_metadata, status_code, expected_behavior, actual_behavior roots are supported. Must resolve to the quoted value.")
    ambiguous: bool = False


class EvidenceGap(DomainModel):
    description: str = Field(min_length=1)
    required_to_resolve: str = Field(min_length=1)


class EvidenceCollection(DomainModel):
    items: list[Evidence] = Field(default_factory=list)
    missing: list[EvidenceGap] = Field(default_factory=list)
    completeness: Literal["unknown", "complete", "partial", "missing", "conflicting"] = "unknown"
