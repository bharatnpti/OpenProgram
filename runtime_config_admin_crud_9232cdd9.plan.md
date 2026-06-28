---
name: Runtime config admin CRUD
overview: "Replace hardcoded sample IDs with a runtime configuration capability: admin CRUD APIs (DB-persisted via the existing graph + checkin_preferences tables) plus an Admin UI to manage programs, projects, pods, members/roles, hierarchy links, assignments and check-in timing, and make every dashboard/section render dynamically from that configuration."
todos:
  - id: repo-crud
    content: Extend GraphRepository port + in-memory + postgres impls with list_nodes/get_node/delete_node/list_edges/remove_edge; add StatusRepository.list_checkin_preferences (+ impls).
    status: pending
  - id: config-service
    content: Add core/application/config_service.py ConfigService with CRUD + edge link/assign + validation.
    status: pending
  - id: authz-cap
    content: Add Capability.MANAGE_CONFIG (admin-only) in authorization.py.
    status: pending
  - id: config-router
    content: Add api/routers/config.py admin-gated CRUD endpoints for programs/projects/pods/members/links/assignments/check-in timing; wire DTOs, DI, and main.py registration.
    status: pending
  - id: directory-endpoints
    content: Add read-only GET /programs, /pods, /projects directory list endpoints for dashboards.
    status: pending
  - id: dynamic-portfolio-root
    content: Make portfolio root dynamic in persona_views.py (derive from configured programs) instead of PORTFOLIO_ROOT_ID constant.
    status: pending
  - id: remove-sample-bootstrap
    content: Remove automatic demo data from main.py lifespan and keep sample graph data test-local.
    status: pending
  - id: openapi
    content: Regenerate frontend openapi.json + run generate:client; export new types in schema.ts.
    status: pending
  - id: fe-foundation
    content: Install react-hook-form+zod; add UI primitives (input/select/textarea/slider/dialog/field); add Layout+sidebar, RoleContext+switcher, and new apiClient methods.
    status: pending
  - id: fe-pods-projects
    content: Build Pods list/detail and Projects list/detail pages driven by directory + existing persona endpoints.
    status: pending
  - id: fe-admin
    content: Build Admin configuration screens (CRUD forms/tables for programs/projects/pods/members/hierarchy/links/assignments + per-member check-in timing).
    status: pending
  - id: fe-dynamic-dash
    content: Make PersonaDashboard dynamic via selectors from directory lists; remove all hardcoded IDs.
    status: pending
  - id: verify
    content: Add/adjust backend tests for repo+service+router, update fixture-dependent tests; run frontend typecheck/build; manual config->dashboard smoke.
    status: pending
isProject: false
---

# Runtime Configuration: Admin CRUD + Dynamic Dashboards

## Approach
- Reuse existing storage as the config store: programs/projects/pods/developers/tasks are `graph_nodes`; memberships, pod-project links and assignments are `graph_edges` (with `metadata.role` for "role in pod"); check-in timing uses the existing `checkin_preferences` table. No new DB tables/migrations.
- Add CRUD on the repository layer (currently only `upsert_node`/`add_edge`/`get_program_tree` exist), an application `ConfigService`, an admin-gated `config` router, read-only directory list endpoints for dashboards, and an Admin UI + dynamic dashboards.
- Remove startup demo data and all hardcoded IDs (`program-platform`, `pod-runtime`, `project-foundations`).

## Out of scope this pass (note for later)
- Web "Daily Check-in" submit form + AI parse endpoint (check-ins are chat-based today).
- App user/profile RBAC management ("list users, add/remove roles") — there is no user table; "members" here = developer graph nodes with a role-in-pod label. Principal roles still come from `dev_principal_roles`.

## Phase 1 - Repository + domain foundation
- Extend `GraphRepository` Protocol in [backend/core/ports/repositories.py](backend/core/ports/repositories.py): `list_nodes(tenant_id, kind=None)`, `get_node(tenant_id, id)`, `delete_node(tenant_id, id)`, `list_edges(tenant_id, from_node_id=None, to_node_id=None, kind=None)`, `remove_edge(edge)`.
- Implement in [backend/infra/persistence/in_memory_graph.py](backend/infra/persistence/in_memory_graph.py) (dict/list filters) and [backend/infra/persistence/postgres_graph.py](backend/infra/persistence/postgres_graph.py) (SELECT/DELETE on `graph_nodes`/`graph_edges`).
- Extend `StatusRepository` with `list_checkin_preferences(tenant_id)` (+ in-memory and `postgres_status.py` impls) so admin can list everyone's timing.

