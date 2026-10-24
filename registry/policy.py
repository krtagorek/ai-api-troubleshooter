"""Deterministic authorization: status + exact approved revision + pinned policy."""

import hashlib
import json

from registry.models import ApiRegistration, AuthorizationDecision
from registry.security import destination_ip


def policy_digest(policy, pins, revision):
    value = {"policy": policy.model_dump(mode="json"), "pinned_ips": sorted(pins), "revision": revision}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def authorize(registration: ApiRegistration, operation_id: str, *, subject_id=None, admin_ids=frozenset(), environment="production"):
    if subject_id is None or (subject_id != registration.owner_id and subject_id not in admin_ids):
        return AuthorizationDecision(allowed=False, reason="actor_denied")
    if registration.status != "approved":
        return AuthorizationDecision(allowed=False, reason="unapproved")
    if (registration.approved_revision != registration.revision
        or registration.verified_revision != registration.revision
        or registration.approval_digest != policy_digest(registration.policy, registration.pinned_ips, registration.revision)):
        return AuthorizationDecision(allowed=False, reason="policy_changed")
    development = registration.policy.kind == "development_mock"
    if development and environment != "development":
        return AuthorizationDecision(allowed=False, reason="development_disabled")
    try:
        if not registration.pinned_ips:
            raise ValueError
        for ip in registration.pinned_ips:
            destination_ip(ip, development=development)
    except Exception:
        return AuthorizationDecision(allowed=False, reason="invalid_destination")
    operation = next((op for op in registration.policy.operations if op.operation_id == operation_id), None)
    if operation is None:
        return AuthorizationDecision(allowed=False, reason="operation_denied")
    return AuthorizationDecision(allowed=True, reason="approved", operation=operation)
