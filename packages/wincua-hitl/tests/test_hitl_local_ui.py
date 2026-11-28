"""LocalUI over real loopback HTTP with urllib — no browser."""

import asyncio
import json
import time
import urllib.error
import urllib.request

import pytest

from wincua_hitl import LocalUI, ask_human, confirm_action
from wincua_hitl.local_ui import _static

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _http(ui, path, *, method="GET", body=None, token=True, headers=None, host=None):
    req = urllib.request.Request(ui.url + path, method=method)
    if token:
        req.add_header("X-Wincua-Token", ui.token if token is True else token)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if host:
        req.add_header("Host", host)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(), dict(err.headers)


def _wait_pending(ui, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body, _ = _http(ui, "/api/state")
        assert status == 200
        state = json.loads(body)
        if state["pending"]:
            return state["pending"]
        time.sleep(0.02)
    raise AssertionError("no pending request")


def _answer(ui, payload, check=None):
    pending = _wait_pending(ui)
    if check:
        check(pending)
    status, body, _ = _http(ui, f"/api/respond/{pending['id']}", method="POST", body=payload)
    assert status == 200, body
    return pending


async def _with_ui(fn):
    ui = LocalUI(port=0, timeout_s=10)
    await ui.start()
    try:
        return await fn(ui)
    finally:
        await ui.stop()


def test_binds_loopback_and_serves_page_with_token():
    async def run(ui):
        assert ui.url.startswith("http://127.0.0.1:")
        assert ui._server.server_address[0] == "127.0.0.1"
        status, body, headers = await asyncio.to_thread(_http, ui, f"/?token={ui.token}", token=False)
        assert status == 200 and b"wincua" in body
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["Referrer-Policy"] == "no-referrer"
        status, _, _ = await asyncio.to_thread(_http, ui, "/", token=False)
        assert status == 401
        status, _, _ = await asyncio.to_thread(_http, ui, "/?token=wrong", token=False)
        assert status == 401
        status, _, _ = await asyncio.to_thread(_http, ui, "/app.js", token=False)
        assert status == 200

    asyncio.run(_with_ui(run))


def test_api_requires_token_header_and_loopback_host():
    async def run(ui):
        assert (await asyncio.to_thread(_http, ui, "/api/state", token=False))[0] == 401
        assert (await asyncio.to_thread(_http, ui, "/api/state", token="nope"))[0] == 401
        # Token in the query string is not accepted for the API.
        assert (await asyncio.to_thread(_http, ui, f"/api/state?token={ui.token}", token=False))[0] == 401
        assert (await asyncio.to_thread(_http, ui, "/api/state"))[0] == 200
        bad_host = await asyncio.to_thread(_http, ui, "/api/state", host=f"evil.example:{ui.port}")
        assert bad_host[0] == 403
        wrong_port = await asyncio.to_thread(_http, ui, "/api/state", host="127.0.0.1:1")
        assert wrong_port[0] == 403
        ok_host = await asyncio.to_thread(_http, ui, "/api/state", host=f"localhost:{ui.port}")
        assert ok_host[0] == 200

    asyncio.run(_with_ui(run))


def test_ask_human_round_trip_with_screenshot_and_observation():
    async def run(ui):
        from wincua_driver import ImageRef
        from wincua_driver.backend.mock import MockBackend

        obs = await MockBackend().observe("Calculator", ["uia", "app"])

        def check(p):
            assert p["kind"] == "question"
            assert p["question"] == "Which mode?"
            assert p["options"] == ["Standard", "Scientific"]
            assert p["has_screenshot"] is True
            assert p["observation"]["app"]["title"] == "Calculator"
            assert any(e["element_id"] == "auto:num1Button" for e in p["observation"]["uia"])
            status, body, headers = _http(ui, f"/api/screenshot/{p['id']}")
            assert status == 200 and body == PNG and headers["Content-Type"] == "image/png"
            assert _http(ui, f"/api/screenshot/{p['id']}", token=False)[0] == 401

        task = asyncio.create_task(
            ask_human(
                "Which mode?",
                options=["Standard", "Scientific"],
                screenshot=ImageRef(data=PNG),
                observation=obs,
                ui=ui,
            )
        )
        await asyncio.to_thread(_answer, ui, {"kind": "answer", "text": "Scientific"}, check)
        assert await task == "Scientific"
        assert ui.history[-1]["response"] == "answer"

    asyncio.run(_with_ui(run))


def test_confirm_action_approve_reject_and_invalid_kind():
    async def run(ui):
        task = asyncio.create_task(
            confirm_action({"tool": "key", "args": {"keys": "alt+f4"}}, reason="closes app", ui=ui)
        )

        def check(p):
            assert p["kind"] == "confirm"
            assert p["action"] == {"tool": "key", "args": {"keys": "alt+f4"}}
            assert p["reason"] == "closes app"
            status, body, _ = _http(ui, f"/api/respond/{p['id']}", method="POST", body={"kind": "answer"})
            assert status == 400 and b"not a valid response" in body

        await asyncio.to_thread(_answer, ui, {"kind": "approve"}, check)
        assert await task is True

        task = asyncio.create_task(confirm_action({"tool": "key", "args": {"keys": "del"}}, ui=ui))
        await asyncio.to_thread(_answer, ui, {"kind": "reject"})
        assert await task is False

        task = asyncio.create_task(confirm_action({"tool": "key", "args": {"keys": "del"}}, ui=ui))
        await asyncio.to_thread(_answer, ui, {"kind": "takeover"})
        assert await task is False

    asyncio.run(_with_ui(run))


def test_next_step_instruction_becomes_action():
    async def run(ui):
        task = asyncio.create_task(ui.next_step(task="t", observation=None))
        await asyncio.to_thread(_answer, ui, {"kind": "instruction", "text": "invoke auto:num1Button"})
        resp = await task
        assert resp.kind == "action"
        assert resp.action == {"tool": "invoke", "args": {"element_id": "auto:num1Button"}}

    asyncio.run(_with_ui(run))


def test_rejects_bad_posts():
    async def run(ui):
        task = asyncio.create_task(ui.prompt(question="q"))
        pending = await asyncio.to_thread(_wait_pending, ui)
        rid = pending["id"]
        status, _, _ = await asyncio.to_thread(
            _http, ui, f"/api/respond/{rid}", method="POST", body={"kind": "answer"}, token="bad"
        )
        assert status == 401

        def form_post():
            req = urllib.request.Request(
                ui.url + f"/api/respond/{rid}", data=b"kind=answer", method="POST"
            )
            req.add_header("X-Wincua-Token", ui.token)
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as err:
                return err.code

        assert await asyncio.to_thread(form_post) == 415
        status, _, _ = await asyncio.to_thread(
            _http, ui, "/api/respond/unknown", method="POST", body={"kind": "answer"}
        )
        assert status == 404
        await asyncio.to_thread(_answer, ui, {"kind": "answer", "text": "ok"})
        assert await task == "ok"
        status, _, _ = await asyncio.to_thread(
            _http, ui, f"/api/respond/{rid}", method="POST", body={"kind": "answer"}
        )
        assert status == 404

    asyncio.run(_with_ui(run))


def test_timeout_fails_closed():
    async def run(ui):
        assert await ask_human("anyone?", ui=ui, timeout_s=0.2) is None
        assert await confirm_action({"tool": "key", "args": {}}, ui=ui, timeout_s=0.2) is False
        status, body, _ = await asyncio.to_thread(_http, ui, "/api/state")
        assert json.loads(body)["pending"] is None
        assert ui.history[-1]["response"] == "timeout"

    asyncio.run(_with_ui(run))


def test_credential_answer_redacted_in_history():
    async def run(ui):
        task = asyncio.create_task(ui.prompt(question="PIN?", risk="credential"))
        await asyncio.to_thread(_answer, ui, {"kind": "answer", "text": "1234"})
        assert await task == "1234"
        assert ui.history[-1]["text"] == "<redacted>"

    asyncio.run(_with_ui(run))


def test_stop_releases_waiters_and_tokens_differ():
    async def run():
        ui = LocalUI(port=0)
        await ui.start()
        task = asyncio.create_task(ui.prompt(question="q"))
        await asyncio.to_thread(_wait_pending, ui)
        await ui.stop()
        assert await task is None
        assert LocalUI().token != LocalUI().token

    asyncio.run(run())


def test_static_assets_packaged():
    for name in ("index.html", "app.js", "app.css"):
        assert _static(name)


@pytest.mark.parametrize("busy_first", [True])
def test_port_in_use_falls_back(busy_first):
    import socket

    async def run():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            ui = LocalUI(port=port)
            await ui.start()
            assert ui.port != port
            await ui.stop()
        finally:
            s.close()

    asyncio.run(run())
