"""Read-only mock diagnostics with exact operation allowlists and pinned sockets.

No runtime API accepts URLs, paths, headers, methods, or request bodies. Configure
targets/operations in trusted Python code. No proxies, redirects, or writes.
"""

import asyncio
import ipaddress
import logging
import select
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from queue import Queue, Empty
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit
import re
import inspect

import httpcore
import httpx
from pydantic import JsonValue

from models.incident import DomainModel
from tools.redaction import install_redaction_filters, log_event, redact

_DNS_SLOTS = threading.BoundedSemaphore(4)


class DestinationBlocked(ValueError):
    pass


def resolve_dns(host, port):
    """Bound resolver concurrency and wait; stalled OS lookups run as daemons."""
    if not _DNS_SLOTS.acquire(blocking=False):
        raise DestinationBlocked("Resolver capacity unavailable")
    result = Queue(maxsize=1)

    def work():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=work, daemon=True).start()
    try:
        records = result.get(timeout=1)
    except Empty:
        raise DestinationBlocked("DNS resolution deadline exceeded") from None
    if not records:
        raise DestinationBlocked("DNS resolution unavailable")
    return {record[4][0] for record in records}


def _permitted_ip(value):
    address = ipaddress.ip_address(value)
    if address == ipaddress.ip_address("fd00:ec2::254"):
        raise DestinationBlocked("Cloud metadata destinations are forbidden")
    if address.version == 6 and address.ipv4_mapped:
        raise DestinationBlocked("Mapped IPv6 destinations are forbidden")
    # Only loopback/RFC1918/ULA mock hosts can be explicitly configured. Cloud
    # metadata/link-local, unspecified, multicast, public and reserved ranges fail.
    private = any(address in ipaddress.ip_network(net) for net in (
        ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16") if address.version == 4 else ("fc00::/7",)))
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        # ::1 is reserved according to ipaddress but is an intentional mock host.
        if address != ipaddress.ip_address("::1"):
            raise DestinationBlocked("Forbidden destination address")
    if not (address.is_loopback or private):
        raise DestinationBlocked("Only explicitly configured local mock hosts are allowed")
    return str(address)


@dataclass(frozen=True)
class MockTarget:
    base_url: str
    allowed_ips: tuple[str, ...]

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        try:
            valid = parsed.scheme in ("http", "https") and parsed.hostname and (
                not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path in ("", "/") and 0 < (parsed.port or (443 if parsed.scheme == "https" else 80)) < 65536)
        except ValueError:
            valid = False
        if not valid or any(c.isspace() or ord(c) < 32 for c in self.base_url) or "\\" in self.base_url:
            raise DestinationBlocked("Invalid mock target origin")
        if not self.allowed_ips:
            raise DestinationBlocked("Mock target requires explicit IP addresses")
        object.__setattr__(self, "allowed_ips", tuple(_permitted_ip(ip) for ip in self.allowed_ips))

    @property
    def host(self):
        return urlsplit(self.base_url).hostname

    @property
    def port(self):
        parsed = urlsplit(self.base_url)
        return parsed.port or (443 if parsed.scheme == "https" else 80)


@dataclass(frozen=True)
class MockOperation:
    target: str
    path: str
    method: str = "GET"
    demo_auth: bool = False
    scenario: str = "success"

    def __post_init__(self):
        if self.method != "GET" or not re.fullmatch(r"/(?:health|inventory/[A-Za-z0-9_-]{1,64}|customers/[A-Za-z0-9_-]{1,64})", self.path):
            raise DestinationBlocked("Operation method or path is not permitted")
        if self.scenario not in {"success", "unauthorized", "forbidden", "not_found", "invalid_payload",
                                "rate_limit", "internal_error", "service_unavailable", "timeout", "schema_mismatch"}:
            raise DestinationBlocked("Unknown demo scenario")


class ApiResult(DomainModel):
    ok: bool
    status_code: int | None = None
    body: JsonValue = None
    error: Literal["blocked", "timeout", "unavailable", "response_too_large", "invalid_response"] | None = None
    untrusted: Literal[True] = True


class SocketStream(httpcore.NetworkStream):
    def __init__(self, sock, deadline=None):
        self.sock = sock
        self.deadline = deadline if deadline is not None else time.monotonic() + 6

    def _timeout(self, timeout):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ReadTimeout("Total operation deadline exceeded")
        return min(timeout if timeout is not None else 2, 2, remaining)

    def read(self, max_bytes, timeout=None):
        try:
            self.sock.settimeout(self._timeout(timeout))
            return self.sock.recv(max_bytes)
        except socket.timeout:
            raise httpcore.ReadTimeout("Read deadline exceeded") from None
        except OSError:
            raise httpcore.ReadError("Read failed") from None

    def write(self, buffer, timeout=None):
        try:
            self.sock.settimeout(self._timeout(timeout))
            self.sock.sendall(buffer)
        except socket.timeout:
            raise httpcore.WriteTimeout("Write deadline exceeded") from None
        except OSError:
            raise httpcore.WriteError("Write failed") from None

    def close(self):
        self.sock.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            self.sock.settimeout(self._timeout(timeout))
            self.sock = ssl_context.wrap_socket(self.sock, server_hostname=server_hostname)
            return self
        except (socket.timeout, httpcore.TimeoutException):
            self.close()
            raise httpcore.ConnectTimeout("TLS deadline exceeded") from None
        except Exception:
            self.close()
            raise httpcore.ConnectError("TLS handshake failed") from None

    def get_extra_info(self, info):
        if info == "ssl_object":
            return getattr(self.sock, "_sslobj", None)
        if info == "is_readable":
            return bool(select.select([self.sock], [], [], 0)[0])
        if info == "server_addr":
            return self.sock.getpeername()
        if info == "socket":
            return self.sock
        return None


class PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, target, snapshot, resolver=resolve_dns, ip_validator=_permitted_ip):
        self.target, self.snapshot, self.resolver = target, frozenset(snapshot), resolver
        self.ip_validator = ip_validator
        self.deadline = time.monotonic() + 6

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.target.host or port != self.target.port or local_address is not None:
            raise DestinationBlocked("Connection origin mismatch")
        # Resolve again immediately before connecting, then connect a numeric
        # sockaddr directly. No hostname resolution occurs between check/use.
        addresses = frozenset(self.ip_validator(ip) for ip in self.resolver(host, port))
        if not addresses or addresses != self.snapshot or not addresses <= set(self.target.allowed_ips):
            raise DestinationBlocked("Destination changed or is not authorized")
        address = sorted(addresses)[0]
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise httpcore.ConnectTimeout("Total operation deadline exceeded")
            sock.settimeout(min(timeout if timeout is not None else 2, 2, remaining))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect((address, port))
            peer_info = sock.getpeername()
            peer = self.ip_validator(peer_info[0])
            if peer != address or peer_info[1] != port:
                raise DestinationBlocked("Connected peer does not match pinned destination")
            return SocketStream(sock, self.deadline)
        except Exception:
            sock.close()
            raise

    def connect_unix_socket(self, *args, **kwargs):
        raise DestinationBlocked("Unix sockets are not permitted")


class CoreStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        yield from self.stream

    def close(self):
        self.stream.close()


class PinnedTransport(httpx.BaseTransport):
    def __init__(self, target, snapshot, resolver=resolve_dns, ip_validator=_permitted_ip):
        self.pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(), max_connections=4,
            max_keepalive_connections=0, retries=0,
            network_backend=PinnedBackend(target, snapshot, resolver, ip_validator),
        )

    def handle_request(self, request):
        response = self.pool.handle_request(httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions))
        return httpx.Response(response.status, headers=response.headers,
                              stream=CoreStream(response.stream), extensions=response.extensions)

    def close(self):
        self.pool.close()


