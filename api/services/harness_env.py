"""Environment helpers for locating agent-harness executables.

When the server is launched from a GUI (e.g. macOS Finder/Dock), its PATH
can differ from the user's terminal PATH, so bare `shutil.which()` lookups
may miss Homebrew-installed CLIs. These helpers normalize PATH before any
detection or subprocess launch.
"""

import os
import shutil

_EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin",   # Apple Silicon Homebrew
    "/usr/local/bin",      # Intel Homebrew / manual installs
    os.path.expanduser("~/.local/bin"),
]


def normalized_env() -> dict:
    """Return a copy of os.environ with common CLI bin dirs on PATH."""
    env = os.environ.copy()
    parts = env.get("PATH", "").split(os.pathsep)
    for d in _EXTRA_BIN_DIRS:
        if d and d not in parts:
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts)
    return env


def which_harness(command: str) -> str | None:
    """Locate a harness executable using the normalized PATH."""
    return shutil.which(command, path=normalized_env()["PATH"])
