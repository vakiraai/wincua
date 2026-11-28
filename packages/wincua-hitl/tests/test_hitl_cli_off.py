import asyncio
import io

from wincua_hitl import CliUI, OffUI, ask_human, confirm_action, set_default_ui


def _cli(text, tmp_path):
    out = io.StringIO()
    return CliUI(stdin=io.StringIO(text), stdout=out, shot_dir=tmp_path), out


def test_cli_question_option_by_number(tmp_path):
    ui, out = _cli("2\n", tmp_path)
    answer = asyncio.run(ask_human("Mode?", options=["Standard", "Scientific"], ui=ui))
    assert answer == "Scientific"
    assert "[2] Scientific" in out.getvalue()


def test_cli_saves_screenshot(tmp_path):
    ui, out = _cli("ok\n", tmp_path)
    asyncio.run(ask_human("look", screenshot=b"\x89PNG....", ui=ui))
    assert list(tmp_path.glob("*.png"))
    assert "screenshot:" in out.getvalue()


def test_cli_confirm(tmp_path):
    ui, _ = _cli("y\n", tmp_path)
    assert asyncio.run(confirm_action({"tool": "key", "args": {"keys": "del"}}, ui=ui)) is True
    ui, _ = _cli("n\n", tmp_path)
    assert asyncio.run(confirm_action({"tool": "key", "args": {"keys": "del"}}, ui=ui)) is False


def test_cli_eof_fails_closed(tmp_path):
    ui, _ = _cli("", tmp_path)
    assert asyncio.run(ask_human("q", ui=ui)) is None
    assert asyncio.run(confirm_action({"tool": "x"}, ui=ui)) is False


def test_cli_next_step_and_takeover(tmp_path):
    ui, _ = _cli("invoke e1\n\n", tmp_path)

    async def run():
        resp = await ui.next_step(task="t", observation=None)
        assert resp.kind == "action" and resp.action["args"] == {"element_id": "e1"}
        resp = await ui.takeover(task="t")
        assert resp.kind == "resume"

    asyncio.run(run())


def test_off_mode_fails_closed():
    ui = OffUI()
    assert asyncio.run(ask_human("q", ui=ui)) is None
    assert asyncio.run(confirm_action({"tool": "x"}, ui=ui)) is False


def test_default_ui_from_mode_name():
    try:
        set_default_ui("off")
        assert asyncio.run(ask_human("q")) is None
    finally:
        set_default_ui(None)


def test_cli_timeout_does_not_lose_next_line(tmp_path):
    import os
    import threading

    r, w = os.pipe()
    rf, wf = os.fdopen(r), os.fdopen(w, "w")
    ui = CliUI(stdin=rf, stdout=io.StringIO(), shot_dir=tmp_path)

    async def run():
        assert await ui.prompt(question="first", timeout_s=0.2) is None
        threading.Timer(0.1, lambda: (wf.write("second\n"), wf.flush())).start()
        assert await ui.prompt(question="again", timeout_s=5) == "second"

    asyncio.run(run())
    wf.close()
