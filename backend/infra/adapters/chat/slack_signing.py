from __future__ import annotations

import hashlib
import hmac
import time


def verify_slack_signature(
    signing_secret: str | None,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    tolerance_seconds: int,
) -> bool:
    if signing_secret is None or not signing_secret.strip():
        return False
    if timestamp is None or not timestamp.strip():
        return False
    if signature is None or not signature.strip():
        return False
    if not isinstance(body, bytes):
        return False
    if tolerance_seconds <= 0:
        return False

    timestamp_value = timestamp.strip()
    try:
        timestamp_seconds = int(timestamp_value)
    except ValueError:
        return False
    if timestamp_seconds < 0:
        return False

    if abs(int(time.time()) - timestamp_seconds) > tolerance_seconds:
        return False

    base_string = b"v0:" + timestamp_value.encode("utf-8") + b":" + body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        base_string,
        hashlib.sha256,
    ).hexdigest()
    expected_signature = f"v0={digest}"
    return hmac.compare_digest(expected_signature, signature.strip())
