#!/usr/bin/env python3
"""
Lightweight local-LLM MCP client for Unity MCP.

Flow:
  .gguf -> llama-server(OpenAI compatible) -> this CLI -> unity-mcp(Streamable HTTP)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from local_llm_clients import CONFIG_DIR, SESSIONS_DIR


DEFAULT_SYSTEM_PROMPT = """You are a local LLM agent connected to Unity through MCP tools.
Use tools when you need to inspect or change the Unity project or scene.
When you call tools, choose the smallest useful action and explain the result briefly.
If a requested Unity operation is ambiguous, ask one concise clarifying question.
Never invent placeholder values such as "null", "none", "unknown", or empty strings for required tool arguments.
If you do not know a required argument, ask the user instead of calling a tool.
For execute_code, the submitted C# snippet must return a value on every path.
"""


@dataclass
class Config:
    llama_base_url: str = "http://127.0.0.1:8081/v1/"
    llama_model: str = "local-model"
    mcp_url: str = "http://127.0.0.1:8080/mcp"
    temperature: float = 0.2
    request_timeout: int = 120
    max_tool_rounds: int = 8
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        config = cls()
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        config.llama_base_url = env("LLAMA_BASE_URL", config.llama_base_url)
        config.llama_model = env("LLAMA_MODEL", config.llama_model)
        config.mcp_url = env("UNITY_MCP_URL", config.mcp_url)
        return config


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


class McpHttpClient:
    def __init__(self, url: str, timeout: int) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 1

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "lightweight-unity-mcp-client", "version": "0.1.0"},
            },
        )
        if isinstance(result, dict) and result.get("serverInfo"):
            server = result["serverInfo"]
            print(f"MCP: connected to {server.get('name', 'server')} {server.get('version', '')}".strip())
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        return list(result.get("tools", [])) if isinstance(result, dict) else []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def request(self, method: str, params: dict[str, Any] | None) -> Any:
        call_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params or {}}
        response, headers = post_json(self.url, payload, self.headers(), self.timeout)
        self.capture_session(headers)
        if response is None:
            return None
        if response.get("error"):
            raise RuntimeError(f"MCP error for {method}: {response['error']}")
        return response.get("result")

    def notify(self, method: str, params: dict[str, Any] | None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        response, headers = post_json(self.url, payload, self.headers(), self.timeout)
        self.capture_session(headers)
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(f"MCP notification error for {method}: {response['error']}")

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def capture_session(self, headers: dict[str, str]) -> None:
        session_id = headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id


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

    def save(self, session_id: str, messages: list[dict[str, Any]], config: Config) -> None:
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
            "llama_base_url": config.llama_base_url,
            "llama_model": config.llama_model,
            "mcp_url": config.mcp_url,
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


def to_openai_tools(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    openai_tools = []
    for tool in mcp_tools:
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return [tool for tool in openai_tools if tool["function"]["name"]]


def tool_result_to_text(result: Any) -> str:
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts: list[str] = []
        for item in result["content"]:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False, indent=2)


def summarize_tool_result(result: Any, limit: int = 1200) -> str:
    text = tool_result_to_text(result).strip()
    if not text:
        return "<empty result>"
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... <truncated>"


def is_tool_result_failure(result: Any) -> bool:
    if isinstance(result, dict):
        if result.get("isError") is True:
            return True
        if result.get("success") is True:
            return False
        if result.get("success") is False:
            return True
        if result.get("error"):
            return True
    text = tool_result_to_text(result).lower()
    failure_markers = (
        "error",
        "exception",
        "traceback",
        "compilation failed",
        "compilationerror",
        "compile error",
        "validationerror",
        "failed",
    )
    return any(marker in text for marker in failure_markers)


def parse_json_tool_request(content: str) -> tuple[str, dict[str, Any]] | None:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    name = data.get("tool") or data.get("name")
    args = data.get("arguments") or data.get("args") or {}
    if isinstance(name, str) and isinstance(args, dict):
        return name, args
    return None


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


RAW_JSON_STRING_FIELDS = {
    ("script_apply_edits", "edits"),
}

CODE_STRING_KEYS = {"replacement", "contents", "code", "text", "system_prompt"}

JSON_STRING_LITERAL_FIELDS = {
    ("execute_code", "code"),
}


def normalize_tool_arguments(arguments: Any, tool_name: str | None = None, schema: dict[str, Any] | None = None) -> Any:
    return normalize_value(arguments, tool_name, None, schema, schema)


def normalize_value(
    value: Any,
    tool_name: str | None,
    key: str | None,
    schema: dict[str, Any] | None,
    root_schema: dict[str, Any] | None,
) -> Any:
    if (tool_name, key) in JSON_STRING_LITERAL_FIELDS:
        return unwrap_json_string_literal(value)
    if key in CODE_STRING_KEYS or (tool_name, key) in RAW_JSON_STRING_FIELDS:
        return value
    if isinstance(value, dict):
        return {
            child_key: normalize_value(
                child_value,
                tool_name,
                child_key,
                property_schema(schema, child_key, root_schema),
                root_schema,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        item_schema = array_item_schema(schema, root_schema)
        return [normalize_value(child_value, tool_name, key, item_schema, root_schema) for child_value in value]
    if isinstance(value, str):
        if schema_allows_string(schema, root_schema):
            return value
        text = value.strip()
        if looks_json_encoded(text):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return value
            return normalize_value(parsed, tool_name, key, schema, root_schema)
    return value


def unwrap_json_string_literal(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith('"'):
        return value
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return value
    return parsed if isinstance(parsed, str) else value


def looks_json_encoded(text: str) -> bool:
    if not text:
        return False
    if text[0] in ('"', "[", "{"):
        return True
    return text in ("true", "false", "null") or re.fullmatch(r"-?\d+(?:\.\d+)?", text) is not None


def tool_call_signature(name: str | None, arguments: Any) -> str:
    return compact_json({"tool": name, "arguments": arguments})


def is_missing_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in ToolValidator.BAD_PLACEHOLDERS)


def last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def repair_tool_call_from_user_text(name: str | None, arguments: Any, user_text: str) -> tuple[Any, str | None]:
    if name != "manage_gameobject" or not isinstance(arguments, dict):
        return arguments, None

    repaired = dict(arguments)
    changes: list[str] = []
    lowered = user_text.lower()

    if is_missing_value(repaired.get("action")) and re.search(r"\b(make|create|add|spawn|place)\b", lowered):
        repaired["action"] = "create"
        changes.append("action=create")

    primitive = infer_primitive_type(user_text)
    if primitive and is_missing_value(repaired.get("primitive_type")):
        repaired["primitive_type"] = primitive
        changes.append(f"primitive_type={primitive}")

    if primitive and is_missing_value(repaired.get("name")):
        repaired["name"] = primitive
        changes.append(f"name={primitive}")

    position = infer_position(user_text)
    if position is not None and is_missing_value(repaired.get("position")):
        repaired["position"] = position
        changes.append(f"position={compact_json(position)}")

    if not changes:
        return arguments, None
    return repaired, ", ".join(changes)


def infer_primitive_type(text: str) -> str | None:
    primitive_types = {
        "cube": "Cube",
        "sphere": "Sphere",
        "capsule": "Capsule",
        "cylinder": "Cylinder",
        "plane": "Plane",
        "quad": "Quad",
    }
    lowered = text.lower()
    for key, value in primitive_types.items():
        if re.search(rf"\b{re.escape(key)}\b", lowered):
            return value
    return None


def infer_position(text: str) -> list[float] | None:
    match = re.search(
        r"(?:at|position|pos)?\s*\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return [number_from_text(group) for group in match.groups()]


def number_from_text(value: str) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else number


def property_schema(schema: Any, property_name: str, root_schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
    root = root_schema if root_schema is not None else schema
    for candidate in schema_candidates(schema, root):
        properties = candidate.get("properties")
        if not isinstance(properties, dict) or property_name not in properties:
            continue
        return resolve_ref(properties[property_name], root)
    for found in deep_property_schemas(schema, property_name, root):
        return found
    return None


def allowed_values_for_property(schema: dict[str, Any], property_name: str) -> list[Any] | None:
    prop_schema = property_schema(schema, property_name, schema)
    if prop_schema is not None:
        values = literal_values(prop_schema, schema)
        if values is not None:
            return values
    return None


def schema_candidates(schema: Any, root_schema: Any | None = None) -> list[dict[str, Any]]:
    if not isinstance(schema, dict):
        return []
    root = root_schema if root_schema is not None else schema
    resolved = resolve_ref(schema, root)
    if not isinstance(resolved, dict):
        return []
    candidates = [resolved]
    for key in ("allOf", "anyOf", "oneOf"):
        items = resolved.get(key)
        if isinstance(items, list):
            for item in items:
                candidates.extend(schema_candidates(item, root))
    return candidates


def resolve_ref(schema: Any, root_schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    current = root_schema
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            return schema
        current = current.get(part)
    if isinstance(current, dict):
        merged = dict(current)
        merged.update({key: value for key, value in schema.items() if key != "$ref"})
        return merged
    return schema


def deep_property_schemas(schema: Any, property_name: str, root_schema: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        resolved = resolve_ref(schema, root_schema)
        properties = resolved.get("properties") if isinstance(resolved, dict) else None
        if isinstance(properties, dict) and property_name in properties:
            prop = resolve_ref(properties[property_name], root_schema)
            if isinstance(prop, dict):
                found.append(prop)
        for value in resolved.values() if isinstance(resolved, dict) else []:
            found.extend(deep_property_schemas(value, property_name, root_schema))
    elif isinstance(schema, list):
        for item in schema:
            found.extend(deep_property_schemas(item, property_name, root_schema))
    return found


def literal_values(schema: Any, root_schema: Any | None = None) -> list[Any] | None:
    if not isinstance(schema, dict):
        return None
    root = root_schema if root_schema is not None else schema
    resolved = resolve_ref(schema, root)
    if "enum" in resolved and isinstance(resolved["enum"], list):
        return resolved["enum"]
    if "const" in resolved:
        return [resolved["const"]]
    for key in ("allOf", "anyOf", "oneOf"):
        items = resolved.get(key)
        if isinstance(items, list):
            values: list[Any] = []
            for item in items:
                nested = literal_values(item, root)
                if nested:
                    values.extend(nested)
            if values:
                return values
    return None


def array_item_schema(schema: Any, root_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    root = root_schema if root_schema is not None else schema
    for candidate in schema_candidates(schema, root):
        items = candidate.get("items")
        if isinstance(items, dict):
            return resolve_ref(items, root)
    return None


def schema_types(schema: Any, root_schema: Any | None = None) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    root = root_schema if root_schema is not None else schema
    types: set[str] = set()
    for candidate in schema_candidates(schema, root):
        value = candidate.get("type")
        if isinstance(value, str):
            types.add(value)
        elif isinstance(value, list):
            types.update(item for item in value if isinstance(item, str))
    return types


def schema_allows_string(schema: Any, root_schema: Any | None = None) -> bool:
    return "string" in schema_types(schema, root_schema)


def value_matches_schema_type(value: Any, schema: Any, root_schema: Any | None = None) -> bool:
    types = schema_types(schema, root_schema)
    if not types:
        return True
    return any(value_matches_json_type(value, json_type) for json_type in types)


def value_matches_json_type(value: Any, json_type: str) -> bool:
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "null":
        return value is None
    return True


def describe_schema_types(schema: Any, root_schema: Any | None = None) -> str:
    types = sorted(schema_types(schema, root_schema))
    return " or ".join(types) if types else "a valid JSON value"


def repair_execute_code_arguments(arguments: Any) -> tuple[Any, str | None]:
    if not isinstance(arguments, dict):
        return arguments, None
    code = arguments.get("code")
    if not isinstance(code, str) or re.search(r"\breturn\b", code):
        return arguments, None
    repaired = dict(arguments)
    repaired["code"] = code.rstrip() + '\nreturn "OK";'
    return repaired, "appended return value for execute_code"


class ToolValidator:
    BAD_PLACEHOLDERS = {"null", "none", "unknown", "undefined", "n/a", ""}

    def __init__(self, mcp_tools: list[dict[str, Any]]) -> None:
        self.tools = {tool.get("name"): tool for tool in mcp_tools if tool.get("name")}

    def schema_for(self, name: str | None) -> dict[str, Any]:
        if not isinstance(name, str):
            return {}
        tool = self.tools.get(name) or {}
        schema = tool.get("inputSchema") or {}
        return schema if isinstance(schema, dict) else {}

    def validate(self, name: str | None, arguments: Any) -> str | None:
        if not isinstance(name, str) or not name:
            return "Tool call is missing a tool name."
        tool = self.tools.get(name)
        if not tool:
            known = ", ".join(sorted(self.tools))
            return f"Unknown tool '{name}'. Available tools: {known}"
        if not isinstance(arguments, dict):
            return f"Arguments for tool '{name}' must be a JSON object."

        schema = self.schema_for(name)
        missing_required = self.missing_required_arguments(schema, arguments)
        if missing_required:
            return f"Missing required argument(s) for tool '{name}': {', '.join(missing_required)}."
        for key, value in arguments.items():
            prop_schema = property_schema(schema, key, schema)
            if key == "action":
                if is_missing_value(value):
                    allowed = allowed_values_for_property(schema, key)
                    suffix = f" Allowed values: {compact_json(allowed)}." if allowed else ""
                    return (
                        f"Invalid placeholder value for {name}.{key}: {compact_json(value)}."
                        f"{suffix} Ask the user if the correct value is unknown."
                    )
                continue
            if isinstance(value, str) and value.strip().lower() in self.BAD_PLACEHOLDERS:
                allowed = allowed_values_for_property(schema, key)
                suffix = f" Allowed values: {compact_json(allowed)}." if allowed else ""
                return (
                    f"Invalid placeholder value for {name}.{key}: {value!r}."
                    f"{suffix} Ask the user if the correct value is unknown."
                )
            if prop_schema is not None and not value_matches_schema_type(value, prop_schema, schema):
                return (
                    f"Invalid type for {name}.{key}: got {type(value).__name__}, "
                    f"expected {describe_schema_types(prop_schema, schema)}."
                )
            allowed = allowed_values_for_property(schema, key)
            if allowed is not None and value not in allowed:
                return (
                    f"Invalid value for {name}.{key}: {compact_json(value)}."
                    f" Allowed values: {compact_json(allowed)}."
                )
        return None

    def missing_required_arguments(self, schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for candidate in schema_candidates(schema, schema):
            required = candidate.get("required")
            if not isinstance(required, list):
                continue
            for key in required:
                if isinstance(key, str) and key not in arguments:
                    missing.append(key)
        return sorted(set(missing))


def print_help() -> None:
    print(
        textwrap.dedent(
            """
            Commands:
              /help                  Show this help
              /tools                 List Unity MCP tools
              /call NAME {json}      Call a Unity MCP tool directly
              /sessions              List saved sessions
              /load SESSION_ID       Load a saved session
              /new                   Start a fresh session
              /config                Show active endpoints
              /quit                  Save and exit
            """
        ).strip()
    )


def parse_cli_arguments(value: str) -> dict[str, Any]:
    if value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--arguments must be a JSON object.")
    return parsed


class CliApp:
    def __init__(self, config: Config, store: SessionStore) -> None:
        self.config = config
        self.store = store
        self.session_id = store.new_id()
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": config.system_prompt}]
        self.mcp = McpHttpClient(config.mcp_url, config.request_timeout)
        self.llama = LlamaClient(config.llama_base_url, config.llama_model, config.request_timeout, config.temperature)
        self.mcp_tools: list[dict[str, Any]] = []
        self.openai_tools: list[dict[str, Any]] = []
        self.validator = ToolValidator([])
        self.tools_api_available = True

    def start(self) -> None:
        self.mcp.initialize()
        self.refresh_tools()
        print(f"Unity MCP tools: {len(self.mcp_tools)}")
        print("Type /help for commands. Type /quit to exit.")
        while True:
            try:
                user_input = input("\nuser> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self.save()
                return
            if not user_input:
                continue
            if user_input.startswith("/"):
                if self.handle_command(user_input):
                    return
                continue
            self.messages.append({"role": "user", "content": user_input})
            try:
                self.run_turn()
                self.save()
            except Exception as exc:
                print(f"error: {exc}")

    def refresh_tools(self) -> None:
        self.mcp_tools = self.mcp.list_tools()
        self.openai_tools = to_openai_tools(self.mcp_tools)
        self.validator = ToolValidator(self.mcp_tools)

    def run_turn(self) -> None:
        invalid_tool_calls: dict[str, int] = {}
        execute_code_failures = 0
        user_text = last_user_text(self.messages)
        for _ in range(self.config.max_tool_rounds):
            try:
                message = self.llama.chat(self.messages, self.openai_tools if self.tools_api_available else [])
            except RuntimeError as exc:
                if not self.tools_api_available or not self.openai_tools:
                    raise
                self.tools_api_available = False
                print(f"note: tools API failed, retrying with JSON tool fallback: {exc}")
                fallback_messages = self.messages + [{"role": "system", "content": self.fallback_tool_prompt()}]
                message = self.llama.chat(fallback_messages, [])
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                self.messages.append(message)
                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    name = function.get("name")
                    args_text = function.get("arguments") or "{}"
                    arguments = json.loads(args_text) if isinstance(args_text, str) else args_text
                    original_arguments = arguments
                    arguments = normalize_tool_arguments(arguments, name, self.validator.schema_for(name))
                    arguments, execute_repair_note = repair_execute_code_arguments(arguments)
                    arguments, repair_note = repair_tool_call_from_user_text(name, arguments, user_text)
                    print(f"tool> {name} {json.dumps(arguments, ensure_ascii=False)}")
                    if arguments != original_arguments:
                        print(f"tool normalized> {json.dumps(original_arguments, ensure_ascii=False)} -> {json.dumps(arguments, ensure_ascii=False)}")
                    if execute_repair_note:
                        print(f"tool repaired> {execute_repair_note}")
                    if repair_note:
                        print(f"tool repaired> {repair_note}")
                    validation_error = self.validator.validate(name, arguments)
                    if validation_error:
                        print(f"tool validation> {validation_error}")
                        signature = tool_call_signature(name, arguments)
                        invalid_tool_calls[signature] = invalid_tool_calls.get(signature, 0) + 1
                        self.messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "name": name or "invalid_tool",
                                "content": self.validation_feedback(validation_error, invalid_tool_calls[signature]),
                            }
                        )
                        continue
                    try:
                        result = self.mcp.call_tool(name, arguments)
                    except Exception as exc:
                        result = {"success": False, "message": str(exc), "error": type(exc).__name__}
                    print(f"tool result> {summarize_tool_result(result)}")
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "name": name,
                            "content": tool_result_to_text(result),
                        }
                    )
                    if name == "execute_code" and is_tool_result_failure(result):
                        execute_code_failures += 1
                        self.messages.append(
                            {
                                "role": "user",
                                "content": self.execute_code_failure_feedback(result, execute_code_failures),
                            }
                        )
                continue

            content = message.get("content") or ""
            fallback = parse_json_tool_request(content)
            if fallback:
                name, arguments = fallback
                original_arguments = arguments
                arguments = normalize_tool_arguments(arguments, name, self.validator.schema_for(name))
                arguments, execute_repair_note = repair_execute_code_arguments(arguments)
                arguments, repair_note = repair_tool_call_from_user_text(name, arguments, user_text)
                self.messages.append({"role": "assistant", "content": content})
                print(f"tool> {name} {json.dumps(arguments, ensure_ascii=False)}")
                if arguments != original_arguments:
                    print(f"tool normalized> {json.dumps(original_arguments, ensure_ascii=False)} -> {json.dumps(arguments, ensure_ascii=False)}")
                if execute_repair_note:
                    print(f"tool repaired> {execute_repair_note}")
                if repair_note:
                    print(f"tool repaired> {repair_note}")
                validation_error = self.validator.validate(name, arguments)
                if validation_error:
                    print(f"tool validation> {validation_error}")
                    signature = tool_call_signature(name, arguments)
                    invalid_tool_calls[signature] = invalid_tool_calls.get(signature, 0) + 1
                    self.messages.append(
                        {
                            "role": "user",
                            "content": self.validation_feedback(validation_error, invalid_tool_calls[signature]),
                        }
                    )
                    continue
                try:
                    result = self.mcp.call_tool(name, arguments)
                except Exception as exc:
                    result = {"success": False, "message": str(exc), "error": type(exc).__name__}
                print(f"tool result> {summarize_tool_result(result)}")
                self.messages.append(
                    {
                        "role": "user",
                        "content": "Tool result:\n" + tool_result_to_text(result),
                    }
                )
                if name == "execute_code" and is_tool_result_failure(result):
                    execute_code_failures += 1
                    self.messages.append(
                        {
                            "role": "user",
                            "content": self.execute_code_failure_feedback(result, execute_code_failures),
                        }
                    )
                continue

            self.messages.append({"role": "assistant", "content": content})
            print(f"\nassistant> {content}")
            return
        content = (
            "Tool loop limit reached. I returned the latest tool errors/results to the model, "
            "but it did not finish within max_tool_rounds. Try increasing max_tool_rounds or narrowing the request."
        )
        self.messages.append({"role": "assistant", "content": content})
        print(f"assistant> {content}")

    def validation_feedback(self, validation_error: str, repeat_count: int = 1) -> str:
        repeat_note = (
            f"\nThis exact invalid tool call has appeared {repeat_count} times. "
            "Change the tool, action, or argument types before trying again."
            if repeat_count > 1
            else ""
        )
        return (
            "Tool call rejected before execution.\n"
            f"{validation_error}\n"
            "Do not repeat the same rejected tool call. Choose a valid argument value, "
            "or ask the user one concise clarifying question."
            f"{repeat_note}"
        )

    def execute_code_failure_feedback(self, result: Any, failure_count: int = 1) -> str:
        return (
            "The execute_code tool failed. Do not repeat the same code.\n"
            "Inspect the error, fix the snippet, and continue with the smallest useful next tool call.\n"
            f"execute_code failure count this turn: {failure_count}.\n"
            "Tool result:\n"
            f"{summarize_tool_result(result)}"
        )

    def fallback_tool_prompt(self) -> str:
        lines = [
            "The chat-completions tools API is unavailable. If a Unity MCP tool is needed,",
            'reply with only JSON: {"tool":"tool_name","arguments":{...}}.',
            'Do not use placeholder strings like "null" for required arguments.',
            "Available tool names:",
        ]
        for tool in self.mcp_tools:
            description = (tool.get("description") or "").replace("\n", " ")
            lines.append(f"- {tool.get('name')}: {description[:160]}")
        return "\n".join(lines)

    def handle_command(self, command: str) -> bool:
        name, _, rest = command.partition(" ")
        if name == "/help":
            print_help()
        elif name == "/tools":
            self.refresh_tools()
            for tool in self.mcp_tools:
                print(f"- {tool.get('name')}: {tool.get('description', '')}")
        elif name == "/call":
            self.direct_call(rest)
        elif name == "/sessions":
            for session in self.store.list():
                print(f"{session['id']}  {session['updated_at']}  {session['title']}")
        elif name == "/load":
            self.messages = self.store.load(rest.strip())
            self.session_id = rest.strip()
            print(f"Loaded session {self.session_id}")
        elif name == "/new":
            self.session_id = self.store.new_id()
            self.messages = [{"role": "system", "content": self.config.system_prompt}]
            print(f"New session {self.session_id}")
        elif name == "/config":
            print(f"llama: {self.config.llama_base_url} model={self.config.llama_model}")
            print(f"mcp:   {self.config.mcp_url}")
            print(f"save:  {self.store.root}")
        elif name == "/quit":
            self.save()
            return True
        else:
            print("Unknown command. Type /help.")
        return False

    def direct_call(self, rest: str) -> None:
        tool_name, _, args_text = rest.strip().partition(" ")
        if not tool_name:
            print("Usage: /call NAME {json}")
            return
        arguments = json.loads(args_text) if args_text.strip() else {}
        arguments = normalize_tool_arguments(arguments, tool_name, self.validator.schema_for(tool_name))
        arguments, _ = repair_execute_code_arguments(arguments)
        validation_error = self.validator.validate(tool_name, arguments)
        if validation_error:
            print(f"tool validation> {validation_error}")
            return
        result = self.mcp.call_tool(tool_name, arguments)
        print(tool_result_to_text(result))

    def save(self) -> None:
        self.store.save(self.session_id, self.messages, self.config)
        print(f"Saved session {self.session_id}")


def create_default_config(path: Path) -> None:
    if path.exists():
        return
    path.write_text(
        json.dumps(
            {
                "llama_base_url": "http://127.0.0.1:8081/v1/",
                "llama_model": "local-model",
                "mcp_url": "http://127.0.0.1:8080/mcp",
                "temperature": 0.2,
                "request_timeout": 120,
                "max_tool_rounds": 8,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Lightweight llama-server to Unity MCP CLI bridge.")
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "mcp-client.config.json")
    parser.add_argument("--session-dir", type=Path, default=SESSIONS_DIR / "mcp")
    parser.add_argument("--init-config", action="store_true", help="Write a default config file and exit.")
    parser.add_argument("--list-tools", action="store_true", help="Connect to Unity MCP, list tools, and exit.")
    parser.add_argument("--call-tool", help="Connect to Unity MCP, call one tool, and exit.")
    parser.add_argument("--arguments", default="{}", help="JSON arguments for --call-tool.")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return 0

    config = Config.load(args.config)
    if args.list_tools or args.call_tool:
        mcp = McpHttpClient(config.mcp_url, config.request_timeout)
        mcp.initialize()
        if args.list_tools:
            for tool in mcp.list_tools():
                print(f"- {tool.get('name')}: {tool.get('description', '')}")
            return 0
        arguments = parse_cli_arguments(args.arguments)
        tools = mcp.list_tools()
        validator = ToolValidator(tools)
        arguments = normalize_tool_arguments(arguments, args.call_tool, validator.schema_for(args.call_tool))
        arguments, _ = repair_execute_code_arguments(arguments)
        validation_error = validator.validate(args.call_tool, arguments)
        if validation_error:
            print(f"tool validation> {validation_error}")
            return 1
        print(tool_result_to_text(mcp.call_tool(args.call_tool, arguments)))
        return 0

    store = SessionStore(args.session_dir)
    app = CliApp(config, store)
    app.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
