"""Set up, test and read the tenant's connections to external systems.

An admin saves a connection's plain settings and its secrets separately: a
secret is only ever written or cleared, never sent back. Leaving a secret out
of a save keeps the stored one, so the console can save other fields without
asking for a token again. A connection can be saved incomplete while it is
off; turning it on needs every required field that applies.

Adapters read an enabled connection through :class:`StoredConnections`. A
tenant with no enabled connection for a connector falls back to the server's
own settings, so a deployment configured by environment keeps working.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.connections import (
    Connection,
    ConnectionCheck,
    ConnectionTestOutcome,
    ConnectionValidationError,
    ConnectionValues,
    ConnectorSpec,
    applicable_fields,
    missing_required,
    normalize_settings,
    with_defaults,
)
from core.domain.errors import OpenProgramError, SecretNotFound
from core.ports.connections import ConnectionRepository, ConnectionTester, ConnectorCatalog
from core.ports.secrets import SecretRef, SecretStore

# A secret longer than this is not a token anyone pastes; refusing it keeps a
# mistaken paste of a whole file out of the store.
MAX_SECRET_LENGTH = 8192


class UnknownConnector(OpenProgramError):
    """No connector with that id exists in this deployment."""


class ConnectionConflict(OpenProgramError):
    """Another connector that does the same job is already on."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class ConnectionView:
    spec: ConnectorSpec
    connection: Connection | None
    environment_configured: bool


@dataclass(frozen=True, kw_only=True)
class ConnectionDraft:
    """Unsaved values to test before saving: they override the stored ones."""

    settings: Mapping[str, str | None]
    secrets: Mapping[str, str]


@dataclass(frozen=True, kw_only=True)
class ConnectionTestView:
    check: ConnectionCheck
    tested_at: datetime
    #: True when the stored connection was tested and its last test updated.
    recorded: bool


@dataclass(frozen=True, kw_only=True)
class StoredConnections:
    """Read a tenant's enabled connections with their secrets decrypted."""

    repository: ConnectionRepository
    secret_store: SecretStore
    catalog: ConnectorCatalog

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        spec = _spec(self.catalog, connector)
        if spec is None:
            return None
        connection = await self.repository.get(tenant_id, connector)
        if connection is None or not connection.enabled:
            return None
        return await self.values_for(spec, connection)

    async def values_for(self, spec: ConnectorSpec, connection: Connection) -> ConnectionValues:
        values = with_defaults(spec, connection.settings)
        for item in applicable_fields(spec, connection.settings):
            if not item.secret or item.key not in connection.secret_keys:
                continue
            secret = await self._secret(connection.tenant_id, spec.id, item.key)
            if secret:
                values[item.key] = secret
        applicable = {item.key for item in applicable_fields(spec, connection.settings)}
        return ConnectionValues(
            connector=spec.id,
            values={key: value for key, value in values.items() if key in applicable},
        )

    async def _secret(self, tenant_id: str, connector: str, key: str) -> str | None:
        try:
            return await self.secret_store.get(
                SecretRef(tenant_id=tenant_id, connector=connector, key=key)
            )
        except SecretNotFound:
            return None


