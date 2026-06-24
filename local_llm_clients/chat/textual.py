#!/usr/bin/env python3
"""Clickable Textual UI for the tool-free local LLM chat client."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from local_llm_clients import CONFIG_DIR, SESSIONS_DIR

try:
    from textual import events, work
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.message import Message
    from textual.widgets import Footer, Header, Markdown, Static, TextArea
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise SystemExit("Textual is required: py -3 -m pip install textual") from exc
    raise

from local_llm_clients.chat.cli import Config, create_default_config
from local_llm_clients.common import LlamaClient, SessionStore


HELP_TEXT = """### Commands

- `/help`, `/sessions`, `/new`, `/config`, `/quit`
- `/load SESSION_ID`

Enter submits. Shift+Enter inserts a newline.
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


class ChatTextualApp(App[None]):
    TITLE = "Local LLM Chat"
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
    .error-message { background: #2b1618; border: round #f85149; color: #ff7b72; }
    #prompt { dock: bottom; height: 6; margin: 0 1 1 1; border: tall #3b82f6; background: #161b22; }
    #prompt:focus { border: tall #58a6ff; }
    """

    def __init__(self, config: Config, store: SessionStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.session_id = store.new_id()
        self.llama = LlamaClient(
            config.llama_base_url,
            config.llama_model,
            config.request_timeout,
            config.temperature,
        )
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": config.system_prompt}]
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
            "**Tool-free local LLM chat**  \n"
            "Type `/help` for commands. This client has no external tools.",
            "system-message",
        )
        self.query_one("#prompt", PromptTextArea).focus()

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
            self.run_chat_turn()

    @work(exclusive=True, group="llama-turn")
    async def run_chat_turn(self) -> None:
        self.set_busy(True, "Thinking...")
        try:
            message = await asyncio.to_thread(self.llama.chat, self.messages, [])
            content = message.get("content") or ""
            self.messages.append({"role": "assistant", "content": content})
            await self.add_markdown(content or "_(No content returned)_", "assistant-message")
        except Exception as exc:
            await self.add_plain(str(exc), "error-message", "Error")
        finally:
            self.save()
            self.set_busy(False, "Ready")

    async def handle_command(self, command: str) -> None:
        name, _, rest = command.partition(" ")
        rest = rest.strip()
        try:
            if name == "/help":
                await self.add_markdown(HELP_TEXT, "system-message")
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
                    f"save: {self.store.root}",
                    "system-message",
                    "Configuration",
                )
            elif name == "/multiline":
                await self.add_plain(
                    "Multiline input is enabled. Press Shift+Enter to insert a newline.",
                    "system-message",
                    "Multiline",
                )
            elif name == "/quit":
                self.action_quit()
            else:
                await self.add_plain("Unknown command. Use /help.", "error-message", "Command")
        except Exception as exc:
            await self.add_plain(str(exc), "error-message", "Command error")

    async def render_loaded_session(self) -> None:
        await self.clear_conversation()
        await self.add_plain(f"Loaded {self.session_id}", "system-message", "Session")
        for message in self.messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "user":
                await self.add_plain(content, "user-message", "You")
            elif role == "assistant" and content:
                await self.add_markdown(content, "assistant-message")

    async def add_plain(self, text: str, css_class: str, title: str | None = None) -> None:
        widget = Static(text, markup=False, classes=f"message {css_class}")
        if title:
            widget.border_title = title
        await self.mount_in_conversation(widget)

    async def add_markdown(self, text: str, css_class: str) -> None:
        await self.mount_in_conversation(Markdown(text, classes=f"message {css_class}"))

    async def mount_in_conversation(self, widget: Static | Markdown) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.mount(widget)
        conversation.scroll_end(animate=False)

    async def clear_conversation(self) -> None:
        await self.query_one("#conversation", VerticalScroll).remove_children()

    def save(self) -> None:
        self.store.save(self.session_id, self.messages, self.config)  # type: ignore[arg-type]

    def set_busy(self, busy: bool, state: str) -> None:
        self.busy = busy
        prompt = self.query_one("#prompt", PromptTextArea)
        prompt.disabled = busy
        if not busy:
            prompt.focus()
        self.update_status(state)

    def update_status(self, state: str) -> None:
        self.query_one("#status", Static).update(
            f"{state}  |  {self.config.llama_model}  |  {self.session_id}"
        )

    async def action_clear_view(self) -> None:
        await self.clear_conversation()

    def action_focus_input(self) -> None:
        self.query_one("#prompt", PromptTextArea).focus()

    def action_quit(self) -> None:
        self.save()
        self.exit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual UI for tool-free local-LLM chat")
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "chat-client.config.json")
    parser.add_argument("--session-dir", type=Path, default=SESSIONS_DIR / "chat")
    parser.add_argument("--init-config", action="store_true")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return

    ChatTextualApp(Config.load(args.config), SessionStore(args.session_dir)).run()


if __name__ == "__main__":
    main()
