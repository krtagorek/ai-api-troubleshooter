"""Canonical URL/path and destination policy; no network access here."""

import ipaddress
import re
from urllib.parse import urlsplit


def origin(value: str, *, development=False) -> str:
    try:
        if not isinstance(value, str) or not value.isascii() or any(
            c.isspace() or ord(c) < 32 or c in "\\%@?#" for c in value
        ):
            raise ValueError
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if not host or parsed.username or parsed.password or parsed.path not in ("", "/"):
            raise ValueError
        if development:
            if parsed.scheme != "http" or host != "127.0.0.1" or port is None or not 1024 <= port <= 65535:
                raise ValueError
            return f"http://127.0.0.1:{port}"
        if parsed.scheme != "https" or port not in (None, 443):
            raise ValueError
        host = host.lower()
        labels = host.split(".")
        if len(host) > 253 or len(labels) < 2 or not re.search("[a-z]", labels[-1]):
            raise ValueError
        if labels[-1] in {"localhost", "local", "internal", "lan", "home", "test", "invalid"}:
            raise ValueError
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return f"https://{host}"
        raise ValueError
    except Exception:
        raise ValueError("Use a canonical HTTPS DNS origin, or the explicitly configured development mock") from None


def path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 256 or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?", value):
        raise ValueError("Only exact canonical paths are allowed; no query, encoding, wildcard, or traversal")
    return value


def destination_ip(value: str, *, development=False) -> str:
    try:
        address = ipaddress.ip_address(value)
        if "%" in value:
            raise ValueError
        if development:
            if str(address) != "127.0.0.1":
                raise ValueError
        elif (not address.is_global or address.is_reserved or address.is_multicast
              or address.is_loopback or address.is_link_local
              or str(address) == "168.63.129.16"
              or (address.version == 6 and (
                  address.ipv4_mapped or address.sixtofour or address.teredo
                  or address in ipaddress.ip_network("64:ff9b::/96")
                  or address in ipaddress.ip_network("64:ff9b:1::/48")))):
            raise ValueError
        return str(address)
    except Exception:
        raise ValueError("Destination is not permitted") from None
