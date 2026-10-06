# frontend-v3: the role-based console, rebuilt from the design

A new console on the same backend, API and sign-in as `frontend-v2/`, built
fresh from the "OpenProgram by Role" design: one top navigation on every
screen, and a Today that is different for each role.

| Screen                                | What it shows                                                                                                                                                      | Reads                                                                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| **Today** (developer)                 | Your check-in with Confirm and Correct details, Focus today, your tasks, Waiting on you, where your check-in rolls up                                              | `/me/status`, `/me/focus`, `/me/cross-person-requests`, directory                                                   |
| **Today** (scrum master)              | Pod chips, check-ins today, open blockers oldest first, why the pod has its colour, Waiting on you                                                                 | `/pods/{id}/checkins`, `/blockers`, `/rollup`                                                                       |
| **Today** (product owner)             | Project chips, progress ring and counts, workstreams worst first, tasks that need attention, Waiting on you                                                        | `/projects/{id}/progress`, `/projects/{id}/workstreams`                                                             |
| **Today** (manager, executive, admin) | Verdict with its reason, 30-day momentum, newest executive brief, portfolio heat, oldest open risks                                                                | `/portfolio/attention`, `/persona/program/{id}/trend`, `/persona/briefs`, directory                                 |
| **Delivery**                          | Navigator of programs, projects, workstreams and pods; a panel per kind with status, reason, related links, progress, check-ins, blockers, tasks                   | `/programs/{id}/tree`, `/projects/{id}/progress`, `/workstreams/{id}/progress`, `/pods/{id}/*`                      |
| **Signals**                           | Risks, drift and watermelons ("owner says" beside "signals say"), flow, 7-day activity; filter Everything / Risks / Drift / Flow / Feed                            | `/portfolio/risks`, `/portfolio/flow`, `/portfolio/feed`                                                            |
| **Coordination**                      | Requests board (Open, Acknowledged, Needs resolution) with Acknowledge / Resolve, Waiting on you, Raised by you, briefs with a kind filter, Ask the graph          | `/portfolio/cross-person-requests`, `/me/cross-person-requests`, `/persona/briefs`, `/ask`                          |
| **Reports**                           | Every project with **Daily** and **Overall**; set up, change or remove a day report                                                                                | `/day-reports…`, `/projects/{id}/delivery`, `/requirements`, `/gates`, `/risks`, `/config/escalation/projects/{id}` |
| **Chat**                              | Your check-in thread; an admin gets every thread, can ask someone for a check-in and clear history                                                                 | `/test/chat-simulator/*`, `/admin/workflows/checkin/dispatch`                                                       |
| **Admin**                             | Check-in preferences with defaults and write-back consent, data source health, entities (add programs, projects, workstreams, pods); the rest links to the console | `/config/*`, `/admin/ops/sync-status`                                                                               |

A destination the viewing role is not offered stays in the navigation, struck
through (Signals for a developer, Admin for everyone but an admin). Chat appears
only when the backend serves the built-in chat. Every panel a role cannot read
says which role opens it; the backend still decides, and a 403 shows its reason.

## Run it

```bash
cd frontend-v3
npm install
npm run dev          # http://127.0.0.1:5175, against the API on :8000
```

| Variable            | Default                 | Purpose                                         |
| ------------------- | ----------------------- | ----------------------------------------------- |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Backend to call (empty = same origin)           |
| `VITE_CONSOLE_URL`  | `http://127.0.0.1:5174` | Where links to settings still in frontend-v2 go |

The backend's CORS allowlist includes port 5175 by default. The port is strict:
if it is taken, Vite fails instead of drifting to a port CORS would block. On a
local dev-auth tenant the header has the acting-as and lens pickers, sharing the
console's `localStorage` keys; under OIDC the token decides.

### Without the backend

```bash
npm run mock         # builds with a same-origin API and serves it on http://127.0.0.1:5175
```

`scripts/mock-api.mjs` (reports) and `scripts/mock-console.mjs` (every other
screen) answer the endpoints above with demo-shaped data and honour the
acting-as role, so each person in the picker sees their own view. The
screenshots in `docs/screenshots/` were taken this way with
`scripts/screenshots.py`; they are not real data.

## Routes

| Path                                                                           | Screen                                                        |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| `/today`                                                                       | Today for the viewing role                                    |
| `/delivery`, `/delivery/:kind/:id`                                             | Delivery explorer (`kind`: program, project, workstream, pod) |
| `/signals?view=`                                                               | Signals (`risks`, `drift`, `flow`, `feed`)                    |
| `/coordination?brief=`                                                         | Coordination (`exec`, `weekly_project`, `daily_pod`)          |
| `/reports`, `/reports/:projectId/daily?report=`, `/reports/:projectId/overall` | Reports                                                       |
| `/chat`                                                                        | Chat                                                          |
| `/admin?tab=`                                                                  | Admin (`checkins`, `sources`, `entities`, `more`)             |

`/projects/:id/daily|overall` from the first scaffold redirect to `/reports/…`.

## Not in this app yet

- Admin's Links, Directory, Delivery stages, Gates, Escalation, Integrations and
  Branding tabs link to the console.
- Viewing a past day (`?asOf=`), the ⌘K palette, and the developer's own
  check-in schedule from the avatar menu.
- Burn-down by story points (the API keeps counts per stage per day, not points).
- Escalation matrix and drift findings show member ids where the API gives no name.

## Checks

```bash
npm run lint && npm run typecheck && npm run format:check
npm test             # node --test on the pure helpers; needs Node 22.18+
npm run build
```

`src/api/client.ts` and `src/api/schema.ts` are copies of frontend-v2's;
`src/app/RoleProvider.tsx` is a trimmed port. Keep identity and request rules in
step when either changes. `make openapi-check` regenerates `src/api/generated.ts`.
