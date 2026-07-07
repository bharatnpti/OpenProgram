# Auth Seam LLD

## Domain Classes

- `Role`: `DEV`, `PO`, `SM`, `MGR`, `EXEC`, `ADMIN`.
- `Principal`: frozen value object with `tenant_id`, `subject`, roles, and scopes.

## Ports

```python
class AuthProvider(Protocol):
    async def authenticate(self, token: str | None) -> Principal: ...

class CurrentPrincipal(Protocol):
    async def get(self) -> Principal: ...
```

## Policy

`AuthorizationPolicy` is central and default-deny. Use cases ask it for capability/scope decisions instead of checking roles inline.

Sensitive fields include budget. Raw DM content is never exposed to any role or API.

## Tests

Unit tests assert default-deny, developer denial for executive fields, and administrator allowance.
