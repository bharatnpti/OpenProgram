from __future__ import annotations

import json
from typing import cast

__all__ = ["extract_json_object"]


def extract_json_object(text: str) -> dict[str, object] | None:
    """Best-effort extraction of the first balanced JSON object from model text.

    Tolerates Markdown code fences (```json ... ```) and surrounding prose by
    stripping fences and scanning for the first balanced ``{...}`` span. Returns
    the decoded mapping, or ``None`` when no balanced JSON object parses. Pure and
    provider-neutral so both parse and clarification paths can reuse it before any
    fallback.
    """
    if not text:
        return None
    unfenced = _strip_code_fences(text)
    direct = _load_object(unfenced.strip())
    if direct is not None:
        return direct
    candidate = _first_balanced_object(unfenced)
    if candidate is None:
        return None
    return _load_object(candidate)


def _load_object(text: str) -> dict[str, object] | None:
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(decoded, dict):
        return cast("dict[str, object]", decoded)
    return None


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    without_open = stripped[3:]
    newline = without_open.find("\n")
    if newline == -1:
        return text
    body = without_open[newline + 1 :]
    closing = body.rfind("```")
    if closing == -1:
        return body
    return body[:closing]


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            in_string, escaped = _string_scan_step(char, escaped)
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _string_scan_step(char: str, escaped: bool) -> tuple[bool, bool]:
    """Advance JSON string-literal scan state; returns (still_in_string, escaped)."""
    if escaped:
        return True, False
    if char == "\\":
        return True, True
    if char == '"':
        return False, False
    return True, False
