#!/usr/bin/env python3
"""Clickable Textual UI for the local LLM Unity MCP client."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from local_llm_clients import CONFIG_DIR, SESSIONS_DIR

try:
    from textual import events, work
    from textual.app import App, ComposeResult
    from textual.containers import Vertical, VerticalScroll
    from textual.message import Message
    from textual.widgets import Collapsible, Footer, Header, Markdown, Static, TextArea
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise SystemExit("Textual is required: py -3 -m pip install textual") from exc
    raise

from local_llm_clients.agent.cli import reasoning_text
from local_llm_clients.mcp.unity import (
    Config,
    LlamaClient,
    McpHttpClient,
    SessionStore,
    ToolValidator,
    create_default_config,
    is_tool_result_failure,
    last_user_text,
    normalize_tool_arguments,
    parse_cli_arguments,
    parse_json_tool_request,
    repair_execute_code_arguments,
    repair_tool_call_from_user_text,
    summarize_tool_result,
    to_openai_tools,
    tool_call_signature,
    tool_result_to_text,
)


HELP_TEXT = """### Commands

- `/help`, `/tools`, `/sessions`, `/new`, `/config`, `/quit`
- `/call NAME {json}`, `/load SESSION_ID`

Enter submits. Ctrl+J inserts a newline.
Click a Reasoning, Tool call, or Tool result header to expand or collapse it.
"""


class PromptTextArea(TextArea):
    """Multiline prompt where Enter submits and modified Enter inserts a newline."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    async def on_key(self, event: events.Key) -> None:
        aliases = set(event.aliases)
        if event.key == "shift+enter" or "shift+enter" in aliases or "newline" in aliases:
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))


