"""Minimal zero-dependency .env loader.

Loads KEY=VALUE lines from a .env file into os.environ without overriding
values already set in the real environment. Kept dependency-free so the slice
runs with only the `anthropic` package installed.
"""

from __future__ import annotations

import os


def load_env(path: str = ".env") -> bool:
    """Populate os.environ from `path`. Returns True if the file was read.

    - Existing environment variables win (a real exported var is not clobbered).
    - Supports `export KEY=value`, quoted values, and `#` comments.
    """
    if not os.path.exists(path):
        return False
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return True
