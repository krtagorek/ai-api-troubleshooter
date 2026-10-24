"""Pure governance and destination enforcement tests; no network calls."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from registry.authorization import Principal, AdminAuthorization
from registry.execution import ApprovedApiExecutor
from registry.models import ApiPolicy, ApiRegistration, Operation
from registry.policy import authorize, policy_digest
from registry.security import destination_ip
from tools.api_client import MockApiClient, MockTarget, MockOperation, PinnedBackend, DestinationBlocked


def policy():
    return ApiPolicy(origin="https://api.example.com", operations=(Operation(operation_id="health", path="/health"),))


def registration(**updates):
    value = policy()
    record = ApiRegistration(api_id=uuid4(), owner_id=uuid4(), policy=value,
        pinned_ips=("93.184.216.34",), status="approved", revision=1, verified_revision=1,
        approved_revision=1, approval_digest=policy_digest(value, ("93.184.216.34",), 1),
        challenge_expires_at=datetime.now(timezone.utc))
    return record.model_copy(update=updates)


@pytest.mark.parametrize("url", [
    "http://api.example.com", "file:///etc/passwd", "gopher://example.com", "https://user:secret@example.com",
    "https://example.com?key=secret", "https://example.com#secret", "https://example.com/path",
    "https://127.0.0.1", "https://169.254.169.254", "https://[::1]", "https://2130706433",
    "https://localhost", "https://api.internal", "https://example.com.", "https://example.com:8443",
    "https://example.com%2f.evil.org", "https://example.com\\evil", " https://example.com",
    "https://example.com\r\nHost: evil.com", "https://example.com/", "https://api.example.com:443",
])
def test_registration_url_validation(url):
    with pytest.raises(ValueError):
        ApiPolicy(origin=url, operations=policy().operations)


@pytest.mark.parametrize("value", ["/../admin", "//evil.com", "/path?token=x", "/%2e%2e", "/api/*",
    "/api/{id}", "/api\\admin", "/api/./health", "/api;secret", "/sk-proj-secret", "/person@example.com"])
def test_exact_paths_and_no_url_credentials(value):
    with pytest.raises(ValueError):
        Operation(operation_id="diagnostic", path=value)


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "CONNECT", "TRACE", "PATCH"])
def test_read_only_methods(method):
    with pytest.raises(ValueError):
        Operation(operation_id="diagnostic", path="/health", method=method)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.100.100.200",
    "0.0.0.0", "224.0.0.1", "::1", "fd00:ec2::254", "::ffff:93.184.216.34", "64:ff9b::a00:1",
    "2002:7f00:1::", "168.63.129.16", "fe80::1%lo0"])
def test_external_destinations_deny_unsafe_ranges(address):
    with pytest.raises(ValueError):
        destination_ip(address)


@pytest.mark.parametrize("status", ["pending", "verified", "suspended", "revoked"])
def test_only_approved_can_execute(status):
    item = registration(status=status)
    assert not authorize(item, "health", subject_id=item.owner_id).allowed


def test_deterministic_actor_method_path_and_revision_policy():
    item = registration()
    assert authorize(item, "health", subject_id=item.owner_id).allowed
    assert not authorize(item, "health").allowed
    assert not authorize(item, "health", subject_id=uuid4()).allowed
    assert not authorize(item, "/health", subject_id=item.owner_id).allowed
    assert not authorize(item, "https://evil.com", subject_id=item.owner_id).allowed
    changed = item.model_copy(update={"revision": 2})
    assert authorize(changed, "health", subject_id=item.owner_id).reason == "policy_changed"
    changed = item.model_copy(update={"policy": ApiPolicy(origin="https://other.example.com", operations=item.policy.operations)})
    assert not authorize(changed, "health", subject_id=item.owner_id).allowed
    changed = item.model_copy(update={"pinned_ips": ("8.8.8.8",)})
    assert not authorize(changed, "health", subject_id=item.owner_id).allowed


def test_development_mock_is_explicit_and_production_denied():
    dev = ApiPolicy(origin="http://127.0.0.1:8001", kind="development_mock", operations=policy().operations)
    item = registration(policy=dev, pinned_ips=("127.0.0.1",), approval_digest=policy_digest(dev, ("127.0.0.1",), 1))
    assert authorize(item, "health", subject_id=item.owner_id).reason == "development_disabled"
    assert authorize(item, "health", subject_id=item.owner_id, environment="development").allowed
    with pytest.raises(ValueError):
        destination_ip("127.0.0.2", development=True)


def test_no_credential_fields_or_model_supplied_admin_roles():
    with pytest.raises(ValueError):
        ApiPolicy(**policy().model_dump(), api_key="raw-key")
    with pytest.raises(ValueError):
        Operation(operation_id="health", path="/health", headers={"Authorization": "raw"})
    caller = Principal(uuid4())
    assert not AdminAuthorization().is_admin(caller)
    assert not AdminAuthorization(frozenset({caller.subject_id})).is_admin({"subject_id": caller.subject_id, "is_admin": True})
    unsafe = policy().model_copy(update={"origin": "http://127.0.0.1"})
    with pytest.raises(ValueError):
        ApiPolicy.model_validate(unsafe)


def test_legacy_client_requires_explicit_approval_and_no_dns():
    def forbidden(*args):
        pytest.fail("Unapproved request must not resolve DNS")
    client = MockApiClient(targets={"local": MockTarget("http://127.0.0.1:8001", ("127.0.0.1",))},
        operations={"health": MockOperation("local", "/health")}, resolver=forbidden)
    assert client.execute("health").error == "blocked"


def test_public_backend_rebinding_and_peer_check(monkeypatch):
    from registry.execution import ApprovedTarget
    target = ApprovedTarget("https://api.example.com", ("93.184.216.34",))
    def checked(value):
        try:
            return destination_ip(value)
        except ValueError:
            raise DestinationBlocked("blocked") from None
    backend = PinnedBackend(target, target.allowed_ips, resolver=lambda *_: {"127.0.0.1"}, ip_validator=checked)
    with pytest.raises(DestinationBlocked):
        backend.connect_tcp(target.host, 443)
    from tests.test_security_tools import FakeSocket
    class WrongPeer(FakeSocket):
        def getpeername(self):
            return ("8.8.8.8", 443)
    fake = WrongPeer(b"")
    monkeypatch.setattr("tools.api_client.socket.socket", lambda *_: fake)
    backend = PinnedBackend(target, target.allowed_ips, resolver=lambda *_: set(target.allowed_ips), ip_validator=checked)
    with pytest.raises(DestinationBlocked):
        backend.connect_tcp(target.host, 443)
    assert not fake.sent


def test_missing_database_enforcement_fails_closed():
    from types import SimpleNamespace
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def failed():
        raise RuntimeError("credential=hidden")
        yield
    owner = Principal(uuid4())
    executor = ApprovedApiExecutor(SimpleNamespace(_principal=lambda _: None,
        database=SimpleNamespace(transaction=failed)), principal=owner)
    result = asyncio.run(executor.aexecute(uuid4(), "health"))
    assert result.error == "blocked"
    assert "hidden" not in result.model_dump_json()
