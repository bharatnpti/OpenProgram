# Implementation Status: Runtime Config Admin CRUD

Last updated: 2026-06-18

## Current State

The runtime configuration admin CRUD implementation is substantially complete. Backend support is implemented and the focused backend unit/contract suite passes. Frontend shell, routing, dynamic persona dashboards, directory pages, admin config UI, and frontend typecheck/build verification are now done. Broader backend verification and manual config→dashboard smoke remain optional follow-ups.

Primary goal:

- Implement `runtime_config_admin_crud_9232cdd9.plan.md`.
- Update and write tests.
- Keep OpenAPI schema and generated frontend client in sync.

Latest progress:

- Implemented frontend UI primitives, app shell/sidebar, role context/switcher, and nested routes.
- Made `PersonaDashboard` configuration-driven with directory-backed selectors (no hardcoded IDs).
- Added Pods/Projects list+detail pages, Portfolio page, and Admin config CRUD/linking/check-in UI.
- Ran `npm run typecheck` and `npm run build` successfully.
- Re-ran focused backend suite: `55 passed, 1 warning`.

## Worktree Snapshot

Current status before latest progress update:

```text
 M backend/api/dependencies.py
 M backend/api/dtos.py
 M backend/api/main.py
 M backend/core/application/authorization.py
 M backend/core/application/persona_views.py
 M backend/core/ports/repositories.py
 M backend/infra/persistence/in_memory_graph.py
 M backend/infra/persistence/postgres_graph.py
 M backend/infra/persistence/postgres_status.py
 M backend/infra/phase1_smoke.py
 M backend/tests/contract/contracts.py
 M backend/tests/contract/fakes.py
 M backend/tests/unit/test_api.py
 M backend/tests/unit/test_authorization.py
 M backend/tests/unit/test_graph_store.py
 M backend/tests/unit/test_persona_views.py
 M docker-compose.yml
 M frontend/package-lock.json
 M frontend/package.json
 M frontend/src/api/client.ts
 M frontend/src/api/generated.ts
 M frontend/src/api/openapi.json
 M frontend/src/api/schema.ts
?? backend/api/routers/config.py
?? backend/core/application/config_service.py
?? backend/tests/unit/test_config_service.py
?? imp-status.md
?? lovable-git-files-301.json
?? lovable-project.png
?? runtime_config_admin_crud_9232cdd9.plan.md
```

Pre-existing dirty/untracked files observed before this implementation started:

- `backend/infra/phase1_smoke.py`
- `docker-compose.yml`
- `lovable-git-files-301.json`
- `lovable-project.png`
- `runtime_config_admin_crud_9232cdd9.plan.md`

Do not revert those unless the user explicitly asks. They may contain user work or imported artifacts.

## Lore Protocol

Repo instructions require Lore Protocol before modifying files. Lore was checked for the main touched areas during implementation. For this status file, the following were checked and returned no results:

- `lore constraints imp-status.md --json`
- `lore rejected imp-status.md --json`
- `lore directives imp-status.md --json`

These checks were repeated before this progress update and still returned no results.

Important constraints from earlier Lore checks:

- Core/API must stay hexagonal and provider-neutral.
- Do not expose raw DM/reply content through logs, traces, persona views, or public APIs.
- OpenAPI schema and generated frontend client must be committed with API route changes.
- Postgres graph writes must keep relational rows and AGE sync in one transaction.
- Tests should be deterministic and fake-backed where possible.

## Backend Completed So Far

### Repository Ports

`backend/core/ports/repositories.py`

- Added `NodeKind` and `EdgeKind` imports.
- Extended `GraphRepository` protocol with:
  - `list_nodes(tenant_id, kind=None)`
  - `get_node(tenant_id, id)`
  - `delete_node(tenant_id, id)`
  - `list_edges(tenant_id, from_node_id=None, to_node_id=None, kind=None)`
  - `remove_edge(edge)`
- Extended `StatusRepository` with:
  - `list_checkin_preferences(tenant_id)`

### In-Memory Persistence

`backend/infra/persistence/in_memory_graph.py`

- Implemented graph node list/get/delete.
- Implemented graph edge list/remove.
- `delete_node` removes incident edges.
- `list_edges` filters by source, target, and kind, and returns sorted results.
- Implemented `list_checkin_preferences`.

### Postgres Persistence

`backend/infra/persistence/postgres_graph.py`

- Implemented graph node list/get/delete.
- Implemented graph edge list/remove.
- `delete_node` and `remove_edge` run relational mutations and AGE sync in one transaction.
- Added AGE cleanup helpers:
  - `_sync_age_delete_node`
  - `_sync_age_remove_edge`