def send_pinned_request(target, path, *, method="GET", demo_auth=False, scenario=None, resolver=resolve_dns, ip_validator=_permitted_ip):
    """Internal transport primitive; callers must establish application authorization."""
    try:
        addresses = frozenset(ip_validator(ip) for ip in resolver(target.host, target.port))
        if not addresses or not addresses <= set(target.allowed_ips):
            return ApiResult(ok=False, error="blocked")
        transport = PinnedTransport(target, addresses, resolver, ip_validator)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if demo_auth:
            headers["Authorization"] = "Bearer demo-token"  # Public fixture only.
        timeout = httpx.Timeout(connect=2, read=2, write=2, pool=1)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=0)
        started = time.monotonic()
        with httpx.Client(transport=transport, timeout=timeout, limits=limits,
                          follow_redirects=False, trust_env=False) as client:
            with client.stream(method, target.base_url.rstrip("/") + path,
                               params={"demo_scenario": scenario} if scenario is not None else None, headers=headers) as response:
                if 300 <= response.status_code < 400:
                    return ApiResult(ok=False, status_code=response.status_code, error="blocked")
                if response.headers.get("content-encoding", "identity") != "identity":
                    return ApiResult(ok=False, error="invalid_response")
                if method == "HEAD":
                    return ApiResult(ok=200 <= response.status_code < 300, status_code=response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if time.monotonic() - started > 6:
                        return ApiResult(ok=False, error="timeout")
                    data.extend(chunk)
                    if len(data) > 65536:
                        return ApiResult(ok=False, error="response_too_large")
                try:
                    import json
                    body = json.loads(data)
                except (ValueError, UnicodeError):
                    return ApiResult(ok=False, status_code=response.status_code, error="invalid_response")
                return ApiResult(ok=200 <= response.status_code < 300,
                                 status_code=response.status_code, body=redact(body))
    except DestinationBlocked:
        return ApiResult(ok=False, error="blocked")
    except (httpx.TimeoutException, httpcore.TimeoutException, socket.timeout):
        return ApiResult(ok=False, error="timeout")
    except Exception:
        return ApiResult(ok=False, error="unavailable")


class MockApiClient:
    def __init__(self, *, targets, operations, resolver=resolve_dns, request_authorizer=None):
        self.targets = MappingProxyType(dict(targets))
        self.operations = MappingProxyType(dict(operations))
        self.resolver = resolver
        self.request_authorizer = request_authorizer
        self._slots = threading.BoundedSemaphore(4)
        if not all(isinstance(t, MockTarget) for t in self.targets.values()):
            raise ValueError("Mock targets must use MockTarget configuration")
        if not all(isinstance(op, MockOperation) and op.target in self.targets for op in self.operations.values()):
            raise ValueError("Operations require configured mock targets")
        install_redaction_filters()

    def execute(self, operation_id: str) -> ApiResult:
        # Legacy synchronous adapter: deny unless trusted application code
        # explicitly injects its approval check. Prefer registry.execution.
        try:
            decision = self.request_authorizer(operation_id) if self.request_authorizer is not None else None
            if inspect.iscoroutine(decision):
                decision.close()
            if decision is not True:
                return ApiResult(ok=False, error="blocked")
        except Exception:
            return ApiResult(ok=False, error="blocked")
        if not self._slots.acquire(blocking=False):
            return ApiResult(ok=False, error="unavailable")
        try:
            try:
                result = self._execute(operation_id)
            except Exception:
                result = ApiResult(ok=False, error="blocked")
        finally:
            self._slots.release()
        log_event(logging.getLogger("diagnostic_tools"), "mock_diagnostic_completed",
                  ok=result.ok, status_code=result.status_code, error=result.error)
        return result

    def _execute(self, operation_id):
        operation = self.operations.get(operation_id)
        if operation is None:
            return ApiResult(ok=False, error="blocked")
        return send_pinned_request(self.targets[operation.target], operation.path,
            demo_auth=operation.demo_auth, scenario=operation.scenario, resolver=self.resolver)

    async def aexecute(self, operation_id):
        return await asyncio.to_thread(self.execute, operation_id)

    def evidence_tool(self, operation_id):
        """Bind an operation in application code; incident/model URLs are ignored."""
        if operation_id not in self.operations:
            raise ValueError("Unknown configured operation")
        def collect(_incident):
            result = self.execute(operation_id)
            if result.status_code is None or result.error:
                return []
            return [{"message": "Untrusted observation from configured mock API",
                     "tool": "mock_api", "result": result.model_dump(mode="json")}]
        return collect
