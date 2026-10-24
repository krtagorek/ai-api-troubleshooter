"""Inference-safe incident models; independent of benchmark answer schemas."""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from tools.redaction import REDACTED, LABELS, SENSITIVE, redact as sanitize


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True,
                              revalidate_instances="always", hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def sanitize_input(cls, value):
        return sanitize(value)


class Incident(DomainModel):
    incident_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    status_code: int = Field(ge=100, le=599, strict=True)
    request_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    response_body: JsonValue = None
    logs: list[dict[str, JsonValue]] = Field(default_factory=list)
    expected_behavior: str = Field(min_length=1)
    actual_behavior: str = Field(min_length=1)
    severity: Literal["unknown", "low", "medium", "high", "critical"] = "unknown"
    evidence_quality: Literal["unknown", "complete", "missing", "ambiguous"] = "unknown"


def incident_from_record(record: Mapping | BaseModel) -> Incident:
    """Map existing labeled or inference JSONL without importing answer fields.

    Unknown fields are intentionally excluded at this explicit dataset boundary.
    Existing dataset files and their schema are left unchanged.
    """
    raw = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    selected = {key: raw[key] for key in Incident.model_fields if key in raw}
    metadata = selected.get("request_metadata", {})
    selected.setdefault("evidence_quality", metadata.get("evidence_quality", "unknown"))
    return Incident.model_validate(selected)