Attention point:

- AGE delete Cypher snippets still need integration validation against a real Postgres/AGE instance.

`backend/infra/persistence/postgres_status.py`

- Implemented `list_checkin_preferences`.

### Config Application Service

New file: `backend/core/application/config_service.py`

Implemented:

- `ConfigService`
- `DirectoryService`
- `DirectoryItemView`
- `ConfigValidationError`
- `ConfigConflict`

ConfigService currently supports:

- Program, project, pod, and member node CRUD.
- Program-project linking/unlinking.
- Project-pod linking/unlinking.
- Pod-member linking/unlinking with role and `valid_from` metadata.
- Member-task assignment/unassignment.
- Member check-in preference get/update/list.
- Duplicate/self-link validation.
- Referenced node kind validation.
- Metadata merge on node update.

DirectoryService currently supports:

- Listing programs, projects, and pods.
- Including relationship IDs.
- Including latest rollup `rag` and `source` values when present.

### Authorization

`backend/core/application/authorization.py`

- Added `READ_DIRECTORY`.
- Added `MANAGE_CONFIG`.
- `READ_DIRECTORY` is available to developer, PO, SM, manager, and executive roles.
- `MANAGE_CONFIG` is admin-only through the existing admin short-circuit.

### Persona Views

`backend/core/application/persona_views.py`

- Removed the hardcoded `program-platform` fallback as the only portfolio root.
- `portfolio_heatmap` now:
  - uses explicit `program_root_id` if provided,
  - otherwise uses the first configured program node,
  - otherwise returns an empty heatmap.

### API Dependencies

`backend/api/dependencies.py`

- Added `get_config_service`.
- Added `get_directory_service`.

### API DTOs

`backend/api/dtos.py`

Added request/response models:

- `ConfigNodeResponse`
- `ConfigNodeCreateRequest`
- `ConfigNodeUpdateRequest`
- `ConfigEdgeResponse`
- `ProgramProjectLinkRequest`
- `PodMemberLinkRequest`
- `MemberTaskAssignmentRequest`
- `DirectoryItemResponse`

### Config Router

New file: `backend/api/routers/config.py`

Admin-gated config endpoints implemented:

- `GET /config/programs`
- `POST /config/programs`
- `PUT /config/programs/{program_id}`
- `DELETE /config/programs/{program_id}`
- `GET /config/projects`
- `POST /config/projects`
- `PUT /config/projects/{project_id}`
- `DELETE /config/projects/{project_id}`
- `GET /config/pods`
- `POST /config/pods`
- `PUT /config/pods/{pod_id}`
- `DELETE /config/pods/{pod_id}`
- `GET /config/members`
- `POST /config/members`
- `PUT /config/members/{member_id}`
- `DELETE /config/members/{member_id}`
- `POST /config/projects/{project_id}/program`
- `DELETE /config/projects/{project_id}/program`
- `POST /config/pods/{pod_id}/projects/{project_id}`
- `DELETE /config/pods/{pod_id}/projects/{project_id}`
- `POST /config/pods/{pod_id}/members/{member_id}`
- `DELETE /config/pods/{pod_id}/members/{member_id}`
- `POST /config/members/{member_id}/tasks`
- `DELETE /config/members/{member_id}/tasks`
- `GET /config/members/{member_id}/checkin-preference`
- `PUT /config/members/{member_id}/checkin-preference`
- `GET /config/checkin-preferences`

Read-only directory endpoints implemented:

- `GET /programs`
- `GET /projects`
- `GET /pods`

Notes:

- Delete endpoints return `204`.
- `DELETE /config/members/{member_id}/tasks` takes `task_id` as a query parameter.
- Directory endpoints are gated by `READ_DIRECTORY`.
- Config endpoints are gated by `MANAGE_CONFIG`.

### API Main

`backend/api/main.py`

- Registered `config.router`.
- Removed automatic demo graph population from memory-mode lifespan.
- Tests that need demo data now use test-local fixtures explicitly.

## Backend Tests Added/Updated

`backend/tests/contract/fakes.py`

- Fake status repository implements `list_checkin_preferences`.

`backend/tests/contract/contracts.py`

- Status repository contract now asserts `list_checkin_preferences("demo")`.

`backend/tests/unit/test_graph_store.py`

- Added graph node/edge list/get/delete coverage.

`backend/tests/unit/test_config_service.py`

- New tests for:
  - node CRUD,
  - relationship creation/removal,
  - task assignment/removal,
  - check-in preference update/list,
  - duplicate conflict handling,
  - directory relationship and rollup projection.

