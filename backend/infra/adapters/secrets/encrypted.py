from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from core.domain.errors import SecretNotFound
from core.ports.secrets import SecretRef


class EncryptedSecretRecordStore(Protocol):
    async def put_ciphertext(self, ref: SecretRef, ciphertext: bytes) -> None: ...

    async def get_ciphertext(self, ref: SecretRef) -> bytes: ...


@dataclass
class InMemoryEncryptedSecretRecordStore:
    _records: dict[SecretRef, bytes] = field(default_factory=dict)

    async def put_ciphertext(self, ref: SecretRef, ciphertext: bytes) -> None:
        self._records[ref] = ciphertext

    async def get_ciphertext(self, ref: SecretRef) -> bytes:
        try:
            return self._records[ref]
        except KeyError as exc:
            raise SecretNotFound(f"secret {ref.connector}/{ref.key} not found") from exc


@dataclass(frozen=True)
class FernetSecretStore:
    fernet: Fernet
    record_store: EncryptedSecretRecordStore

    async def put(self, ref: SecretRef, value: str) -> None:
        ciphertext = self.fernet.encrypt(value.encode("utf-8"))
        await self.record_store.put_ciphertext(ref, ciphertext)

    async def get(self, ref: SecretRef) -> str:
        ciphertext = await self.record_store.get_ciphertext(ref)
        try:
            plaintext = self.fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise SecretNotFound(
                f"secret {ref.connector}/{ref.key} could not be decrypted"
            ) from exc
        return plaintext.decode("utf-8")
