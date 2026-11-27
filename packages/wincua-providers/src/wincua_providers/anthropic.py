"""AnthropicProvider — Claude Messages API with tool use (+ optional computer use).

UIA tools (invoke / type / key …) are always offered. With ``computer_use=True``
the Anthropic ``computer`` tool is added too; its screenshot-space coordinates
are mapped back to screen coordinates (window origin + scale) and translated
into wincua ``click`` / ``type`` / ``key`` / ``wait`` steps. Unsupported
computer actions (drag, scroll, mouse_move, …) come back to the model as
failed tool results.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Sequence

from wincua_providers._http import ProviderUnavailable, raise_for_status
from wincua_providers.base import PlanStep, ProviderResult
from wincua_providers.chat import HTTPChatProvider
from wincua_providers.prompting import (
    NUDGE,
    clean_args,
    group_history,
    png_size,
    screenshot_png,
    user_turn_text,
    window_origin,
)
from wincua_providers.tools import anthropic_tools, parse_arguments, to_plan_step

ANTHROPIC_VERSION = "2023-06-01"

_XDOTOOL_KEYS = {
    "return": "enter",
    "kp_enter": "enter",
    "escape": "escape",
    "esc": "escape",
    "backspace": "backspace",
    "delete": "delete",
    "tab": "tab",
    "space": "space",
    "super": "win",
    "super_l": "win",
    "control": "ctrl",
    "control_l": "ctrl",
    "alt_l": "alt",
    "shift_l": "shift",
    "page_up": "pageup",
    "page_down": "pagedown",
}


def _normalize_keys(keys: str) -> str:
    parts = [p.strip() for p in str(keys).replace(" ", "+").split("+") if p.strip()]
    return "+".join(_XDOTOOL_KEYS.get(p.lower(), p.lower()) for p in parts)


class AnthropicProvider(HTTPChatProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com"
    default_model = "claude-sonnet-4-5"
    default_api_key_env = "ANTHROPIC_API_KEY"
    requires_api_key = True

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        computer_use: bool = False,
        computer_tool: str = "computer_20250124",
        computer_beta: str = "computer-use-2025-01-24",
        max_screenshot_px: tuple[int, int] | list[int] = (1280, 800),
        max_tokens: int | None = 1024,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("tool_mode", "native")
        super().__init__(model, base_url, max_tokens=max_tokens, **kwargs)
        self.computer_use = bool(computer_use)
        self.computer_tool = computer_tool
        self.computer_beta = computer_beta
        self.max_screenshot_px = (int(max_screenshot_px[0]), int(max_screenshot_px[1]))
        self._display = (1024, 768)
        self._scale = 1.0
        self._origin = (0, 0)

    # -- request building ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        key = self._require_key() or ""
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        if self.computer_use and self.computer_beta:
            headers["anthropic-beta"] = self.computer_beta
        return headers

    def _prepare_screenshot(self, observation: Any) -> str | None:
        """Base64 PNG for the model; records display size, scale and window origin."""
        data = screenshot_png(observation) if self.send_screenshot else None
        self._origin = window_origin(observation)
        self._scale = 1.0
        if not data:
            return None
        size = png_size(data)
        if size:
            w, h = size
            max_w, max_h = self.max_screenshot_px
            scale = min(1.0, max_w / w, max_h / h)
            if scale < 1.0:
                try:
                    from PIL import Image  # type: ignore[import-not-found]

                    img = Image.open(io.BytesIO(data))
                    new = (max(1, int(w * scale)), max(1, int(h * scale)))
                    buf = io.BytesIO()
                    img.resize(new).save(buf, format="PNG")
                    data, size, self._scale = buf.getvalue(), new, scale
                except Exception:
                    self._scale = 1.0
            self._display = size
        return base64.b64encode(data).decode("ascii")

    def _tools(self) -> list[dict[str, Any]]:
        tools = anthropic_tools(self.tools)
        if self.computer_use:
            tools.append(
                {
                    "type": self.computer_tool,
                    "name": "computer",
                    "display_width_px": self._display[0],
                    "display_height_px": self._display[1],
                }
            )
        return tools

    def build_messages(
        self, task: str, observation: Any, history: Sequence[Any] | None
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        turns = group_history(history)
        if turns:
            messages.append({"role": "user", "content": [{"type": "text", "text": f"Task: {task}"}]})
        for turn in turns:
            if turn.text is not None and not turn.calls:
                messages.append(
                    {"role": "assistant", "content": [{"type": "text", "text": turn.text or "(no tool call)"}]}
                )
                messages.append({"role": "user", "content": [{"type": "text", "text": NUDGE}]})
                continue
            blocks: list[dict[str, Any]] = []
            if turn.text:
                blocks.append({"type": "text", "text": turn.text})
            results: list[dict[str, Any]] = []
            for c in turn.calls:
                if "_cu" in c.args:
                    name, inp = "computer", dict(c.args["_cu"])
                else:
                    name, inp = c.name, clean_args(c.args)
                blocks.append({"type": "tool_use", "id": c.call_id, "name": name, "input": inp})
                results.append({"type": "tool_result", "tool_use_id": c.call_id, "content": c.result})
            messages.append({"role": "assistant", "content": blocks})
            messages.append({"role": "user", "content": results})

        obs_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": user_turn_text(task, observation, first=not turns)}
        ]
        img = self._prepare_screenshot(observation)
        if img:
            obs_blocks.append(
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}
            )
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"].extend(obs_blocks)
        else:
            messages.append({"role": "user", "content": obs_blocks})
        return messages

    def build_payload(
        self, task: str, observation: Any, history: Sequence[Any] | None
    ) -> dict[str, Any]:
        messages = self.build_messages(task, observation, history)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens or 1024,
            "system": self.system_prompt,
            "tools": self._tools(),
            "messages": messages,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    # -- computer-use mapping -----------------------------------------------------

    def _to_screen(self, coord: Any) -> tuple[int, int] | None:
        if not isinstance(coord, (list, tuple)) or len(coord) != 2:
            return None
        x, y = coord
        ox, oy = self._origin
        return ox + round(float(x) / self._scale), oy + round(float(y) / self._scale)

    def map_computer_action(self, inp: dict[str, Any], call_id: str | None) -> PlanStep:
        action = str(inp.get("action") or "")
        cu = dict(inp)
        if action == "screenshot":
            return PlanStep(kind="tool", name="wait", args={"seconds": 0, "_cu": cu}, call_id=call_id)
        if action == "left_click":
            pt = self._to_screen(inp.get("coordinate"))
            if pt:
                return PlanStep(
                    kind="tool", name="click", args={"x": pt[0], "y": pt[1], "_cu": cu}, call_id=call_id
                )
        if action == "type":
            return PlanStep(
                kind="tool", name="type", args={"text": str(inp.get("text", "")), "_cu": cu}, call_id=call_id
            )
        if action == "key":
            return PlanStep(
                kind="tool", name="key", args={"keys": _normalize_keys(inp.get("text", "")), "_cu": cu},
                call_id=call_id,
            )
        if action == "wait":
            return PlanStep(
                kind="tool", name="wait", args={"seconds": float(inp.get("duration", 1)), "_cu": cu},
                call_id=call_id,
            )
        return PlanStep(
            kind="tool",
            name=f"computer.{action or 'unknown'}",
            args={"_cu": cu},
            call_id=call_id,
        )

    # -- API ----------------------------------------------------------------------

    async def _chat(
        self, *, task: str, observation: Any | None, history: Sequence[Any] | None
    ) -> ProviderResult:
        headers = self._headers()
        url = f"{self.base_url}/v1/messages"
        payload = self.build_payload(task, observation, history)
        resp = await self._request("POST", url, payload, headers)
        if self._downgrade(resp):
            resp = await self._request(
                "POST", url, self.build_payload(task, observation, history), headers
            )
        raise_for_status(resp, label=self.name)
        data = resp.json() or {}
        texts: list[str] = []
        steps: list[PlanStep] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text") or "")
            elif btype == "tool_use":
                name = block.get("name") or ""
                inp = parse_arguments(block.get("input"))
                if name == "computer":
                    steps.append(self.map_computer_action(inp, block.get("id")))
                else:
                    steps.append(to_plan_step(name, inp, block.get("id")))
        text = "\n".join(t for t in texts if t)
        if not steps:
            steps = self._steps_from_text(text)
        return ProviderResult(
            steps=steps,
            raw=data,
            text=text or None,
            error=None if steps else "model returned no tool call",
        )

    async def probe(self) -> tuple[bool, str]:
        try:
            resp = await self._request(
                "GET", f"{self.base_url}/v1/models", headers=self._headers(), timeout_s=10.0
            )
            raise_for_status(resp, label=self.name)
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, f"anthropic ok (model {self.model!r})"
