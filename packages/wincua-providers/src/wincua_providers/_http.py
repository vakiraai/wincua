"""Minimal async JSON-over-HTTP helper (stdlib only) with an injectable transport.

Providers never import an HTTP client directly; they call :func:`request_json`
with a ``transport``. Tests pass a fake transport so no real API is contacted.
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


@dataclass
class HTTPResponse:
    status: int
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def text(self, limit: int = 500) -> str:
        return self.body.decode("utf-8", errors="replace")[:limit]


# (method, url, headers, body, timeout_s) -> HTTPResponse
# Must raise ConnectionError / TimeoutError / OSError when the host is unreachable.
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], Awaitable[HTTPResponse]]


class ProviderUnavailable(Exception):
    """The provider cannot serve requests (unreachable, auth, missing model…).

    Raised inside providers; ``plan()`` turns it into
    ``ProviderResult(available=False)`` so the fallback chain moves on.
    """

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
# Local model servers must not go through HTTP(S)_PROXY or the Windows system
# proxy: urllib's registry bypass ("<local>") does not cover 127.0.0.1.
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _urllib_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_s: float,
) -> HTTPResponse:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    opener = _DIRECT_OPENER.open if is_loopback_url(url) else urllib.request.urlopen
    try:
        with opener(req, timeout=timeout_s) as resp:  # noqa: S310 - user-configured URL
            return HTTPResponse(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        return HTTPResponse(status=exc.code, body=exc.read() or b"")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(reason)) from exc
        raise ConnectionError(str(reason)) from exc


async def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_s: float,
) -> HTTPResponse:
    return await asyncio.to_thread(_urllib_request, method, url, headers, body, timeout_s)


# 500 is not retried: llama-server uses it for config errors (no --jinja / --mmproj).
RETRY_STATUSES = frozenset({429, 502, 503, 504, 529})


async def request_json(
    transport: Transport,
    method: str,
    url: str,
    *,
    payload: Any = None,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = 120.0,
    retries: int = 1,
    retry_backoff_s: float = 1.0,
    label: str = "provider",
) -> HTTPResponse:
    """Send a JSON request; raise :class:`ProviderUnavailable` if unreachable.

    HTTP error statuses are returned to the caller (it may adapt and retry),
    except transient ones (429/5xx) which are retried ``retries`` times first.
    """
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    attempt = 0
    while True:
        try:
            resp = await transport(method, url, hdrs, body, timeout_s)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(retry_backoff_s * attempt)
                continue
            raise ProviderUnavailable(
                f"{label}: timed out after {timeout_s:g}s ({url}); a local model may still be loading, "
                "raise timeout_s for this provider in wincua.toml"
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise ProviderUnavailable(f"{label}: unreachable at {url} ({exc})") from exc
        if resp.status in RETRY_STATUSES and attempt < retries:
            attempt += 1
            await asyncio.sleep(retry_backoff_s * attempt)
            continue
        return resp


def error_text(resp: HTTPResponse) -> str:
    """The ``error`` / ``error.message`` field of a JSON error body (Ollama, OpenAI…), else ""."""
    try:
        data = resp.json()
    except (ValueError, UnicodeDecodeError):
        return ""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        err = err.get("message")
    return str(err) if err else ""


def raise_for_status(resp: HTTPResponse, *, label: str, hint: str = "") -> None:
    if 200 <= resp.status < 300:
        return
    detail = resp.text()
    msg = f"{label}: HTTP {resp.status}: {error_text(resp) or detail}"
    if hint:
        msg = f"{msg} — {hint}"
    raise ProviderUnavailable(msg, status=resp.status, body=detail)
