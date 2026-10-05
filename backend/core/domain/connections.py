"""Connections to the systems OpenProgram works with, as an admin sets them up.

A connector is one kind of external system (an issue tracker, a code host, a
chat workspace, a mail server). Its spec lists the fields an admin fills in;
the spec comes from the infrastructure that talks to the system, so this module
stays provider-neutral. A connection is one tenant's filled-in spec: the plain
settings, and which secret fields hold a value. Secret values never live here:
they are kept encrypted in the secret store and are never read back out to the
console.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from core.domain.errors import OpenProgramError


class ConnectorPurpose(StrEnum):
    """What OpenProgram uses a connector for."""

    ISSUE_TRACKER = "issue_tracker"
    CODE = "code"
    CHAT = "chat"
    CALENDAR = "calendar"
    REPORT_DELIVERY = "report_delivery"


class FieldKind(StrEnum):
    TEXT = "text"
    URL = "url"
    EMAIL = "email"
    NUMBER = "number"
    SELECT = "select"
    SECRET = "secret"


@dataclass(frozen=True, kw_only=True)
class FieldCondition:
    """The field applies only while another field holds one of ``values``."""

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class FieldOption:
    value: str
    label: str
    shown_when: FieldCondition | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectorField:
    key: str
    label: str
    kind: FieldKind
    required: bool = False
    help: str = ""
    placeholder: str = ""
    default: str | None = None
    options: tuple[FieldOption, ...] = ()
    shown_when: FieldCondition | None = None

    @property
    def secret(self) -> bool:
        return self.kind is FieldKind.SECRET


@dataclass(frozen=True, kw_only=True)
class ConnectorSpec:
    id: str
    name: str
    description: str
    purposes: tuple[ConnectorPurpose, ...]
    fields: tuple[ConnectorField, ...]
    #: Connectors in one group are alternatives: a tenant enables at most one.
    exclusive_group: str | None = None

    def field(self, key: str) -> ConnectorField | None:
        return next((item for item in self.fields if item.key == key), None)

    @property
    def secret_keys(self) -> frozenset[str]:
        return frozenset(item.key for item in self.fields if item.secret)


@dataclass(frozen=True, kw_only=True)
class ConnectionTestOutcome:
    """The last test of a stored connection. ``message`` is fixed text, never an error."""

    ok: bool
    message: str
    tested_at: datetime


@dataclass(frozen=True, kw_only=True)
class Connection:
    tenant_id: str
    connector: str
    enabled: bool
    #: Plain settings only, normalised. Secrets are in the secret store.
    settings: Mapping[str, str]
    #: The secret fields that hold a stored value.
    secret_keys: frozenset[str]
    updated_at: datetime
    updated_by: str
    last_test: ConnectionTestOutcome | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectionValues:
    """What an adapter reads for an enabled connection: settings and secrets together.

    Holds only the fields that apply under the chosen options, with defaults
    filled in, so an adapter never sees a password left over from an auth
    method the admin switched away from.
    """

    connector: str
    values: Mapping[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return value if value else None


@dataclass(frozen=True, kw_only=True)
class ConnectionCheck:
    """A tester's verdict on a set of values.

    ``details`` are short label/value lines to show (server name, account), and
    ``suggestions`` offer values for fields, such as the custom fields a tracker
    has, keyed by field key. Nothing here is an exception text or a secret.
    """

    ok: bool
    message: str
    details: tuple[tuple[str, str], ...] = ()
    suggestions: Mapping[str, tuple[FieldOption, ...]] = field(default_factory=dict)


class ConnectionValidationError(OpenProgramError):
    """The values an admin entered do not fit the connector's fields."""


def condition_holds(condition: FieldCondition | None, values: Mapping[str, str]) -> bool:
    if condition is None:
        return True
    return values.get(condition.field, "") in condition.values


