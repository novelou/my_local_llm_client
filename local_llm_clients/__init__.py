"""Local LLM client package."""

from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parent


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def first_existing_directory(candidates: list[Path], fallback: Path) -> Path:
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return fallback


RUNTIME_ROOT = runtime_root()
CONFIG_DIR = first_existing_directory(
    [
        RUNTIME_ROOT / "config",
        RUNTIME_ROOT / "local_llm_clients" / "config",
        PACKAGE_ROOT / "config",
    ],
    RUNTIME_ROOT / "config",
)
SESSIONS_DIR = first_existing_directory(
    [
        RUNTIME_ROOT / "sessions",
        RUNTIME_ROOT / "local_llm_clients" / "sessions",
        PACKAGE_ROOT / "sessions",
    ],
    RUNTIME_ROOT / "sessions",
)
