#!/usr/bin/env python3
"""
Lightweight local-LLM file agent client.

Flow:
  .gguf -> llama-server(OpenAI compatible) -> this CLI -> local workspace files
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_unity_client import (
    LlamaClient,
    SessionStore,
    compact_json,
    normalize_tool_arguments,
    parse_json_tool_request,
    summarize_tool_result,
    tool_call_signature,
    tool_result_to_text,
)


DEFAULT_SYSTEM_PROMPT = """You are a local coding/file agent.
Use tools when you need to inspect or edit files in the active working directory.
The user can change the active working directory with /set_directory.
Always use relative paths in tool arguments. Never try to access paths outside the active working directory.
Before editing, read the relevant file and keep changes focused on the user's request.
If a requested edit is ambiguous, ask one concise clarifying question.
Never invent placeholder values such as "null", "none", "unknown", or empty strings for required tool arguments.
"""


@dataclass
class Config:
    llama_base_url: str = "http://127.0.0.1:8081/v1/"
    llama_model: str = "local-model"
    mcp_url: str = ""
    temperature: float = 0.2
    request_timeout: int = 120
    max_tool_rounds: int = 8
    max_invalid_tool_retries: int = 0
    workdir: str = "."
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
        return config


def env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def print_help() -> None:
    print(
        textwrap.dedent(
            """
            Commands:
              /help                         Show this help
              /set_directory PATH           Set the active working directory
              /pwd                          Show the active working directory
              /tools                        List local file tools
              /call NAME {json}             Call a local file tool directly
              /sessions                     List saved sessions
              /load SESSION_ID              Load a saved session
              /new                          Start a fresh session
              /config                       Show active config
              /quit                         Save and exit
            """
        ).strip()
    )


def create_default_config(path: Path) -> None:
    if path.exists():
        return
    path.write_text(
        json.dumps(
            {
                "llama_base_url": "http://127.0.0.1:8081/v1/",
                "llama_model": "local-model",
                "temperature": 0.2,
                "request_timeout": 120,
                "max_tool_rounds": 8,
                "max_invalid_tool_retries": 0,
                "workdir": ".",
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_cli_arguments(value: str) -> dict[str, Any]:
    if value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--arguments must be a JSON object.")
    return parsed


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
        if tool.get("name")
    ]


class LocalFileTools:
    def __init__(self, workdir: Path) -> None:
        self.workdir = self.set_workdir(workdir)

    def set_workdir(self, workdir: Path) -> Path:
        resolved = workdir.expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"Directory does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"Path is not a directory: {resolved}")
        self.workdir = resolved
        return resolved

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_files",
                "description": "List files and directories under the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path. Defaults to ."},
                        "recursive": {"type": "boolean", "description": "Whether to include nested files."},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                },
            },
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file from the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path."},
                        "start_line": {"type": "integer", "minimum": 1},
                        "line_count": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a UTF-8 text file in the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path."},
                        "content": {"type": "string", "description": "Full file content to write."},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "replace_text",
                "description": "Replace exact text in a UTF-8 text file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path."},
                        "old_text": {"type": "string", "description": "Exact text to replace."},
                        "new_text": {"type": "string", "description": "Replacement text."},
                        "count": {
                            "type": "integer",
                            "description": "Maximum replacements. Omit or set 0 to replace all.",
                            "minimum": 0,
                        },
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "append_file",
                "description": "Append UTF-8 text to a file in the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path."},
                        "content": {"type": "string", "description": "Text to append."},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "delete_file",
                "description": "Delete one file in the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative file path."}},
                    "required": ["path"],
                },
            },
            {
                "name": "make_directory",
                "description": "Create a directory under the active working directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative directory path."}},
                    "required": ["path"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        try:
            if name == "list_files":
                return self._ok(self.list_filesystem(args))
            if name == "read_file":
                return self._ok(self.read_file(args))
            if name == "write_file":
                return self._ok(self.write_file(args))
            if name == "replace_text":
                return self._ok(self.replace_text(args))
            if name == "append_file":
                return self._ok(self.append_file(args))
            if name == "delete_file":
                return self._ok(self.delete_file(args))
            if name == "make_directory":
                return self._ok(self.make_directory(args))
            return self._error(f"Unknown tool: {name}")
        except Exception as exc:
            return self._error(str(exc))

    def list_filesystem(self, args: dict[str, Any]) -> str:
        root = self.resolve_path(str(args.get("path") or "."), must_exist=True)
        if not root.is_dir():
            raise ValueError(f"Not a directory: {self.relative(root)}")
        recursive = bool(args.get("recursive", False))
        max_entries = int(args.get("max_entries") or 200)
        iterator = root.rglob("*") if recursive else root.iterdir()
        entries = []
        for item in sorted(iterator, key=lambda p: self.relative(p).lower()):
            suffix = "/" if item.is_dir() else ""
            entries.append(f"{self.relative(item)}{suffix}")
            if len(entries) >= max_entries:
                entries.append("... <truncated>")
                break
        return "\n".join(entries) if entries else "<empty>"

    def read_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=True)
        if not path.is_file():
            raise ValueError(f"Not a file: {self.relative(path)}")
        text = path.read_text(encoding="utf-8")
        start_line = args.get("start_line")
        line_count = args.get("line_count")
        if start_line is None and line_count is None:
            return text
        lines = text.splitlines()
        start = max(int(start_line or 1), 1) - 1
        end = start + int(line_count or 200)
        numbered = [f"{index + 1}: {line}" for index, line in enumerate(lines[start:end], start)]
        return "\n".join(numbered)

    def write_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=False)
        content = required_string(args, "content", allow_empty=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {self.relative(path)}"

    def replace_text(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=True)
        old_text = required_string(args, "old_text")
        new_text = required_string(args, "new_text", allow_empty=True)
        count = int(args.get("count") or 0)
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old_text)
        if occurrences == 0:
            raise ValueError(f"Text not found in {self.relative(path)}")
        replace_count = count if count > 0 else occurrences
        updated = text.replace(old_text, new_text, replace_count)
        path.write_text(updated, encoding="utf-8")
        return f"Replaced {min(occurrences, replace_count)} occurrence(s) in {self.relative(path)}"

    def append_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=False)
        content = required_string(args, "content", allow_empty=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return f"Appended {len(content)} characters to {self.relative(path)}"

    def delete_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=True)
        if not path.is_file():
            raise ValueError(f"Not a file: {self.relative(path)}")
        path.unlink()
        return f"Deleted {self.relative(path)}"

    def make_directory(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(required_string(args, "path"), must_exist=False)
        path.mkdir(parents=True, exist_ok=True)
        return f"Created directory {self.relative(path)}"

    def resolve_path(self, relative_path: str, must_exist: bool) -> Path:
        if not relative_path:
            raise ValueError("Path is required.")
        raw = Path(relative_path).expanduser()
        if raw.is_absolute():
            raise ValueError("Use a relative path inside the active working directory.")
        resolved = (self.workdir / raw).resolve()
        if not self.is_inside_workdir(resolved):
            raise ValueError("Path escapes the active working directory.")
        if must_exist and not resolved.exists():
            raise ValueError(f"Path does not exist: {self.relative(resolved)}")
        return resolved

    def is_inside_workdir(self, path: Path) -> bool:
        try:
            path.relative_to(self.workdir)
            return True
        except ValueError:
            return False

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workdir)).replace("\\", "/")
        except ValueError:
            return str(path)

    def _ok(self, text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}]}

    def _error(self, text: str) -> dict[str, Any]:
        return {"isError": True, "content": [{"type": "text", "text": text}]}


def required_string(args: dict[str, Any], key: str, allow_empty: bool = False) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    if not allow_empty and not value:
        raise ValueError(f"{key} must not be empty.")
    return value


class ToolValidator:
    BAD_PLACEHOLDERS = {"null", "none", "unknown", "undefined", "n/a", ""}
    EMPTY_STRING_OK_KEYS = {"content", "new_text"}

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = {tool["name"]: tool for tool in tools if tool.get("name")}

    def validate(self, name: str | None, arguments: Any) -> str | None:
        if not isinstance(name, str) or not name:
            return "Tool call is missing a tool name."
        tool = self.tools.get(name)
        if not tool:
            known = ", ".join(sorted(self.tools))
            return f"Unknown tool '{name}'. Available tools: {known}"
        if not isinstance(arguments, dict):
            return f"Arguments for tool '{name}' must be a JSON object."
        required = tool.get("inputSchema", {}).get("required", [])
        for key in required:
            if key not in arguments:
                return f"Missing required argument for {name}.{key}."
        for key, value in arguments.items():
            if key in self.EMPTY_STRING_OK_KEYS and value == "":
                continue
            if isinstance(value, str) and value.strip().lower() in self.BAD_PLACEHOLDERS:
                return f"Invalid placeholder value for {name}.{key}: {value!r}."
        return None


class AgentCliApp:
    def __init__(self, config: Config, store: SessionStore) -> None:
        self.config = config
        self.store = store
        self.session_id = store.new_id()
        self.file_tools = LocalFileTools(Path(config.workdir))
        self.tools = self.file_tools.list_tools()
        self.openai_tools = to_openai_tools(self.tools)
        self.validator = ToolValidator(self.tools)
        self.llama = LlamaClient(config.llama_base_url, config.llama_model, config.request_timeout, config.temperature)
        self.tools_api_available = True
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]

    def system_prompt(self) -> str:
        return f"{self.config.system_prompt}\nActive working directory: {self.file_tools.workdir}\n"

    def start(self) -> None:
        print(f"Agent file tools: {len(self.tools)}")
        print(f"Working directory: {self.file_tools.workdir}")
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

    def run_turn(self) -> None:
        invalid_tool_calls: dict[str, int] = {}
        for _ in range(self.config.max_tool_rounds):
            try:
                message = self.llama.chat(self.messages, self.openai_tools if self.tools_api_available else [])
            except RuntimeError as exc:
                if not self.tools_api_available:
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
                    arguments = normalize_tool_arguments(arguments, name)
                    print(f"tool> {name} {json.dumps(arguments, ensure_ascii=False)}")
                    if self.handle_tool_validation(name, arguments, invalid_tool_calls, tool_call.get("id")):
                        return
                    result = self.file_tools.call_tool(name, arguments)
                    print(f"tool result> {summarize_tool_result(result)}")
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "name": name,
                            "content": tool_result_to_text(result),
                        }
                    )
                continue

            content = message.get("content") or ""
            fallback = parse_json_tool_request(content)
            if fallback:
                name, arguments = fallback
                arguments = normalize_tool_arguments(arguments, name)
                self.messages.append({"role": "assistant", "content": content})
                print(f"tool> {name} {json.dumps(arguments, ensure_ascii=False)}")
                if self.handle_tool_validation(name, arguments, invalid_tool_calls, None, fallback_mode=True):
                    return
                result = self.file_tools.call_tool(name, arguments)
                print(f"tool result> {summarize_tool_result(result)}")
                self.messages.append({"role": "user", "content": "Tool result:\n" + tool_result_to_text(result)})
                continue

            self.messages.append({"role": "assistant", "content": content})
            print(f"\nassistant> {content}")
            return
        print("assistant> Tool loop limit reached. Try narrowing the request.")

    def handle_tool_validation(
        self,
        name: str | None,
        arguments: Any,
        invalid_tool_calls: dict[str, int],
        tool_call_id: str | None,
        fallback_mode: bool = False,
    ) -> bool:
        validation_error = self.validator.validate(name, arguments)
        if not validation_error:
            return False
        print(f"tool validation> {validation_error}")
        signature = tool_call_signature(name, arguments)
        invalid_tool_calls[signature] = invalid_tool_calls.get(signature, 0) + 1
        feedback = self.validation_feedback(validation_error)
        if fallback_mode:
            self.messages.append({"role": "user", "content": feedback})
        else:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id or f"call_{uuid.uuid4().hex[:8]}",
                    "name": name or "invalid_tool",
                    "content": feedback,
                }
            )
        if invalid_tool_calls[signature] > self.config.max_invalid_tool_retries:
            self.stop_repeated_invalid_tool_call(validation_error)
            return True
        return False

    def validation_feedback(self, validation_error: str) -> str:
        return (
            "Tool call rejected before execution.\n"
            f"{validation_error}\n"
            "Do not repeat the same rejected tool call. Choose valid arguments, "
            "or ask the user one concise clarifying question."
        )

    def stop_repeated_invalid_tool_call(self, validation_error: str) -> None:
        content = (
            "The invalid tool call was not executed, so this turn is stopping.\n"
            f"{validation_error}\n"
            "Please make the file operation more specific before trying again."
        )
        self.messages.append({"role": "assistant", "content": content})
        print(f"\nassistant> {content}")

    def fallback_tool_prompt(self) -> str:
        lines = [
            "The chat-completions tools API is unavailable. If a file tool is needed,",
            'reply with only JSON: {"tool":"tool_name","arguments":{...}}.',
            "Use only relative paths inside the active working directory.",
            'Do not use placeholder strings like "null" for required arguments.',
            "Available tool names:",
        ]
        for tool in self.tools:
            description = (tool.get("description") or "").replace("\n", " ")
            lines.append(f"- {tool.get('name')}: {description[:160]}")
        return "\n".join(lines)

    def handle_command(self, command: str) -> bool:
        name, _, rest = command.partition(" ")
        if name == "/help":
            print_help()
        elif name == "/set_directory":
            self.set_directory(rest.strip())
        elif name == "/pwd":
            print(self.file_tools.workdir)
        elif name == "/tools":
            for tool in self.tools:
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
            self.messages = [{"role": "system", "content": self.system_prompt()}]
            print(f"New session {self.session_id}")
        elif name == "/config":
            print(f"llama: {self.config.llama_base_url} model={self.config.llama_model}")
            print(f"workdir: {self.file_tools.workdir}")
            print(f"save: {self.store.root}")
        elif name == "/quit":
            self.save()
            return True
        else:
            print("Unknown command. Type /help.")
        return False

    def set_directory(self, value: str) -> None:
        if not value:
            print("Usage: /set_directory PATH")
            return
        workdir = self.file_tools.set_workdir(Path(value))
        self.messages.append({"role": "system", "content": f"Active working directory changed to: {workdir}"})
        print(f"Working directory: {workdir}")

    def direct_call(self, rest: str) -> None:
        tool_name, _, args_text = rest.strip().partition(" ")
        if not tool_name:
            print("Usage: /call NAME {json}")
            return
        arguments = json.loads(args_text) if args_text.strip() else {}
        validation_error = self.validator.validate(tool_name, arguments)
        if validation_error:
            print(f"tool validation> {validation_error}")
            return
        print(tool_result_to_text(self.file_tools.call_tool(tool_name, arguments)))

    def save(self) -> None:
        self.store.save(self.session_id, self.messages, self.config)  # type: ignore[arg-type]
        path = self.store.path_for(self.session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["workdir"] = str(self.file_tools.workdir)
        data["updated_at"] = now_iso()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved session {self.session_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight local-LLM file agent client")
    parser.add_argument("--config", type=Path, default=Path("agent-client.config.json"))
    parser.add_argument("--init-config", action="store_true", help="Write a default config file and exit.")
    parser.add_argument("--set-directory", type=Path, help="Set the initial active working directory.")
    parser.add_argument("--call-tool", help="Call a local file tool and exit.")
    parser.add_argument("--arguments", default="{}", help="JSON arguments for --call-tool, or @file.json")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return

    config = Config.load(args.config)
    if args.set_directory:
        config.workdir = str(args.set_directory)
    store = SessionStore(Path(".agent-client") / "sessions")

    if args.call_tool:
        tools = LocalFileTools(Path(config.workdir))
        arguments = parse_cli_arguments(args.arguments)
        print(tool_result_to_text(tools.call_tool(args.call_tool, arguments)))
        return

    AgentCliApp(config, store).start()


if __name__ == "__main__":
    main()
