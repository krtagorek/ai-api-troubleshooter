"""Central redaction boundary for untrusted data, not an instruction filter."""

import json
import logging
import re
from collections.abc import Mapping

from pydantic import BaseModel

REDACTED = "[REDACTED]"
LABELS = {"root_cause", "expected_resolution"}
SENSITIVE = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api[_-]?key|credential|private[_-]?key|"
    r"(?:^|_)(?:email|phone|mobile|ssn|social_security|first_name|last_name|full_name|"
    r"date_of_birth|dob|address|ip_address|client_ip|customer_name|user_name)(?:$|_)", re.I)


def redact(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): REDACTED if SENSITIVE.search(str(key)) else redact(item)
                for key, item in value.items() if str(key) not in LABELS}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    # Preserve JSON structure: redact parsed values, never run header/assignment
    # regexes across adjacent serialized keys and values.
    if value.lstrip().startswith(("{", "[")):
        try:
            structured = json.loads(value)
        except (ValueError, RecursionError):
            pass  # Ordinary free text retains the existing protection below.
        else:
            if isinstance(structured, (dict, list)):
                return json.dumps(redact(structured), ensure_ascii=False, allow_nan=False)
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                   REDACTED, value, flags=re.S)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[\w.+/=-]+", REDACTED, value)
    value = re.sub(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]+", REDACTED, value)
    value = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", REDACTED, value)
    value = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", REDACTED, value)
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    # Handles ordinary assignments, JSON strings, HTTP headers and repr(header tuples).
    names = r"password|passwd|secret|client_secret|access_token|refresh_token|token|api[_-]?key|authorization|(?:set-)?cookie|credential|email|phone|ssn|full_name|first_name|last_name|date_of_birth"
    def replace_assignment(match):
        content = match.group(2)
        replacement = content[0] + REDACTED + content[0] if content[:1] in ("\"", "'") else REDACTED
        return match.group(1) + replacement
    value = re.sub(
        rf"(?i)(\b(?:{names})[\"']?\s*[=:]\s*)(\[REDACTED\]|\"[^\"]*\"|'[^']*'|[^\s&,;}}\]]+)",
        replace_assignment, value)
    value = re.sub(rf"(?i)(b?[\"'](?:{names})[\"']\s*,\s*)b?[\"'][^\"']*[\"']",
                   lambda match: match.group(1) + repr(REDACTED), value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", REDACTED, value)
    value = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", REDACTED, value)
    value = re.sub(r"(?<!\w)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)", REDACTED, value)
    value = re.sub(r"\b\d{10}\b", REDACTED, value)
    return value


def redact_message_content(content):
    """Fail closed for malformed evidence envelopes; serialize only after redaction."""
    marker = "UNTRUSTED_EVIDENCE_JSON\n"
    if isinstance(content, str) and content.startswith(marker):
        payload = json.loads(content[len(marker):])
        if not isinstance(payload, dict):
            raise ValueError("Evidence envelope must be an object")
        return marker + json.dumps(redact(payload), ensure_ascii=False, allow_nan=False)
    return redact(content)


class RedactingFilter(logging.Filter):
    def filter(self, record):
        # HTTPCore debug records may include arbitrary response header tuples;
        # suppress those details rather than trying to guess every secret name.
        record.msg = "HTTP transport diagnostic details omitted" if record.name.startswith("httpcore.") else redact(record.getMessage())
        record.args = ()
        # Exceptions may contain unsanitized request bodies, URLs, or headers.
        if record.exc_info or record.exc_text:
            record.exc_info = None
            record.exc_text = None
            record.msg += " [exception details omitted]"
        record.stack_info = None
        for key in list(record.__dict__):
            if key not in {"msg", "args", "exc_info", "exc_text", "stack_info"}:
                if SENSITIVE.search(key):
                    record.__dict__[key] = REDACTED
                elif isinstance(record.__dict__[key], (dict, list)):
                    record.__dict__[key] = redact(record.__dict__[key])
        return True


def install_redaction_filters():
    """Protect application and HTTPX/HTTPCore records before handler emission."""
    names = ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
             "httpcore.proxy", "mock_api.requests", "diagnostic_tools")
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(RedactingFilter())
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def log_event(logger, event, **fields):
    logger.info(json.dumps(redact({"event": event, **fields}), sort_keys=True))
