"""Local LLM client package."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE_ROOT / "config"
SESSIONS_DIR = PACKAGE_ROOT / "sessions"
