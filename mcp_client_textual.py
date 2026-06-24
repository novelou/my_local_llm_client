#!/usr/bin/env python3
"""Compatibility entry point for the Unity MCP Textual UI."""

import sys

from local_llm_clients.mcp.textual import *  # noqa: F403
from local_llm_clients.mcp.textual import main


if __name__ == "__main__":
    sys.exit(main())
