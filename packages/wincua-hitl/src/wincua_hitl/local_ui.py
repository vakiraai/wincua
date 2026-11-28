"""LocalUI — loopback-only web page for HITL, stdlib ``http.server`` only.

Security model:
- Binds only to loopback; non-loopback hosts are refused.
- A random per-session token is required on every request except the static
  assets: ``/?token=...`` for the page, ``X-Wincua-Token`` for the JSON API.
  The custom header also means cross-site pages cannot post answers.
- ``Host`` must be a loopback name (blocks DNS rebinding).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from wincua_hitl.settings import is_loopback_host
from wincua_hitl.transport import HitlTransport, normalize_response
from wincua_hitl.types import HumanRequest, HumanResponse, image_mime

MAX_BODY = 64 * 1024
_STATIC = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class _Pending:
    __slots__ = ("request", "future", "loop")

    def __init__(self, request: HumanRequest, future: asyncio.Future, loop: asyncio.AbstractEventLoop):
        self.request = request
        self.future = future
        self.loop = loop


class LocalUI(HitlTransport):
    """Serve the HITL page on ``http://127.0.0.1:<port>/?token=<token>``."""

    name = "ui"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        token: str | None = None,
        open_browser: bool = False,
        timeout_s: float = 300.0,
        confirm_risky_actions: bool = True,
        title: str = "wincua",
    ) -> None:
        super().__init__(timeout_s=timeout_s, confirm_risky_actions=confirm_risky_actions)
        if not is_loopback_host(host):
            raise ValueError(f"LocalUI only binds to loopback, refusing host {host!r}")
        self.host = "127.0.0.1" if host.lower() == "localhost" else host.strip("[]")
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(24)
        self.open_browser = open_browser
        self.title = title
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._opened = False
        self._seq = 0

    @property
    def _started(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    @property
    def session_url(self) -> str:
        return f"{self.url}/?token={self.token}"

    async def start(self) -> str:
        if self._server is not None:
            return self.session_url
        server_cls = _V6Server if ":" in self.host else _V4Server
        handler = _make_handler(self)
        try:
            server = server_cls((self.host, self.port), handler)
        except OSError as exc:
            if self.port == 0:
                raise
            print(
                f"wincua-hitl: port {self.port} unavailable ({exc}); using a free port",
                file=sys.stderr,
            )
            server = server_cls((self.host, 0), handler)
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="wincua-hitl", daemon=True
        )
        self._thread.start()
        print(f"wincua HITL UI: {self.session_url}", file=sys.stderr)
        if self.open_browser and not self._opened:
            self._opened = True
            try:
                webbrowser.open(self.session_url)
            except Exception:
                pass
        return self.session_url

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for p in pending:
            p.loop.call_soon_threadsafe(_resolve, p.future, None)

    async def _exchange(self, req: HumanRequest, timeout_s: float) -> HumanResponse | None:
        if self._server is None:
            await self.start()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        with self._lock:
            self._pending[req.id] = _Pending(req, fut, loop)
            self._seq += 1
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        finally:
            with self._lock:
                if self._pending.pop(req.id, None) is not None:
                    self._seq += 1

    # Called from the HTTP thread.
    def _state(self) -> dict[str, Any]:
        with self._lock:
            current = next(iter(self._pending.values()), None)
            return {
                "title": self.title,
                "seq": self._seq,
                "queued": len(self._pending),
                "pending": current.request.public_dict() if current else None,
                "history": self.history[-20:],
                "server_time": time.time(),
            }

    def _screenshot(self, request_id: str) -> bytes | None:
        with self._lock:
            p = self._pending.get(request_id)
            return p.request.screenshot if p else None

    def _respond(self, request_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            p = self._pending.get(request_id)
        if p is None:
            return HTTPStatus.NOT_FOUND, {"error": "no pending request with that id"}
        kind = payload.get("kind")
        text = payload.get("text")
        action = payload.get("action")
        if not isinstance(kind, str):
            return HTTPStatus.BAD_REQUEST, {"error": "kind is required"}
        if text is not None and not isinstance(text, str):
            return HTTPStatus.BAD_REQUEST, {"error": "text must be a string"}
        if action is not None and not isinstance(action, dict):
            return HTTPStatus.BAD_REQUEST, {"error": "action must be an object"}
        try:
            resp = normalize_response(p.request, HumanResponse(kind=kind, text=text, action=action))  # type: ignore[arg-type]
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        with self._lock:
            if self._pending.pop(request_id, None) is None:
                return HTTPStatus.CONFLICT, {"error": "request already answered"}
            self._seq += 1
        p.loop.call_soon_threadsafe(_resolve, p.future, resp)
        return HTTPStatus.OK, {"ok": True, "kind": resp.kind, "action": resp.action}


def _resolve(fut: asyncio.Future, value: Any) -> None:
    if not fut.done():
        fut.set_result(value)


class _V4Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class _V6Server(_V4Server):
    address_family = socket.AF_INET6


def _static(name: str) -> bytes:
    return resources.files("wincua_hitl").joinpath("static", name).read_bytes()


def _make_handler(ui: LocalUI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "wincua-hitl"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", _CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            if host.startswith("["):
                name, _, rest = host[1:].partition("]")
                port = rest.lstrip(":")
            else:
                name, _, port = host.rpartition(":") if ":" in host else (host, "", "")
            return is_loopback_host(name) and port == str(ui.port)

        def _token_ok(self, supplied: str | None) -> bool:
            return bool(supplied) and hmac.compare_digest(supplied.encode(), ui.token.encode())

        def _guard(self, supplied: str | None) -> bool:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad Host header"})
                return False
            if not self._token_ok(supplied):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid session token"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            path = parts.path
            if path in _STATIC:
                if not self._host_ok():
                    self._json(HTTPStatus.FORBIDDEN, {"error": "bad Host header"})
                    return
                fname, ctype = _STATIC[path]
                self._send(HTTPStatus.OK, _static(fname), ctype)
                return
            if path in ("/", "/index.html"):
                token = (parse_qs(parts.query).get("token") or [None])[0]
                if not self._host_ok():
                    self._json(HTTPStatus.FORBIDDEN, {"error": "bad Host header"})
                    return
                if not self._token_ok(token):
                    self._send(
                        HTTPStatus.UNAUTHORIZED,
                        b"<!doctype html><title>wincua HITL</title><p>Open the full URL "
                        b"(with ?token=...) printed in the agent's terminal.</p>",
                        "text/html; charset=utf-8",
                    )
                    return
                self._send(HTTPStatus.OK, _static("index.html"), "text/html; charset=utf-8")
                return
            if not self._guard(self.headers.get("X-Wincua-Token")):
                return
            if path == "/api/state":
                self._json(HTTPStatus.OK, ui._state())
                return
            if path.startswith("/api/screenshot/"):
                data = ui._screenshot(path.rsplit("/", 1)[-1])
                if not data:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no screenshot"})
                    return
                self._send(HTTPStatus.OK, data, image_mime(data))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard(self.headers.get("X-Wincua-Token")):
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/respond/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected JSON"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if not isinstance(payload, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "expected a JSON object"})
                return
            status, body = ui._respond(path.rsplit("/", 1)[-1], payload)
            self._json(status, body)

    return Handler
