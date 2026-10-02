"""Read QA-org settings without ever printing them.

Precedence matches the other oneai projects: a real environment variable wins,
then the shared secrets file. Values are never echoed; callers report only
whether a key is present.
"""

from __future__ import annotations

import os
from pathlib import Path

SECRETS_FILE = Path(
    os.environ.get("OPENPROGRAM_SECRETS_FILE", "~/.config/oneai/secrets.env")
).expanduser()


def _file_values() -> dict[str, str]:
    if not SECRETS_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for raw in SECRETS_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get(key: str) -> str | None:
    value = os.environ.get(key) or _file_values().get(key)
    return value or None


def require(*keys: str) -> dict[str, str]:
    """Return every key, or exit naming the missing ones (never their values)."""
    found = {key: get(key) for key in keys}
    missing = [key for key, value in found.items() if value is None]
    if missing:
        raise SystemExit(f"missing in environment or {SECRETS_FILE}: {', '.join(missing)}")
    return {key: value for key, value in found.items() if value is not None}
