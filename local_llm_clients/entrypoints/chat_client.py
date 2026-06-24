#!/usr/bin/env python3
"""Compatibility entry point for the chat CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from local_llm_clients.chat.cli import *  # noqa: F403
from local_llm_clients.chat.cli import main


if __name__ == "__main__":
    main()
