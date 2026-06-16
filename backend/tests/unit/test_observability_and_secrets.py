from __future__ import annotations

from cryptography.fernet import Fernet

from core.ports.secrets import SecretRef
from infra.adapters.secrets.encrypted import FernetSecretStore, InMemoryEncryptedSecretRecordStore
from infra.observability.logging import redact_sensitive


def test_redaction_removes_dm_content_and_tokens() -> None:
    event = redact_sensitive(
        None,
        "info",
        {"message": "raw dm", "token": "secret-token", "safe": "kept"},
    )
    assert event == {"message": "[redacted]", "token": "[redacted]", "safe": "kept"}


async def test_secret_store_encrypts_records() -> None:
    record_store = InMemoryEncryptedSecretRecordStore()
    secret_store = FernetSecretStore(Fernet(Fernet.generate_key()), record_store)
    ref = SecretRef(tenant_id="demo", connector="chat", key="bot_token")
    await secret_store.put(ref, "plain-value")
    assert await secret_store.get(ref) == "plain-value"
    ciphertext = await record_store.get_ciphertext(ref)
    assert b"plain-value" not in ciphertext
