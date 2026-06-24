#!/usr/bin/env python3
"""Compatibility entry point for the chat Textual UI."""

from local_llm_clients.chat.textual import *  # noqa: F403
from local_llm_clients.chat.textual import main


if __name__ == "__main__":
    main()
