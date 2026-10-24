"""Parse bounded log input as untrusted observations; never execute content."""

import json
from typing import Literal

from pydantic import Field, JsonValue

from models.incident import DomainModel
from tools.redaction import redact


class LogParseResult(DomainModel):
    status: Literal["ok", "partial", "error"]
    entries: list[dict[str, JsonValue]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    untrusted: Literal[True] = True


def parse_logs(raw: str | list, *, max_entries=1000, max_bytes=262144) -> LogParseResult:
    try:
        if len(json.dumps(raw).encode()) > max_bytes:
            return LogParseResult(status="error", issues=["Log input exceeds size limit"])
        entries = raw.splitlines() if isinstance(raw, str) else raw
        if not isinstance(entries, list) or len(entries) > max_entries:
            return LogParseResult(status="error", issues=["Log input exceeds entry limit"])
        parsed, issues = [], []
        for entry in entries:
            if isinstance(entry, str):
                try:
                    entry = json.loads(entry)
                except ValueError:
                    entry = {"message": entry, "format": "text"}
            if not isinstance(entry, dict):
                issues.append("Non-object log entry omitted")
                continue
            parsed.append(redact(entry))
        return LogParseResult(status="partial" if issues else "ok", entries=parsed, issues=issues)
    except Exception:
        return LogParseResult(status="error", issues=["Log input could not be parsed safely"])
