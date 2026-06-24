#!/usr/bin/env python3
"""
Lightweight tool-free local-LLM chat client.

Flow:
  .gguf -> llama-server(OpenAI compatible) -> this CLI
"""

from __future__ import annotations

import argparse
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_llm_clients import CONFIG_DIR, SESSIONS_DIR
from local_llm_clients.common import LlamaClient, SessionStore, env


DEFAULT_SYSTEM_PROMPT = """You are a helpful chat assistant.
Answer the user directly and clearly.
You have no tools and cannot inspect files, browse the web, or perform external actions.
"""


@dataclass
class Config:
    llama_base_url: str = "http://127.0.0.1:8081/v1/"
    llama_model: str = "local-model"
    mcp_url: str = ""
    temperature: float = 0.7
    request_timeout: int = 120
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

def print_help() -> None:
    print(
        textwrap.dedent(
            """
            Commands:
              /help                  Show this help
              /multiline             Enter a multi-line prompt; finish with a line containing only .
              /sessions              List saved sessions
              /load SESSION_ID       Load a saved session
              /new                   Start a fresh session
              /config                Show active config
              /quit                  Save and exit
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
                "temperature": 0.7,
                "request_timeout": 120,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class ChatCliApp:
    def __init__(self, config: Config, store: SessionStore) -> None:
        self.config = config
        self.store = store
        self.session_id = store.new_id()
        self.llama = LlamaClient(config.llama_base_url, config.llama_model, config.request_timeout, config.temperature)
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": config.system_prompt}]

    def start(self) -> None:
        print("Tool-free local LLM chat")
        print("Type /help for commands. Type /multiline for multi-line input. Type /quit to exit.")
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
            self.submit(user_input)

    def read_multiline_input(self) -> str:
        print("Enter multi-line input. Finish with a line containing only .")
        lines: list[str] = []
        while True:
            try:
                line = input("... ")
            except (EOFError, KeyboardInterrupt):
                print()
                return ""
            if line == ".":
                return "\n".join(lines).strip()
            lines.append(line)

    def submit(self, user_input: str) -> None:
        self.messages.append({"role": "user", "content": user_input})
        try:
            message = self.llama.chat(self.messages, [])
            content = message.get("content") or ""
            self.messages.append({"role": "assistant", "content": content})
            print(f"\nassistant> {content}")
            self.save()
        except Exception as exc:
            print(f"error: {exc}")

    def handle_command(self, command: str) -> bool:
        name, _, rest = command.partition(" ")
        if name == "/help":
            print_help()
        elif name == "/multiline":
            user_input = self.read_multiline_input()
            if user_input:
                self.submit(user_input)
        elif name == "/sessions":
            for session in self.store.list():
                print(f"{session['id']}  {session['updated_at']}  {session['title']}")
        elif name == "/load":
            session_id = rest.strip()
            if not session_id:
                print("Usage: /load SESSION_ID")
            else:
                self.messages = self.store.load(session_id)
                self.session_id = session_id
                print(f"Loaded session {self.session_id}")
        elif name == "/new":
            self.session_id = self.store.new_id()
            self.messages = [{"role": "system", "content": self.config.system_prompt}]
            print(f"New session {self.session_id}")
        elif name == "/config":
            print(f"llama: {self.config.llama_base_url} model={self.config.llama_model}")
            print(f"save: {self.store.root}")
        elif name == "/quit":
            self.save()
            return True
        else:
            print("Unknown command. Type /help.")
        return False

    def save(self) -> None:
        self.store.save(self.session_id, self.messages, self.config)  # type: ignore[arg-type]
        print(f"Saved session {self.session_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight tool-free local-LLM chat client")
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "chat-client.config.json")
    parser.add_argument("--init-config", action="store_true", help="Write a default config file and exit.")
    args = parser.parse_args()

    if args.init_config:
        create_default_config(args.config)
        print(f"Wrote {args.config}")
        return

    config = Config.load(args.config)
    store = SessionStore(SESSIONS_DIR / "chat")
    ChatCliApp(config, store).start()


if __name__ == "__main__":
    main()
