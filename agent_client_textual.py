#!/usr/bin/env python3
"""Compatibility entry point for the local file agent Textual UI."""

from local_llm_clients.agent.textual import *  # noqa: F403
from local_llm_clients.agent.textual import main


if __name__ == "__main__":
    main()