def with_defaults(spec: ConnectorSpec, settings: Mapping[str, str]) -> dict[str, str]:
    """``settings`` with every unset plain field's default filled in."""
    filled = dict(settings)
    for item in spec.fields:
        if not item.secret and item.default is not None and not filled.get(item.key):
            filled[item.key] = item.default
    return filled


def applicable_fields(
    spec: ConnectorSpec, settings: Mapping[str, str]
) -> tuple[ConnectorField, ...]:
    """The fields that apply under the options chosen in ``settings`` (defaults filled)."""
    filled = with_defaults(spec, settings)
    return tuple(item for item in spec.fields if condition_holds(item.shown_when, filled))


def normalize_settings(spec: ConnectorSpec, settings: Mapping[str, str | None]) -> dict[str, str]:
    """Check and tidy the plain settings an admin entered.

    Blank values are dropped. Unknown keys, secret keys, and values that do not
    fit their field's kind are refused with one sentence naming every problem.
    """
    normalized: dict[str, str] = {}
    problems: list[str] = []
    for key, raw in settings.items():
        spec_field = spec.field(key)
        if spec_field is None:
            problems.append(f"{spec.name} has no field {key!r}")
            continue
        if spec_field.secret:
            problems.append(f"{spec_field.label} is a secret and is set on its own")
            continue
        value = (raw or "").strip()
        if not value:
            continue
        problem = _value_problem(spec_field, value)
        if problem is not None:
            problems.append(problem)
            continue
        normalized[key] = _normal_value(spec_field, value)
    filled = with_defaults(spec, normalized)
    for key, value in normalized.items():
        spec_field = spec.field(key)
        if spec_field is None or spec_field.kind is not FieldKind.SELECT:
            continue
        option = next(item for item in spec_field.options if item.value == value)
        if condition_holds(spec_field.shown_when, filled) and not condition_holds(
            option.shown_when, filled
        ):
            problems.append(f"{option.label} is not available for this {spec_field.label}")
    if problems:
        raise ConnectionValidationError(_sentence(problems))
    return normalized


def missing_required(
    spec: ConnectorSpec, settings: Mapping[str, str], secret_keys: frozenset[str]
) -> tuple[str, ...]:
    """Labels of required fields that apply and hold no value: what blocks enabling."""
    filled = with_defaults(spec, settings)
    missing: list[str] = []
    for item in applicable_fields(spec, settings):
        if not item.required:
            continue
        present = item.key in secret_keys if item.secret else bool(filled.get(item.key))
        if not present:
            missing.append(item.label)
    return tuple(missing)


def _value_problem(spec_field: ConnectorField, value: str) -> str | None:
    if spec_field.kind is FieldKind.URL:
        return _url_problem(spec_field.label, value)
    if spec_field.kind is FieldKind.EMAIL:
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            return f"{spec_field.label} must be an email address"
        return None
    if spec_field.kind is FieldKind.NUMBER:
        if not value.isdigit() or int(value) <= 0:
            return f"{spec_field.label} must be a whole number above zero"
        return None
    if spec_field.kind is FieldKind.SELECT:
        if all(option.value != value for option in spec_field.options):
            allowed = ", ".join(option.label for option in spec_field.options)
            return f"{spec_field.label} must be one of {allowed}"
        return None
    return None


def _url_problem(label: str, value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return f"{label} must be an http(s) address"
    if parts.username or parts.password:
        return f"{label} must not carry a user name or password"
    if parts.query or parts.fragment:
        return f"{label} must not carry a query or fragment"
    return None


def _normal_value(spec_field: ConnectorField, value: str) -> str:
    if spec_field.kind is FieldKind.URL:
        return value.rstrip("/")
    if spec_field.kind is FieldKind.NUMBER:
        return str(int(value))
    if spec_field.kind is FieldKind.EMAIL:
        return value.lower()
    return value


def _sentence(problems: list[str]) -> str:
    text = "; ".join(problems)
    return text[0].upper() + text[1:] + "."
