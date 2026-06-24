"""Shared OpenAI-compatible LLM and session helpers."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json_response(req: Request, timeout: int) -> tuple[Any, dict[str, str]]:
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection failed: {exc.reason}") from exc

    if not body.strip():
        return None, headers

    content_type = headers.get("content-type", "")
    if "text/event-stream" in content_type or body.lstrip().startswith("event:"):
        return parse_sse_json(body), headers

    return json.loads(body), headers


def parse_sse_json(body: str) -> Any:
    payloads: list[str] = []
    current: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                payloads.append("\n".join(current))
                current = []
            continue
        if line.startswith("data:"):
            current.append(line[5:].strip())
    if current:
        payloads.append("\n".join(current))

    for payload in payloads:
        if not payload or payload == "[DONE]":
            continue
        parsed = json.loads(payload)
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed or "id" in parsed):
            return parsed
    raise RuntimeError("No JSON-RPC payload was found in the SSE response.")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> tuple[Any, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")
    return read_json_response(req, timeout)


class LlamaClient:
    def __init__(self, base_url: str, model: str, timeout: int, temperature: float) -> None:
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.model = model
        self.timeout = timeout
        self.temperature = temperature

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response, _ = post_json(
            urljoin(self.base_url, "chat/completions"),
            payload,
            {"Accept": "application/json"},
            self.timeout,
        )
        choices = response.get("choices", [])
        if not choices:
            raise RuntimeError(f"llama-server returned no choices: {response}")
        return choices[0].get("message", {})


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{stamp}-{uuid.uuid4().hex[:6]}"

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
        return self.root / f"{safe}.json"

    def save(self, session_id: str, messages: list[dict[str, Any]], config: Any) -> None:
        title = next((m["content"][:60] for m in messages if m.get("role") == "user" and m.get("content")), "Untitled")
        path = self.path_for(session_id)
        existing = {}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
        data = {
            "id": session_id,
            "title": title,
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
            "llama_base_url": getattr(config, "llama_base_url", ""),
            "llama_model": getattr(config, "llama_model", ""),
            "mcp_url": getattr(config, "mcp_url", ""),
            "messages": messages,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("messages", []))

    def list(self) -> list[dict[str, Any]]:
        sessions = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "id": data.get("id", path.stem),
                        "updated_at": data.get("updated_at", ""),
                        "title": data.get("title", ""),
                    }
                )
            except json.JSONDecodeError:
                continue
        return sessions

