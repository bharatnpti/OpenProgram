# Jira/Git Configurable Scope Implementation Status

Date: 2026-06-29

## Current State

- Jira and GitHub integrations are implemented as read-only sync providers.
- Jira sync supports both the legacy `project_key` path and configured JQL sync into a target project/pod node.
- GitHub sync still runs by repository name and can link synced repo nodes to configured project/pod containers.
- Project and pod runtime config stores integration fields in existing graph node metadata.
- Admin Config exposes project/pod Jira and GitHub integration scope fields.
- Runtime project and pod definitions now drive scheduled Jira/GitHub sync targets through runtime fanout.
- GitLab is not currently implemented; the active VCS provider options are GitHub and fake.

## Agreed Target

- Jira and Git sync scopes should be configurable while defining projects and pods.
- Projects should define broad integration scope:
  - Jira project key or base JQL.
  - Optional Jira board ID.
  - Allowed GitHub repo list.
- Pods should narrow project scope:
  - Additional Jira filter JQL combined with the linked project query.
  - GitHub repo subset selected from the linked project repos.
- Runtime configuration should become the source for scheduled sync targets.
- Existing env-based sync lists should remain as compatibility fallback when no runtime sync targets are configured.
- Integrations remain read-only.
- Current implementation should target the existing GitHub adapter only; GitLab is out of scope for the first implementation.

## Implementation Status

- Planning: complete.
- Backend DTO/API changes: implemented.
- Admin Config UI changes: implemented.
- Jira provider support for configured JQL sync: implemented.
- GitHub runtime repo target resolution: implemented.
- Runtime sync-target resolver and workflow fanout: implemented.
- OpenAPI/frontend client regeneration: complete.
- Tests: backend test/lint and frontend lint/build validation complete; full `make verify` not run.

## Implementation Notes

- Store new project/pod integration fields in existing graph node metadata to avoid a schema migration.
- Keep provider SDK usage isolated in infrastructure adapters.
- Keep admin workflow dispatch routed through `WorkflowScheduler`.
- Cursor scopes should include target ID plus a stable query hash so changed JQL does not reuse stale sync cursors.
- Deduplicate GitHub repo syncs across projects and pods during a fanout run.
- Reject or ignore pod repo selections that are not part of the linked project repo allowlist.

## Validation Needed

- Optional full `make verify` once Docker-backed integration/smoke checks are desired.
- Optional manual Admin Config browser smoke for project/pod integration form ergonomics against a live backend.

## Validation Completed

- `PYTHONPATH=backend uv run pytest`
- `make lint`
- `make frontend-lint`
- `make frontend-build`
- OpenAPI regenerated with `PYTHONPATH=backend uv run python backend/api/openapi.py` and `npm run generate:client`.
- Vite dev server smoke: `curl -I http://127.0.0.1:5174/` returned HTTP 200.