## Phase 2 - Application + API
- New `core/application/config_service.py` `ConfigService`: CRUD for programs/projects/pods/members + edge ops (add/remove CONTAINS for program-project, project-pod links, pod-member with role; ASSIGNED_TO for member-task), with validation (referenced nodes exist; reject self/duplicate links). Membership writes set `valid_from=today`; removal hard-deletes the edge.
- Add `Capability.MANAGE_CONFIG` to [backend/core/application/authorization.py](backend/core/application/authorization.py) (ADMIN-only; ADMIN already short-circuits to allowed).
- New router [backend/api/routers/config.py](backend/api/routers/config.py) (all gated on `MANAGE_CONFIG`):
  - `GET/POST /config/programs`, `PUT/DELETE /config/programs/{id}`
  - `GET/POST /config/projects`, `PUT/DELETE /config/projects/{id}` (name, description, code metadata)
  - `GET/POST /config/pods`, `PUT/DELETE /config/pods/{id}` (name, description)
  - `GET/POST /config/members`, `PUT/DELETE /config/members/{id}`
  - link/assign: `POST/DELETE /config/projects/{id}/program`, `/config/pods/{id}/projects/{projectId}`, `/config/pods/{id}/members/{memberId}` (body: role), `/config/members/{id}/tasks`
  - check-in timing: `GET /config/members/{id}/checkin-preference`, `PUT /config/members/{id}/checkin-preference`, plus `GET /config/checkin-preferences` (list).
- Read-only directory endpoints for dashboards (new lightweight `DirectoryService` or extend `PersonaViewService`): `GET /programs`, `GET /pods`, `GET /projects` returning id/name/description (+ RAG from rollups where relevant). Gate with a permissive read capability (e.g. `READ_TEAM_AGGREGATE`) or a new `READ_DIRECTORY`.
- Make portfolio root dynamic: replace `PORTFOLIO_ROOT_ID = "program-platform"` in [backend/core/application/persona_views.py](backend/core/application/persona_views.py) with "first configured program" lookup via `list_nodes(kind=program)`.
- DTOs (request/response) in [backend/api/dtos.py](backend/api/dtos.py); DI wiring in [backend/api/dependencies.py](backend/api/dependencies.py); register router in [backend/api/main.py](backend/api/main.py).
- Remove demo data population from the `lifespan` in [backend/api/main.py](backend/api/main.py) (no hardcoded data on boot). Keep any sample graph data under test fixtures only.
- Regenerate `frontend/src/api/openapi.json` from the app and update generated types.

## Phase 3 - Frontend foundation
- Install `react-hook-form` + `zod` + `@hookform/resolvers`. Add UI primitives in `frontend/src/components/ui/`: `input.tsx`, `select.tsx`, `textarea.tsx`, `slider.tsx`, `dialog.tsx`, `field.tsx` (matching existing Badge/Button style + `cn`).
- Add `frontend/src/app/Layout.tsx` with a sidebar (Dashboard, Pods, Projects, Portfolio, Admin) using `<Outlet/>`; nest routes under it in [frontend/src/App.tsx](frontend/src/App.tsx).
- Add `RoleContext` (+ localStorage) and a role switcher in the sidebar; gate Portfolio/Admin items by role (Portfolio: mgr/exec; Admin: admin).
- Extend [frontend/src/api/client.ts](frontend/src/api/client.ts) with config CRUD + directory list methods; re-run `npm run generate:client` and export types in `schema.ts`.

## Phase 4 - Frontend pages + dynamic dashboards
- Pods: list page (`GET /pods`) + detail page (members with role-in-pod, related projects, recent check-ins via existing `/pods/{id}/checkins` and `/pods/{id}/blockers`, rollups).
- Projects: list page (`GET /projects`, RAG badges) + detail page (progress via `/projects/{id}/progress`, associated pods, open blockers, rollups).
- Admin config screens: forms/tables to create/edit/delete programs, projects, pods, members; manage hierarchy links, pod-project links, member roles, task assignments; and per-member check-in timing (time + weekdays + waits) using the new sliders/inputs.
- Make [frontend/src/features/personas/PersonaDashboard.tsx](frontend/src/features/personas/PersonaDashboard.tsx) dynamic: drive SM/PO/Exec queries from selectors populated by directory lists; remove hardcoded `pod-runtime`/`project-foundations`/`program-platform` and the `pod-runtime` badge.

## Phase 5 - Verify
- Backend: unit tests for new repo methods, `ConfigService`, and `config` router (create -> appears in directory -> dashboards resolve -> delete). Update/remove existing tests that assumed automatic demo data (e.g. [backend/tests/unit/test_persona_views.py](backend/tests/unit/test_persona_views.py), [backend/tests/unit/test_api.py](backend/tests/unit/test_api.py), [backend/infra/phase1_smoke.py](backend/infra/phase1_smoke.py)).
- Frontend: `npm run typecheck` + `npm run build`.
- Manual smoke: start empty, configure a program/project/pod/member + check-in time in Admin UI, confirm Pods/Projects/dashboards/portfolio populate from config.

## Assumptions (adjust in review)
- Config is modeled on the existing graph (no new tables). If you want dedicated config tables instead, that adds migrations.
- "Members" = developer nodes with a role-in-pod label; principal RBAC unchanged.
- Admin endpoints are gated by `MANAGE_CONFIG` (ADMIN). The default dev principal is `admin`, so it will have access (the spec's "block DEV user" implies changing `dev_principal_roles`).
