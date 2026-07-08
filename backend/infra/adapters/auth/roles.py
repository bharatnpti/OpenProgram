from __future__ import annotations

from collections.abc import Mapping

from core.domain.auth import Role

_FILTERED_ROLE_PREFIXES = ("uma_", "offline_", "default-roles-")


def map_oidc_roles(
    claims: Mapping[str, object],
    *,
    client_id: str,
    claim_paths: tuple[str, ...],
    role_map: Mapping[str, Role],
) -> frozenset[Role]:
    mapped: set[Role] = set()
    for path in claim_paths:
        resolved_path = path.replace("<client_id>", client_id)
        for raw_role in _values_at_path(claims, resolved_path.split(".")):
            normalized = raw_role.strip().lower()
            if not normalized or _filtered_provider_role(normalized):
                continue
            role = role_map.get(normalized)
            if role is not None:
                mapped.add(role)
    return frozenset(mapped)


def _values_at_path(value: object, path: list[str]) -> list[str]:
    if not path:
        return _role_values(value)
    if not isinstance(value, Mapping):
        return []
    current = value.get(path[0])
    return _values_at_path(current, path[1:])


def _role_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _filtered_provider_role(value: str) -> bool:
    return value.startswith(_FILTERED_ROLE_PREFIXES)
