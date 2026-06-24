#!/usr/bin/env python3
"""Clickable Textual UI for the local LLM file agent."""

from __future__ import annotations

import argparse
import asyncio
import json
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

from local_llm_clients.agent.cli import (
    Config, LocalFileTools, ToolValidator, create_default_config, now_iso,
    parse_cli_arguments, reasoning_text, to_openai_tools,
)
from local_llm_clients.common import LlamaClient, SessionStore
from local_llm_clients.mcp.unity import (
    normalize_tool_arguments, parse_json_tool_request,
    tool_call_signature, tool_result_to_text,
)

HELP_TEXT = """### Commands

- `/help`, `/pwd`, `/tools`, `/sessions`, `/new`, `/config`, `/quit`
- `/set_directory PATH`, `/call NAME {json}`, `/load SESSION_ID`

Enter submits. Shift+Enter inserts a newline.
Click a Reasoning, Tool call, or Tool result header to expand or collapse it.
"""


class PromptTextArea(TextArea):
    """Multiline prompt where Enter submits and Shift+Enter inserts a newline."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))


class AgentTextualApp(App[None]):
    TITLE = "Local LLM File Agent"
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
        self.config, self.store = config, store
        self.session_id = store.new_id()
        self.file_tools = LocalFileTools(Path(config.workdir), Path(config.allowed_tools_path))
        self.tools = self.file_tools.list_tools()
        self.openai_tools = to_openai_tools(self.tools)
        self.validator = ToolValidator(self.tools)
        self.llama = LlamaClient(config.llama_base_url, config.llama_model,
                                 config.request_timeout, config.temperature)
        self.tools_api_available = True
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield VerticalScroll(id="conversation")
        yield PromptTextArea(
            placeholder="Message or /command (Enter to send, Shift+Enter for newline)",
            id="prompt",
            show_line_numbers=False,
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.update_status("Ready")
        await self.add_markdown(
            "**Local LLM File Agent**  \n"
            f"Working directory: `{self.file_tools.workdir}`  \n"
            "Click Reasoning and Tool headers to expand them.", "system-message")
        self.query_one("#prompt", PromptTextArea).focus()

    def system_prompt(self) -> str:
        return (
            f"{self.config.system_prompt}\n"
            f"Active working directory: {self.file_tools.workdir}\n"
            f"Allowed command presets file: {self.file_tools.allowed_tools_path}\n"
            "Compile and exception-monitor workflow:\n"
            "- Use list_command_presets before running local compile/test commands.\n"
            "- Use run_command_preset only with preset names from allowed_tools.json; never invent shell commands.\n"
            "- When asked to fix compile or runtime errors, run a compile or exception_monitor preset first, inspect stdout/stderr, edit only implicated files, then run the same preset again.\n"
            "- Stop and report if the same error repeats or no allowed preset applies.\n"
        )

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
            self.run_agent_turn()

    @work(exclusive=True, group="llama-turn")
    async def run_agent_turn(self) -> None:
        self.set_busy(True, "Thinking…")
        invalid_calls: dict[str, int] = {}
        try:
            for _ in range(self.config.max_tool_rounds):
                try:
                    message = await asyncio.to_thread(
                        self.llama.chat, self.messages,
                        self.openai_tools if self.tools_api_available else [])
                except RuntimeError as exc:
                    if not self.tools_api_available:
                        raise
                    self.tools_api_available = False
                    await self.add_plain(
                        f"Tools API failed; using JSON fallback.\n{exc}",
                        "system-message", "Fallback")
                    fallback_messages = self.messages + [
                        {"role": "system", "content": self.fallback_tool_prompt()}]
                    message = await asyncio.to_thread(self.llama.chat, fallback_messages, [])

                reasoning = reasoning_text(message)
                reasoning_block: Collapsible | None = None
                if reasoning:
                    reasoning_block = await self.add_collapsible(
                        reasoning, f"Reasoning · {self.line_label(reasoning)}",
                        "reason-block", False)

                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    self.messages.append(message)
                    for tool_call in tool_calls:
                        function = tool_call.get("function", {})
                        name = function.get("name")
                        raw_args = function.get("arguments") or "{}"
                        try:
                            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError as exc:
                            arguments = {"_invalid_json": str(exc), "_raw": raw_args}
                        arguments = normalize_tool_arguments(arguments, name)
                        if await self.execute_tool(
                            name, arguments, invalid_calls, tool_call.get("id"), False,
                            reasoning_block):
                            return
                    continue

                content = message.get("content") or ""
                fallback = parse_json_tool_request(content)
                if fallback:
                    name, arguments = fallback
                    self.messages.append(message)
                    if await self.execute_tool(
                        name, normalize_tool_arguments(arguments, name),
                        invalid_calls, None, True, reasoning_block):
                        return
                    continue

                self.messages.append(message)
                await self.add_markdown(content or "_(No content returned)_", "assistant-message")
                return

            await self.add_plain("Tool loop limit reached.", "tool-error-block", "Stopped")
        except Exception as exc:
            await self.add_plain(str(exc), "tool-error-block", "Error")
        finally:
            self.save()
            self.set_busy(False, "Ready")

    async def execute_tool(
        self, name: str | None, arguments: Any, invalid_calls: dict[str, int],
        tool_call_id: str | None, fallback_mode: bool,
        parent: Collapsible | None = None,
    ) -> bool:
        await self.add_collapsible(
            json.dumps(arguments, ensure_ascii=False, indent=2),
            f"Tool call · {name or 'unknown'}", "tool-call-block", True, parent)
        error = self.validator.validate(name, arguments)
        if error:
            await self.add_collapsible(
                error, f"Rejected · {name or 'unknown'}", "tool-error-block", False, parent)
            signature = tool_call_signature(name, arguments)
            invalid_calls[signature] = invalid_calls.get(signature, 0) + 1
            feedback = self.validation_feedback(error)
            if fallback_mode:
                self.messages.append({"role": "user", "content": feedback})
            else:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id or f"call_{uuid.uuid4().hex[:8]}",
                    "name": name or "invalid_tool", "content": feedback,
                })
            if invalid_calls[signature] > self.config.max_invalid_tool_retries:
                content = f"The invalid tool call was not executed.\n\n{error}"
                self.messages.append({"role": "assistant", "content": content})
                await self.add_markdown(content, "assistant-message")
                return True
            return False

        result = await asyncio.to_thread(self.file_tools.call_tool, name, arguments)
        result_text = tool_result_to_text(result)
        is_error = bool(result.get("isError")) if isinstance(result, dict) else False
        await self.add_collapsible(
            result_text,
            f"{'Failed' if is_error else 'Tool result'} · {name} · {self.line_label(result_text)}",
            "tool-error-block" if is_error else "tool-result-block", True, parent)
        if fallback_mode:
            self.messages.append({"role": "user", "content": "Tool result:\n" + result_text})
        else:
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id or f"call_{uuid.uuid4().hex[:8]}",
                "name": name, "content": result_text,
            })
        return False

    def validation_feedback(self, error: str) -> str:
        return (
            "Tool call rejected before execution.\n" + error +
            "\nDo not repeat it. Choose valid arguments or ask a clarifying question.")

    def fallback_tool_prompt(self) -> str:
        lines = [
            "The tools API is unavailable. If a file tool is needed, reply with only JSON:",
            '{"tool":"tool_name","arguments":{...}}',
            "Use relative paths. Do not use placeholder values. Available tools:",
        ]
        lines.extend(f"- {tool['name']}: {tool.get('description', '')[:160]}" for tool in self.tools)
        return "\n".join(lines)

    async def handle_command(self, command: str) -> None:
        name, _, rest = command.partition(" ")
        rest = rest.strip()
        try:
            if name == "/help":
                await self.add_markdown(HELP_TEXT, "system-message")
            elif name == "/set_directory":
                if not rest:
                    raise ValueError("Usage: /set_directory PATH")
                workdir = self.file_tools.set_workdir(Path(rest))
                self.messages.append({
                    "role": "system", "content": f"Active working directory changed to: {workdir}"})
                await self.add_plain(str(workdir), "system-message", "Working directory")
                self.update_status("Ready")
            elif name == "/pwd":
                await self.add_plain(str(self.file_tools.workdir), "system-message", "Working directory")
            elif name == "/tools":
                text = "\n".join(
                    f"• {tool['name']}: {tool.get('description', '')}" for tool in self.tools)
                await self.add_collapsible(
                    text, f"Tools · {len(self.tools)} available", "tool-call-block", False)
            elif name == "/call":
                await self.direct_call(rest)
            elif name == "/sessions":
                sessions = self.store.list()
                text = "\n".join(
                    f"{item['id']}  {item['updated_at']}  {item['title']}" for item in sessions)
                await self.add_plain(text or "No saved sessions.", "system-message", "Sessions")
            elif name == "/load":
                if not rest:
                    raise ValueError("Usage: /load SESSION_ID")
                self.messages, self.session_id = self.store.load(rest), rest
                await self.render_loaded_session()
            elif name == "/new":
                self.save()
                self.session_id = self.store.new_id()
                self.messages = [{"role": "system", "content": self.system_prompt()}]
                await self.clear_conversation()
                await self.add_plain("Fresh session started.", "system-message", "New session")
                self.update_status("Ready")
            elif name == "/config":
                await self.add_plain(
                    f"llama: {self.config.llama_base_url}\nmodel: {self.config.llama_model}\n"
                    f"workdir: {self.file_tools.workdir}\nsave: {self.store.root}",
                    "system-message", "Configuration")
            elif name == "/multiline":
                await self.add_plain(
                    "Multiline input is enabled. Press Shift+Enter to insert a newline.",
                    "system-message", "Multiline")
            elif name == "/quit":
                self.action_quit()
            else:
                await self.add_plain("Unknown command. Use /help.", "tool-error-block", "Command")
        except Exception as exc:
            await self.add_plain(str(exc), "tool-error-block", "Command error")

    async def direct_call(self, rest: str) -> None:
        name, _, args_text = rest.partition(" ")
        if not name:
            raise ValueError("Usage: /call NAME {json}")
        arguments = json.loads(args_text) if args_text.strip() else {}
        error = self.validator.validate(name, arguments)
        if error:
            raise ValueError(error)
        await self.add_collapsible(
            json.dumps(arguments, ensure_ascii=False, indent=2),
            f"Tool call · {name}", "tool-call-block", True)
        result = await asyncio.to_thread(self.file_tools.call_tool, name, arguments)
        text = tool_result_to_text(result)
        await self.add_collapsible(
            text, f"Tool result · {name} · {self.line_label(text)}",
            "tool-error-block" if result.get("isError") else "tool-result-block", True)

    async def render_loaded_session(self) -> None:
        await self.clear_conversation()
        await self.add_plain(f"Loaded {self.session_id}", "system-message", "Session")
        reasoning_block: Collapsible | None = None
        for message in self.messages:
            role, content = message.get("role"), str(message.get("content") or "")
            if role == "user":
                reasoning_block = None
                await self.add_plain(content, "user-message", "You")
            elif role == "assistant":
                reasoning_block = None
                reason = reasoning_text(message)
                if reason:
                    reasoning_block = await self.add_collapsible(
                        reason, f"Reasoning · {self.line_label(reason)}", "reason-block", False)
                tool_calls = message.get("tool_calls") or []
                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    name = function.get("name") or "unknown"
                    arguments = function.get("arguments") or "{}"
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False, indent=2)
                    await self.add_collapsible(
                        arguments, f"Tool call · {name}", "tool-call-block", True,
                        reasoning_block)
                if content:
                    reasoning_block = None
                    await self.add_markdown(content, "assistant-message")
            elif role == "tool":
                await self.add_collapsible(
                    content, f"Tool result · {message.get('name', 'tool')} · {self.line_label(content)}",
                    "tool-result-block", True, reasoning_block)

    async def add_plain(self, text: str, css_class: str, title: str | None = None) -> None:
        widget = Static(text, markup=False, classes=f"message {css_class}")
        if title:
            widget.border_title = title
        await self.mount_in_conversation(widget)

    async def add_markdown(self, text: str, css_class: str) -> None:
        await self.mount_in_conversation(Markdown(text, classes=f"message {css_class}"))

    async def add_collapsible(
        self, text: str, title: str, css_class: str, collapsed: bool,
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
        self.store.save(self.session_id, self.messages, self.config)  # type: ignore[arg-type]
        path = self.store.path_for(self.session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["workdir"], data["updated_at"] = str(self.file_tools.workdir), now_iso()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_busy(self, busy: bool, state: str) -> None:
        self.busy = busy
        prompt = self.query_one("#prompt", PromptTextArea)
        prompt.disabled = busy
        if not busy:
            prompt.focus()
        self.update_status(state)

    def update_status(self, state: str) -> None:
        self.query_one("#status", Static).update(
            f"{state}  •  {self.config.llama_model}  •  {self.file_tools.workdir}  •  {self.session_id}")

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual UI for the local-LLM file agent")
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "agent-client.config.json")
    parser.add_argument("--init-config", action="store_true")
    parser.add_argument("--set-directory", type=Path)
    parser.add_argument("--call-tool")
    parser.add_argument("--arguments", default="{}")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return
    config = Config.load(args.config)
    if args.set_directory:
        config.workdir = str(args.set_directory)
    if args.call_tool:
        tools = LocalFileTools(Path(config.workdir), Path(config.allowed_tools_path))
        arguments = parse_cli_arguments(args.arguments)
        print(tool_result_to_text(tools.call_tool(args.call_tool, arguments)))
        return
    AgentTextualApp(config, SessionStore(SESSIONS_DIR / "agent")).run()


if __name__ == "__main__":
    main()
