#!/usr/bin/env python3
"""Compatibility entry point for the Unity MCP CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from local_llm_clients.mcp.unity import *  # noqa: F403
from local_llm_clients.mcp.unity import main


if __name__ == "__main__":
    sys.exit(main())
