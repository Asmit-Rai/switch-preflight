"""A tiny HTTP helper.

Checks never call urllib directly. They call `ctx.fetch`, which defaults to `fetch`
here, so tests can swap in a local server or a fake and never touch the internet.
"""

from __future__ import annotations

import functools
import http.client
import os
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# Some sites refuse requests without a browser-like User-Agent.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Preflight/1.0"
)
DEFAULT_TIMEOUT = 10.0
MAX_BODY = 2_000_000  # bytes read from a response unless the caller asks for less

# System CA bundles, tried only when Python itself ships with none (python.org builds on macOS
# have an empty store until "Install Certificates.command" is run). Verification is never turned off.
_CA_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/usr/local/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
)


class NetError(Exception):
    """No HTTP response at all: DNS failure, refused connection, timeout, TLS error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Response:
    status: int
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


# fetch(url, method="GET", headers=None, timeout=10.0, max_bytes=MAX_BODY) -> Response
Fetch = Callable[..., Response]


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that falls back to the system CA bundle when Python has none."""
    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0) == 0:
        for bundle in _CA_BUNDLES:
            if os.path.isfile(bundle):
                try:
                    context.load_verify_locations(cafile=bundle)
                    break
                except (ssl.SSLError, OSError):
                    continue
    return context


def describe_error(exc: BaseException) -> str:
    """Turn a low-level network exception into a short human reason."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, ConnectionResetError):
        return "connection reset"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS certificate not trusted"
    if isinstance(reason, ssl.SSLError):
        return "TLS error"
    if isinstance(reason, (ValueError, UnicodeError, http.client.InvalidURL)):
        return "invalid URL"
    return type(reason).__name__


def fetch(
    url: str,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BODY,
) -> Response:
    """Fetch a URL, following redirects. HTTP error codes come back as a Response.

    Raises NetError when there is no HTTP response at all.
    """
    all_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    all_headers.update(headers or {})
    try:
        request = urllib.request.Request(url, method=method, headers=all_headers)
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            body = resp.read(max_bytes) if max_bytes > 0 and method != "HEAD" else b""
            return Response(resp.status, resp.geturl(), dict(resp.headers.items()), body)
    except urllib.error.HTTPError as exc:
        try:
            if 300 <= exc.code < 400:  # urllib gave up following it: loop or no Location
                raise NetError(f"redirect could not be followed (HTTP {exc.code})") from exc
            try:
                body = exc.read(max_bytes) if max_bytes > 0 and method != "HEAD" else b""
            except Exception:  # the body of an error page is optional
                body = b""
            return Response(exc.code, url, dict(exc.headers.items()), body)
        finally:
            exc.close()
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        ValueError,
        UnicodeError,
    ) as exc:
        raise NetError(describe_error(exc)) from exc
