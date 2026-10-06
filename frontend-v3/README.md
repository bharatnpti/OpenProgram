# frontend-v3: project reports

A separate app for reading a project's reports, on the same backend, API and
sign-in as the console in `frontend-v2/`. Two views per project:

- **Daily**: today's end-of-day report, built live by the backend exactly as it
  would be sent (`/day-reports/{id}/preview`), plus when it goes out, to whom,
  and every past send. Send now and today's note appear only when the server
  says this reader may (`can_send`, `can_write_note`).
- **Overall**: the project's state since it started. Delivery date and forecast,
  requirements by stage with a 30-day timeline, the acceptance gates and each
  requirement against them, risks and drift, the escalation matrix, and every
  question kept from Jira.

Setting reports up, and the admin tabs the reports rest on (Delivery stages,
Gates, Escalation, Integrations), stay in the console.

## Run it

```bash
cd frontend-v3
npm install
npm run dev          # http://127.0.0.1:5175, against the API on :8000
```

| Variable            | Default                 | Purpose                                      |
| ------------------- | ----------------------- | -------------------------------------------- |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Backend to call                              |
| `VITE_CONSOLE_URL`  | `http://127.0.0.1:5174` | Where "Open console" and the set-up links go |

The backend's CORS allowlist includes port 5175 by default
(`backend/config/settings.py`, `.env.example`). Port 5175 is strict: if it is
taken, Vite fails instead of drifting to a port CORS would block.

On a local dev-auth tenant the header carries the same acting-as picker as the
console, and the choice is shared with it (same `localStorage` keys). Under OIDC
the token decides the role and only the name and Sign out remain.

To make the day report's "Open in OpenProgram" link land here instead of the
console, set `OPENPROGRAM_CONSOLE_URL=http://127.0.0.1:5175` on the backend.

### Without the backend

```bash
npm run mock         # builds with a same-origin API and serves it on http://127.0.0.1:5175
```

`scripts/mock-api.mjs` answers every endpoint the app calls with demo-shaped
data (Checkout Revamp only) and honours the acting-as role, so each person in
the header picker sees their own view. The screenshots in `docs/screenshots/`
were taken this way with `scripts/screenshots.py`; they are not real data.

## Routes

| Path                           | View                                                                                          |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| `/`                            | Projects, worst first                                                                         |
| `/projects/:projectId/daily`   | Daily report; `?report=` picks one when a project has several (whole project, or one release) |
| `/projects/:projectId/overall` | Overall state                                                                                 |

## Who sees what

The app mirrors the backend's capabilities only to say up front which role
opens a panel. The backend still decides, and every panel also handles a 403
with the server's reason.

| Panel                                             | Endpoint                                                 | Readers                                          |
| ------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------ |
| Daily report, past sends                          | `/day-reports…`                                          | everyone                                         |
| Delivery date and forecast, requirements by stage | `/projects/{id}/delivery`, `/projects/{id}/requirements` | product owner, manager, executive, admin         |
| Acceptance gates, questions                       | `/projects/{id}/gates`                                   | everyone (project-progress read or gate editing) |
| Risks and drift                                   | `/projects/{id}/risks`                                   | everyone but the developer                       |
| Escalation matrix                                 | `/config/escalation/projects/{id}`                       | admin                                            |

## Known gaps

- **Burn-down is by count, not story points.** The requirements timeline keeps
  stage counts per day, not points. A points burn-down needs the daily snapshot
  to store points per stage too (backend change in the requirements snapshot and
  `RequirementTimelinePointResponse`).
- **Overall is five requests.** Fine with parallel queries; add an aggregate
  endpoint only if it proves slow.

## Checks

```bash
npm run lint && npm run typecheck && npm run format:check
npm test             # node --test on the pure helpers; needs Node 22.18+
npm run build
```

`make frontend-v3-lint frontend-v3-build` does the same from the repo root, and
`make openapi-check` regenerates `src/api/generated.ts` here with the other two
clients. `src/app/RoleProvider.tsx` and `src/api/client.ts` are trimmed ports of
frontend-v2's; keep identity and request rules in step when either changes.