class McpTextualApp(App[None]):
    TITLE = "Local LLM Unity MCP Client"
    SUB_TITLE = "Textual interface"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear_view", "Clear view"),
        ("escape", "focus_input", "Input"),
    ]
    CSS = """
    Screen { background: #0d1117; color: #d8dee9; }
    #status { height: 1; padding: 0 2; background: #161b22; color: #8b949e; }
    #conversation { height: 1fr; padding: 1 2; scrollbar-color: #3b82f6; }
    .message { width: 100%; height: auto; margin: 0 0 1 0; padding: 1 2; border: round #30363d; }
    .user-message { background: #172033; border: round #3b82f6; }
    .assistant-message { background: #12221b; border: round #3fb950; }
    .system-message { background: #161b22; border: round #6e7681; color: #b1bac4; }
    Collapsible { width: 100%; height: auto; margin: 0 0 1 0; padding: 0 1; }
    .reason-block { background: #151b2b; border: round #8b5cf6; color: #c4b5fd; }
    .tool-call-block { background: #271f12; border: round #d29922; color: #f2cc60; }
    .tool-result-block { background: #10251c; border: round #2ea043; color: #7ee787; }
    .tool-error-block { background: #2b1618; border: round #f85149; color: #ff7b72; }
    .block-content { width: 100%; height: auto; padding: 1 2; }
    .nested-blocks { width: 100%; height: auto; padding: 0 0 0 2; }
    #prompt { dock: bottom; height: 6; margin: 0 1 1 1; border: tall #3b82f6; background: #161b22; }
    #prompt:focus { border: tall #58a6ff; }
    """

    def __init__(self, config: Config, store: SessionStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.session_id = store.new_id()
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": config.system_prompt}]
        self.mcp = McpHttpClient(config.mcp_url, config.request_timeout)
        self.llama = LlamaClient(
            config.llama_base_url,
            config.llama_model,
            config.request_timeout,
            config.temperature,
        )
        self.mcp_tools: list[dict[str, Any]] = []
        self.openai_tools: list[dict[str, Any]] = []
        self.validator = ToolValidator([])
        self.tools_api_available = True
        self.mcp_ready = False
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield VerticalScroll(id="conversation")
        yield PromptTextArea(
            placeholder="Message or /command (Enter to send, Ctrl+J for newline)",
            id="prompt",
            show_line_numbers=False,
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.update_status("Connecting...")
        await self.add_markdown(
            "**Local LLM Unity MCP Client**  \n"
            f"MCP endpoint: `{self.config.mcp_url}`",
            "system-message",
        )
        self.query_one("#prompt", PromptTextArea).focus()
        self.connect_mcp()

    @work(exclusive=True, group="mcp-connect")
    async def connect_mcp(self) -> None:
        self.set_busy(True, "Connecting...")
        try:
            await asyncio.to_thread(self.mcp.initialize)
            await asyncio.to_thread(self.refresh_tools)
            self.mcp_ready = True
            await self.add_plain(
                f"Connected. Unity MCP tools: {len(self.mcp_tools)}",
                "system-message",
                "MCP",
            )
        except Exception as exc:
            await self.add_plain(str(exc), "tool-error-block", "MCP connection error")
        finally:
            self.set_busy(False, "Ready" if self.mcp_ready else "Disconnected")

    def refresh_tools(self) -> None:
        self.mcp_tools = self.mcp.list_tools()
        self.openai_tools = to_openai_tools(self.mcp_tools)
        self.validator = ToolValidator(self.mcp_tools)

    async def on_prompt_text_area_submitted(self, event: PromptTextArea.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return
        self.query_one("#prompt", PromptTextArea).load_text("")
        if self.busy:
            await self.add_plain("A response is already running.", "system-message", "Busy")
        elif text.startswith("/"):
            await self.handle_command(text)
        else:
            await self.add_plain(text, "user-message", "You")
            self.messages.append({"role": "user", "content": text})
            self.run_mcp_turn()

    @work(exclusive=True, group="llama-turn")
    async def run_mcp_turn(self) -> None:
        self.set_busy(True, "Thinking...")
        invalid_calls: dict[str, int] = {}
        execute_code_failures = 0
        user_text = last_user_text(self.messages)
        try:
            if not self.mcp_ready:
                await asyncio.to_thread(self.mcp.initialize)
                await asyncio.to_thread(self.refresh_tools)
                self.mcp_ready = True

            for _ in range(self.config.max_tool_rounds):
                try:
                    message = await asyncio.to_thread(
                        self.llama.chat,
                        self.messages,
                        self.openai_tools if self.tools_api_available else [],
                    )
                except RuntimeError as exc:
                    if not self.tools_api_available or not self.openai_tools:
                        raise
                    self.tools_api_available = False
                    await self.add_plain(
                        f"Tools API failed; using JSON fallback.\n{exc}",
                        "system-message",
                        "Fallback",
                    )
                    fallback_messages = self.messages + [
                        {"role": "system", "content": self.fallback_tool_prompt()}
                    ]
                    message = await asyncio.to_thread(self.llama.chat, fallback_messages, [])

                reasoning = reasoning_text(message)
                reasoning_block: Collapsible | None = None
                if reasoning:
                    reasoning_block = await self.add_collapsible(
                        reasoning,
                        f"Reasoning | {self.line_label(reasoning)}",
                        "reason-block",
                        False,
                    )

                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    self.messages.append(message)
                    for tool_call in tool_calls:
                        if await self.execute_tool_call(
                            tool_call,
                            invalid_calls,
                            execute_code_failures,
                            user_text,
                            False,
                            reasoning_block,
                        ):
                            execute_code_failures += 1
                    continue

                content = message.get("content") or ""
                fallback = parse_json_tool_request(content)
                if fallback:
                    self.messages.append(message)
                    name, arguments = fallback
                    if await self.execute_fallback_tool_call(
                        name,
                        arguments,
                        invalid_calls,
                        execute_code_failures,
                        user_text,
                        reasoning_block,
                    ):
                        execute_code_failures += 1
                    continue

                self.messages.append(message)
                await self.add_markdown(content or "_(No content returned)_", "assistant-message")
                return

            content = (
                "Tool loop limit reached. Try increasing max_tool_rounds or narrowing the request."
            )
            self.messages.append({"role": "assistant", "content": content})
            await self.add_plain(content, "tool-error-block", "Stopped")
        except Exception as exc:
            await self.add_plain(str(exc), "tool-error-block", "Error")
        finally:
            self.save()
            self.set_busy(False, "Ready" if self.mcp_ready else "Disconnected")

    async def execute_tool_call(
        self,
        tool_call: dict[str, Any],
        invalid_calls: dict[str, int],
        execute_code_failures: int,
        user_text: str,
        fallback_mode: bool,
        parent: Collapsible | None = None,
    ) -> bool:
        function = tool_call.get("function", {})
        name = function.get("name")
        raw_args = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as exc:
            arguments = {"_invalid_json": str(exc), "_raw": raw_args}
        return await self.execute_tool(
            name,
            arguments,
            invalid_calls,
            execute_code_failures,
            user_text,
            tool_call.get("id"),
            fallback_mode,
            parent,
        )

    async def execute_fallback_tool_call(
        self,
        name: str,
        arguments: Any,
        invalid_calls: dict[str, int],
        execute_code_failures: int,
        user_text: str,
        parent: Collapsible | None = None,
    ) -> bool:
        return await self.execute_tool(
            name,
            arguments,
            invalid_calls,
            execute_code_failures,
            user_text,
            None,
            True,
            parent,
        )

    async def execute_tool(
        self,
        name: str | None,
        arguments: Any,
        invalid_calls: dict[str, int],
        execute_code_failures: int,
        user_text: str,
        tool_call_id: str | None,
        fallback_mode: bool,
        parent: Collapsible | None = None,
    ) -> bool:
        original_arguments = arguments
        arguments = normalize_tool_arguments(arguments, name, self.validator.schema_for(name))
        arguments, execute_repair_note = repair_execute_code_arguments(arguments)
        arguments, repair_note = repair_tool_call_from_user_text(name, arguments, user_text)

        details = json.dumps(arguments, ensure_ascii=False, indent=2)
        if arguments != original_arguments:
            details += "\n\nNormalized from:\n" + json.dumps(
                original_arguments,
                ensure_ascii=False,
                indent=2,
            )
        if execute_repair_note:
            details += f"\n\nRepaired: {execute_repair_note}"
        if repair_note:
            details += f"\n\nRepaired: {repair_note}"
        await self.add_collapsible(
            details,
            f"Tool call | {name or 'unknown'}",
            "tool-call-block",
            True,
            parent,
        )

        validation_error = self.validator.validate(name, arguments)
        if validation_error:
            signature = tool_call_signature(name, arguments)
            invalid_calls[signature] = invalid_calls.get(signature, 0) + 1
            await self.add_collapsible(
                validation_error,
                f"Rejected | {name or 'unknown'}",
                "tool-error-block",
                False,
                parent,
            )
            feedback = self.validation_feedback(validation_error, invalid_calls[signature])
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
            return False

        try:
            result = await asyncio.to_thread(self.mcp.call_tool, name, arguments)
        except Exception as exc:
            result = {"success": False, "message": str(exc), "error": type(exc).__name__}
        result_text = tool_result_to_text(result)
        failed = is_tool_result_failure(result)
        await self.add_collapsible(
            result_text,
            f"{'Failed' if failed else 'Tool result'} | {name} | {self.line_label(result_text)}",
            "tool-error-block" if failed else "tool-result-block",
            True,
            parent,
        )

        if fallback_mode:
            self.messages.append({"role": "user", "content": "Tool result:\n" + result_text})
        else:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id or f"call_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "content": result_text,
                }
            )

        if name == "execute_code" and failed:
            self.messages.append(
                {
                    "role": "user",
                    "content": self.execute_code_failure_feedback(
                        result,
                        execute_code_failures + 1,
                    ),
                }
            )
            return True
        return False

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

    async def handle_command(self, command: str) -> None:
        name, _, rest = command.partition(" ")
        rest = rest.strip()
        try:
            if name == "/help":
                await self.add_markdown(HELP_TEXT, "system-message")
            elif name == "/tools":
                await asyncio.to_thread(self.refresh_tools)
                text = "\n".join(
                    f"- {tool.get('name')}: {tool.get('description', '')}" for tool in self.mcp_tools
                )
                await self.add_collapsible(
                    text,
                    f"Tools | {len(self.mcp_tools)} available",
                    "tool-call-block",
                    False,
                )
                self.update_status("Ready")
            elif name == "/call":
                await self.direct_call(rest)
            elif name == "/sessions":
                sessions = self.store.list()
                text = "\n".join(
                    f"{item['id']}  {item['updated_at']}  {item['title']}" for item in sessions
                )
                await self.add_plain(text or "No saved sessions.", "system-message", "Sessions")
            elif name == "/load":
                if not rest:
                    raise ValueError("Usage: /load SESSION_ID")
                self.messages, self.session_id = self.store.load(rest), rest
                await self.render_loaded_session()
            elif name == "/new":
                self.save()
                self.session_id = self.store.new_id()
                self.messages = [{"role": "system", "content": self.config.system_prompt}]
                await self.clear_conversation()
                await self.add_plain("Fresh session started.", "system-message", "New session")
                self.update_status("Ready")
            elif name == "/config":
                await self.add_plain(
                    f"llama: {self.config.llama_base_url}\n"
                    f"model: {self.config.llama_model}\n"
                    f"mcp:   {self.config.mcp_url}\n"
                    f"save:  {self.store.root}",
                    "system-message",
                    "Configuration",
                )
            elif name == "/multiline":
                await self.add_plain(
                    "Multiline input is enabled. Press Ctrl+J to insert a newline.",
                    "system-message",
                    "Multiline",
                )
            elif name == "/quit":
                self.action_quit()
            else:
                await self.add_plain("Unknown command. Use /help.", "tool-error-block", "Command")
        except Exception as exc:
            await self.add_plain(str(exc), "tool-error-block", "Command error")

    async def direct_call(self, rest: str) -> None:
        tool_name, _, args_text = rest.strip().partition(" ")
        if not tool_name:
            raise ValueError("Usage: /call NAME {json}")
        arguments = json.loads(args_text) if args_text.strip() else {}
        original_arguments = arguments
        arguments = normalize_tool_arguments(arguments, tool_name, self.validator.schema_for(tool_name))
        arguments, repair_note = repair_execute_code_arguments(arguments)
        details = json.dumps(arguments, ensure_ascii=False, indent=2)
        if arguments != original_arguments:
            details += "\n\nNormalized from:\n" + json.dumps(
                original_arguments,
                ensure_ascii=False,
                indent=2,
            )
        if repair_note:
            details += f"\n\nRepaired: {repair_note}"
        validation_error = self.validator.validate(tool_name, arguments)
        if validation_error:
            raise ValueError(validation_error)
        await self.add_collapsible(details, f"Tool call | {tool_name}", "tool-call-block", True)
        result = await asyncio.to_thread(self.mcp.call_tool, tool_name, arguments)
        text = tool_result_to_text(result)
        await self.add_collapsible(
            text,
            f"Tool result | {tool_name} | {self.line_label(text)}",
            "tool-error-block" if is_tool_result_failure(result) else "tool-result-block",
            True,
        )

    async def render_loaded_session(self) -> None:
        await self.clear_conversation()
        await self.add_plain(f"Loaded {self.session_id}", "system-message", "Session")
        reasoning_block: Collapsible | None = None
        for message in self.messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "user":
                reasoning_block = None
                await self.add_plain(content, "user-message", "You")
            elif role == "assistant":
                reasoning_block = None
                reason = reasoning_text(message)
                if reason:
                    reasoning_block = await self.add_collapsible(
                        reason,
                        f"Reasoning | {self.line_label(reason)}",
                        "reason-block",
                        False,
                    )
                for tool_call in message.get("tool_calls") or []:
                    function = tool_call.get("function", {})
                    tool_name = function.get("name") or "unknown"
                    arguments = function.get("arguments") or "{}"
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False, indent=2)
                    await self.add_collapsible(
                        arguments,
                        f"Tool call | {tool_name}",
                        "tool-call-block",
                        True,
                        reasoning_block,
                    )
                if content:
                    reasoning_block = None
                    await self.add_markdown(content, "assistant-message")
            elif role == "tool":
                await self.add_collapsible(
                    content,
                    f"Tool result | {message.get('name', 'tool')} | {self.line_label(content)}",
                    "tool-result-block",
                    True,
                    reasoning_block,
                )

    async def add_plain(self, text: str, css_class: str, title: str | None = None) -> None:
        widget = Static(text, markup=False, classes=f"message {css_class}")
        if title:
            widget.border_title = title
        await self.mount_in_conversation(widget)

    async def add_markdown(self, text: str, css_class: str) -> None:
        await self.mount_in_conversation(Markdown(text, classes=f"message {css_class}"))

    async def add_collapsible(
        self,
        text: str,
        title: str,
        css_class: str,
        collapsed: bool,
        parent: Collapsible | None = None,
    ) -> Collapsible:
        body = Static(text, markup=False, classes="block-content")
        nested_blocks = Vertical(classes="nested-blocks")
        block = Collapsible(body, nested_blocks, title=title, collapsed=collapsed, classes=css_class)
        block._nested_blocks = nested_blocks  # type: ignore[attr-defined]
        if parent is None:
            await self.mount_in_conversation(block)
        else:
            await self.mount_in_collapsible(parent, block)
        return block

    async def mount_in_conversation(self, widget: Static | Markdown | Collapsible) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.mount(widget)
        conversation.scroll_end(animate=False)

    async def mount_in_collapsible(self, parent: Collapsible, widget: Collapsible) -> None:
        nested_blocks = getattr(parent, "_nested_blocks", None)
        if nested_blocks is None:
            await parent.mount(widget)
        else:
            await nested_blocks.mount(widget)
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)

    async def clear_conversation(self) -> None:
        await self.query_one("#conversation", VerticalScroll).remove_children()

    def save(self) -> None:
        self.store.save(self.session_id, self.messages, self.config)

    def set_busy(self, busy: bool, state: str) -> None:
        self.busy = busy
        prompt = self.query_one("#prompt", PromptTextArea)
        prompt.disabled = busy
        if not busy:
            prompt.focus()
        self.update_status(state)

    def update_status(self, state: str) -> None:
        self.query_one("#status", Static).update(
            f"{state}  |  {self.config.llama_model}  |  {self.config.mcp_url}  |  {self.session_id}"
        )

    @staticmethod
    def line_label(text: str) -> str:
        count = max(len(text.splitlines()), 1)
        return f"{count} line{'s' if count != 1 else ''}"

    async def action_clear_view(self) -> None:
        await self.clear_conversation()

    def action_focus_input(self) -> None:
        self.query_one("#prompt", PromptTextArea).focus()

    def action_quit(self) -> None:
        self.save()
        self.exit()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Textual UI for the llama-server to Unity MCP bridge")
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "mcp-client.config.json")
    parser.add_argument("--session-dir", type=Path, default=SESSIONS_DIR / "mcp")
    parser.add_argument("--init-config", action="store_true")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--call-tool")
    parser.add_argument("--arguments", default="{}")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return 0

    config = Config.load(args.config)
    if args.list_tools or args.call_tool:
        mcp = McpHttpClient(config.mcp_url, config.request_timeout)
        mcp.initialize()
        tools = mcp.list_tools()
        if args.list_tools:
            for tool in tools:
                print(f"- {tool.get('name')}: {tool.get('description', '')}")
            return 0
        validator = ToolValidator(tools)
        arguments = parse_cli_arguments(args.arguments)
        arguments = normalize_tool_arguments(arguments, args.call_tool, validator.schema_for(args.call_tool))
        arguments, _ = repair_execute_code_arguments(arguments)
        validation_error = validator.validate(args.call_tool, arguments)
        if validation_error:
            print(f"tool validation> {validation_error}")
            return 1
        print(tool_result_to_text(mcp.call_tool(args.call_tool, arguments)))
        return 0

    McpTextualApp(config, SessionStore(args.session_dir)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