`backend/tests/unit/test_authorization.py`

- Added authorization coverage for `READ_DIRECTORY` and `MANAGE_CONFIG`.

`backend/tests/unit/test_persona_views.py`

- Added coverage for dynamic portfolio root selection from configured program nodes.

`backend/tests/unit/test_api.py`

- Added test-local graph fixture helper.
- Updated demo-data-dependent tests to populate fixture graph data explicitly.
- Added memory startup test proving app no longer populates demo data automatically.
- Added admin config CRUD API test that populates directory and dashboards.
- Updated that admin config API test to query current-date dashboard data so `valid_from=today` pod membership links are active.
- Added authorization test proving config routes are admin-only while directory routes are readable.

## Frontend Completed So Far

### Dependencies

Ran from `frontend/`:

```sh
npm install react-hook-form zod @hookform/resolvers
```

Effects:

- Updated `frontend/package.json`.
- Updated `frontend/package-lock.json`.
- Added 4 packages.
- npm reported 2 moderate vulnerabilities. No audit fix was run.

### OpenAPI and Generated Client

Ran:

```sh
PYTHONPATH=backend uv run python -m api.openapi
cd frontend && npm run generate:client
```

Effects:

- Updated `frontend/src/api/openapi.json`.
- Updated `frontend/src/api/generated.ts`.

### API Schema Exports

`frontend/src/api/schema.ts`

Added exports for:

- `ConfigNodeResponse`
- `ConfigNodeCreateRequest`
- `ConfigNodeUpdateRequest`
- `ConfigEdgeResponse`
- `DirectoryItemResponse`
- `PodMemberLinkRequest`
- `ProgramProjectLinkRequest`
- `MemberTaskAssignmentRequest`

### API Client Wrapper

`frontend/src/api/client.ts`

Added:

- `204` handling in `requestJson`.
- Directory calls:
  - `programs`
  - `projects`
  - `pods`
- Config CRUD calls for:
  - programs,
  - projects,
  - pods,
  - members.
- Config relationship calls:
  - link/unlink project to program,
  - link/unlink pod to project,
  - link/unlink member to pod,
  - assign/unassign task to member.
- Check-in preference calls:
  - get member config preference,
  - update member config preference,
  - list config preferences.

## Frontend Completed (This Pass)

### UI Primitives

Added under `frontend/src/components/ui/`:

- `input.tsx`
- `select.tsx`
- `textarea.tsx`
- `slider.tsx`
- `dialog.tsx`
- `field.tsx`

### App Shell + Role Context

- `frontend/src/app/Layout.tsx` — sidebar navigation (dashboards, pods, projects, portfolio, admin).
- `frontend/src/app/RoleContext.tsx` — localStorage-backed role switcher; gates Portfolio (exec/admin) and Admin (admin).

### Routes

Updated `frontend/src/App.tsx` with nested layout routes:

- `/me`, `/sm`, `/po`, `/exec` — persona dashboards
- `/pods`, `/pods/:podId`
- `/projects`, `/projects/:projectId`
- `/portfolio`
- `/admin`

### Dynamic Persona Dashboard

`frontend/src/features/personas/PersonaDashboard.tsx`:

- Loads directory lists for pods/projects/programs.
- Provides selectors in the header; defaults to first configured item.
- Removed hardcoded `pod-runtime`, `project-foundations`, `program-platform`.

### New Pages

- `frontend/src/pages/PodsPage.tsx`
- `frontend/src/pages/PodDetailPage.tsx`
- `frontend/src/pages/ProjectsPage.tsx`
- `frontend/src/pages/ProjectDetailPage.tsx`
- `frontend/src/pages/PortfolioPage.tsx`
- `frontend/src/pages/AdminConfigPage.tsx` — CRUD, links/assignments, check-in preferences.

### Selection Helper

- `frontend/src/lib/selection.ts` — shared first-item / resolve-selection logic.

## Frontend Not Done Yet

- Dedicated programs list/detail pages (programs are selectable on Portfolio/Exec dashboards and manageable in Admin; no standalone programs browse page).
- Frontend unit/component tests (no vitest/jest harness in repo).
- Manual smoke: start empty backend, configure via Admin UI, confirm dashboards populate.

## Verification Already Run

Python formatting:

```sh
uv run ruff format backend/core/ports/repositories.py backend/infra/persistence/in_memory_graph.py backend/infra/persistence/postgres_graph.py backend/infra/persistence/postgres_status.py backend/core/application/config_service.py backend/core/application/authorization.py backend/core/application/persona_views.py backend/api/dependencies.py backend/api/dtos.py backend/api/main.py backend/api/routers/config.py backend/tests/contract/contracts.py backend/tests/contract/fakes.py backend/tests/unit/test_graph_store.py backend/tests/unit/test_config_service.py backend/tests/unit/test_authorization.py backend/tests/unit/test_persona_views.py backend/tests/unit/test_api.py
```