class ConnectionService:
    def __init__(
        self,
        *,
        repository: ConnectionRepository,
        secret_store: SecretStore,
        catalog: ConnectorCatalog,
        tester: ConnectionTester,
        clock: Callable[[], datetime] = _utc_now,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._on_change = on_change
        self._secret_store = secret_store
        self._catalog = catalog
        self._tester = tester
        self._clock = clock
        self._stored = StoredConnections(
            repository=repository, secret_store=secret_store, catalog=catalog
        )

    async def list_connections(self, tenant_id: str) -> list[ConnectionView]:
        stored = {item.connector: item for item in await self._repository.list(tenant_id)}
        return [
            ConnectionView(
                spec=spec,
                connection=stored.get(spec.id),
                environment_configured=self._catalog.environment_configured(spec.id),
            )
            for spec in self._catalog.specs()
        ]

    async def connection(self, tenant_id: str, connector: str) -> ConnectionView:
        spec = self._require_spec(connector)
        return ConnectionView(
            spec=spec,
            connection=await self._repository.get(tenant_id, connector),
            environment_configured=self._catalog.environment_configured(spec.id),
        )

    async def save(
        self,
        tenant_id: str,
        connector: str,
        *,
        enabled: bool,
        settings: Mapping[str, str | None],
        secrets: Mapping[str, str | None],
        actor: str,
    ) -> ConnectionView:
        """Store the connection.

        A secret mapped to None or blank is cleared; a secret left out is kept.
        """
        spec = self._require_spec(connector)
        existing = await self._repository.get(tenant_id, connector)
        normalized = normalize_settings(spec, settings)
        to_store, to_clear = _secret_changes(spec, secrets)
        secret_keys = (
            (existing.secret_keys if existing is not None else frozenset()) - to_clear
        ) | frozenset(to_store)
        if enabled:
            missing = missing_required(spec, normalized, secret_keys)
            if missing:
                raise ConnectionValidationError(
                    f"Fill in {', '.join(missing)} before turning {spec.name} on."
                )
            await self._ensure_no_alternative_enabled(tenant_id, spec)
        for key, value in to_store.items():
            await self._secret_store.put(
                SecretRef(tenant_id=tenant_id, connector=spec.id, key=key), value
            )
        for key in to_clear:
            await self._secret_store.delete(
                SecretRef(tenant_id=tenant_id, connector=spec.id, key=key)
            )
        changed = (
            existing is None
            or dict(existing.settings) != normalized
            or existing.secret_keys != secret_keys
            or bool(to_store)
        )
        connection = Connection(
            tenant_id=tenant_id,
            connector=spec.id,
            enabled=enabled,
            settings=normalized,
            secret_keys=secret_keys,
            updated_at=self._clock(),
            updated_by=actor,
            # A test says nothing about values entered after it.
            last_test=None if changed or existing is None else existing.last_test,
        )
        await self._repository.save(connection)
        self._changed(tenant_id)
        return ConnectionView(
            spec=spec,
            connection=connection,
            environment_configured=self._catalog.environment_configured(spec.id),
        )

    async def remove(self, tenant_id: str, connector: str) -> None:
        """Forget the connection and every secret it stored. Idempotent."""
        spec = self._require_spec(connector)
        for key in spec.secret_keys:
            await self._secret_store.delete(
                SecretRef(tenant_id=tenant_id, connector=spec.id, key=key)
            )
        await self._repository.delete(tenant_id, connector)
        self._changed(tenant_id)

    async def test(
        self, tenant_id: str, connector: str, *, draft: ConnectionDraft | None = None
    ) -> ConnectionTestView:
        """Try the stored values, or a draft over them, against the system.

        Only a test of the stored values updates the connection's last test: a
        draft's result says nothing about what is saved.
        """
        spec = self._require_spec(connector)
        existing = await self._repository.get(tenant_id, connector)
        stored_settings: Mapping[str, str] = existing.settings if existing is not None else {}
        settings = dict(stored_settings)
        secret_overrides: dict[str, str] = {}
        if draft is not None:
            settings = normalize_settings(spec, {**stored_settings, **draft.settings})
            secret_overrides, _cleared = _secret_changes(spec, draft.secrets)
        values = dict(
            (await self._stored.values_for(spec, existing)).values if existing is not None else {}
        )
        values.update(with_defaults(spec, settings))
        values.update(secret_overrides)
        applicable = {item.key for item in applicable_fields(spec, settings)}
        values = {key: value for key, value in values.items() if key in applicable}
        missing = [
            item.label
            for item in applicable_fields(spec, settings)
            if item.required and not values.get(item.key)
        ]
        tested_at = self._clock()
        if missing:
            check = ConnectionCheck(ok=False, message=f"Fill in {', '.join(missing)} first.")
        else:
            check = await self._tester.test(tenant_id, spec.id, values)
        recorded = draft is None and existing is not None
        if recorded:
            await self._repository.record_test(
                tenant_id,
                spec.id,
                ConnectionTestOutcome(ok=check.ok, message=check.message, tested_at=tested_at),
            )
        return ConnectionTestView(check=check, tested_at=tested_at, recorded=recorded)

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        return await self._stored.resolve(tenant_id, connector)

    async def _ensure_no_alternative_enabled(self, tenant_id: str, spec: ConnectorSpec) -> None:
        if spec.exclusive_group is None:
            return
        for other in self._catalog.specs():
            if other.id == spec.id or other.exclusive_group != spec.exclusive_group:
                continue
            connection = await self._repository.get(tenant_id, other.id)
            if connection is not None and connection.enabled:
                raise ConnectionConflict(
                    f"{other.name} is already on. Turn it off before turning {spec.name} on."
                )

    def _changed(self, tenant_id: str) -> None:
        if self._on_change is not None:
            self._on_change(tenant_id)

    def _require_spec(self, connector: str) -> ConnectorSpec:
        spec = _spec(self._catalog, connector)
        if spec is None:
            raise UnknownConnector(f"No connector {connector!r} is available.")
        return spec


def _spec(catalog: ConnectorCatalog, connector: str) -> ConnectorSpec | None:
    return next((spec for spec in catalog.specs() if spec.id == connector), None)


def _secret_changes(
    spec: ConnectorSpec, secrets: Mapping[str, str | None]
) -> tuple[dict[str, str], frozenset[str]]:
    to_store: dict[str, str] = {}
    to_clear: set[str] = set()
    problems: list[str] = []
    for key, raw in secrets.items():
        spec_field = spec.field(key)
        if spec_field is None or not spec_field.secret:
            problems.append(f"{spec.name} has no secret {key!r}")
            continue
        value = (raw or "").strip()
        if not value:
            to_clear.add(key)
        elif len(value) > MAX_SECRET_LENGTH:
            problems.append(f"{spec_field.label} is longer than any token or password")
        else:
            to_store[key] = value
    if problems:
        text = "; ".join(problems)
        raise ConnectionValidationError(text[0].upper() + text[1:] + ".")
    return to_store, frozenset(to_clear)
