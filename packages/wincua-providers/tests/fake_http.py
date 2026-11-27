"""Fake HTTP transport for provider tests — records requests, replays canned responses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from wincua_providers import HTTPResponse


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeTransport:
    responses: list[Any] = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)

    def add(self, status: int, body: Any) -> "FakeTransport":
        self.responses.append((status, body))
        return self

    def fail(self, exc: Exception) -> "FakeTransport":
        self.responses.append(exc)
        return self

    async def __call__(self, method, url, headers, body, timeout_s) -> HTTPResponse:
        self.requests.append(
            Request(method, url, dict(headers), json.loads(body) if body else None)
        )
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return HTTPResponse(status=status, body=raw)


PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def ollama_tool_reply(*calls: tuple[str, dict]) -> dict:
    return {
        "model": "m",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls],
        },
        "done": True,
    }


def openai_tool_reply(*calls: tuple[str, dict]) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)},
                        }
                        for i, (n, a) in enumerate(calls)
                    ],
                }
            }
        ]
    }


def anthropic_tool_reply(*calls: tuple[str, dict], text: str = "") -> dict:
    content: list[dict] = [{"type": "text", "text": text}] if text else []
    content += [
        {"type": "tool_use", "id": f"toolu_{i}", "name": n, "input": a}
        for i, (n, a) in enumerate(calls)
    ]
    return {"content": content, "stop_reason": "tool_use"}