Result:

- Completed.
- 4 files reformatted, 14 unchanged.

Python lint for touched files:

```sh
uv run ruff check backend/core/ports/repositories.py backend/infra/persistence/in_memory_graph.py backend/infra/persistence/postgres_graph.py backend/infra/persistence/postgres_status.py backend/core/application/config_service.py backend/core/application/authorization.py backend/core/application/persona_views.py backend/api/dependencies.py backend/api/dtos.py backend/api/main.py backend/api/routers/config.py backend/tests/contract/contracts.py backend/tests/contract/fakes.py backend/tests/unit/test_graph_store.py backend/tests/unit/test_config_service.py backend/tests/unit/test_authorization.py backend/tests/unit/test_persona_views.py backend/tests/unit/test_api.py
```

Result:

- Passed.
- `All checks passed!`

OpenAPI generation:

```sh
PYTHONPATH=backend uv run python -m api.openapi
```

Result:

- Passed.
- Wrote `frontend/src/api/openapi.json`.

Frontend generated client:

```sh
cd frontend && npm run generate:client
```

Result:

- Passed.
- Updated `frontend/src/api/generated.ts`.

Focused backend unit/contract suite:

```sh
PYTHONPATH=backend uv run pytest backend/tests/unit/test_config_service.py backend/tests/unit/test_graph_store.py backend/tests/unit/test_persona_views.py backend/tests/unit/test_api.py backend/tests/unit/test_authorization.py backend/tests/contract --no-cov
```

Result:

- Passed after the API test date fix.
- `55 passed, 1 warning`.

## Verification Still Needed

Broader backend verification:

```sh
PYTHONPATH=backend uv run pytest --no-cov
```

Python checks:

```sh
uv run ruff format --check .
uv run ruff check .
PYTHONPATH=backend uv run mypy
```

OpenAPI/client sync after any further API changes:

```sh
PYTHONPATH=backend uv run python -m api.openapi
cd frontend && npm run generate:client
```

Frontend checks:

```sh
cd frontend && npm run typecheck
cd frontend && npm run build
```

Result (2026-06-18):

- `npm run typecheck` — passed.
- `npm run build` — passed (chunk size warning only).

## Known Risks And Attention Points

- The Postgres AGE delete helpers need validation against a real AGE-enabled database.
- Focused backend unit/contract tests now pass, but full backend tests have not been run after all changes.
- Frontend typecheck/build now pass after UI implementation.
- `DELETE /config/members/{member_id}/tasks` uses query parameter `task_id`; confirm this matches product expectations.
- npm reported 2 moderate vulnerabilities after installing frontend dependencies. No audit fix was run to avoid unrelated dependency churn.
- `backend/infra/phase1_smoke.py` and `docker-compose.yml` were already dirty before this implementation. Avoid mixing unrelated changes into this work.
- Config router and DTOs were generated into OpenAPI, but route naming and generated helper names should be checked during frontend compilation.
- Removing automatic memory-mode demo data population is intentional for runtime-config support; smoke flows create only the data they need locally.

## Suggested Resume Order

1. Manual smoke: empty backend → Admin config → verify Pods/Projects/dashboards/portfolio.
2. Run broader backend verification (`uv run pytest --no-cov`, ruff, mypy) if desired before merge.
3. Optional: add programs list/detail page and frontend test harness.

## Files Most Likely To Need Next Edits

Backend:

- `backend/api/routers/config.py`
- `backend/core/application/config_service.py`
- `backend/tests/unit/test_api.py`
- `backend/tests/unit/test_config_service.py`

Frontend:

- `frontend/src/App.tsx`
- `frontend/src/pages/PersonaDashboard.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/api/schema.ts`
- `frontend/src/components/ui/*`
- New pages under `frontend/src/pages/*`

Generated artifacts:

- `frontend/src/api/openapi.json`
- `frontend/src/api/generated.ts`

## Do Not Lose These Implementation Decisions

- Runtime config should be the source of truth for selectable programs/projects/pods/members.
- Demo graph fixture population should be explicit in tests, and smoke flows should create only the data they need.
- Admin config writes require `MANAGE_CONFIG`; directory reads require `READ_DIRECTORY`.
- The directory endpoints are intentionally separate from admin config endpoints so non-admin users can navigate configured runtime entities.
- Keep config service logic in application/core and persistence details behind ports.
