# frontend-v3: the role-based console, rebuilt from the design

A new console on the same backend, API and sign-in as `frontend-v2/`, built
fresh from the "OpenProgram by Role" design: one top navigation on every
screen, a Today that is different for each role, and every setting in Admin.
Where the build stands, what is still open and how it was checked:
[`../frontend-v3.md`](../frontend-v3.md).

## Screens

| Screen                                | What it shows                                                                                                                                                                                                                      | Reads                                                                                                                                                                                       |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Today** (developer)                 | Your check-in (which day it is from, how it stands) with Confirm and Correct details, Focus today, your tasks, Waiting on you, where your check-in rolls up                                                                        | `/me/status` (and `confirm`, `correct`), `/me/focus`, `/me/cross-person-requests`, directory                                                                                                |
| **Today** (scrum master)              | Pod chips (the pods you run), check-ins today, open blockers oldest first, why the pod has its colour, Waiting on you                                                                                                              | `/pods/{id}/checkins`, `/blockers`, `/rollup`                                                                                                                                               |
| **Today** (product owner)             | Project chips (worst first), progress ring and counts, workstreams worst first, tasks that need attention, Waiting on you                                                                                                          | `/projects/{id}/progress`, `/projects/{id}/workstreams`                                                                                                                                     |
| **Today** (manager, executive, admin) | The verdict with its reason (a program picker when there are several), 30-day momentum, the newest executive brief, portfolio heat with a reason under each tile and a "no pod" row, oldest open risks                             | `/portfolio/attention`, `/portfolio/heatmap`, `/persona/program/{id}/trend`, `/persona/briefs`, directory                                                                                   |
| **Delivery**                          | Navigator of programs, projects, workstreams and pods; a panel per kind with status, every reason once and by name, related links, progress, check-ins, blockers, tasks and the pod's delivery date                                | `/programs/{id}/tree`, `/projects/{id}/progress`, `/workstreams/{id}/progress`, `/pods/{id}/*`, `/pods/{id}/delivery`                                                                       |
| **Signals**                           | Risks, drift and watermelons ("owner says" beside "signals say"), flow, 7-day activity; filter Everything / Risks / Drift / Flow / Feed                                                                                            | `/portfolio/risks`, `/portfolio/flow`, `/portfolio/feed`                                                                                                                                    |
| **Coordination**                      | Requests board (Open, Acknowledged, Needs resolution) with Acknowledge and Resolve for the people on a request, Waiting on you, Raised by you, briefs with a kind filter, Ask the graph                                            | `/portfolio/cross-person-requests`, `/me/cross-person-requests`, `/cross-person-requests/{id}/status`, `/persona/briefs`, `/ask`                                                            |
| **Reports**                           | Every project, worst first, with **Daily** and **Overall**; set up, change or remove a day report                                                                                                                                  | `/day-reports…`, `/projects/{id}/delivery`, `/pods/{id}/delivery`                                                                                                                           |
| ↳ **Daily**                           | Schedule, audience, Send now, today's note, the report as it will be sent, past sends with each destination's outcome                                                                                                              | `/day-reports/{id}/preview`, `/runs`, `/send`, `/note`                                                                                                                                      |
| ↳ **Overall**                         | Delivery date and forecast (set the project's, a release's or a pod's date), burn-down, requirements by stage, acceptance gates and questions (keep, dismiss, sign off, add), risks and drift, escalation matrix, scope by release | `/projects/{id}/requirements`, `/gates`, `/risks`, `/releases`, `/release-candidates`, `/config/escalation/projects/{id}`; writes `/delivery-date`, `/gate-items/{id}/*`, `/questions/{id}` |
| **Chat**                              | Your check-in thread; an admin gets every thread, can ask someone for a check-in and clear history                                                                                                                                 | `/test/chat-simulator/*`, `/admin/workflows/checkin/dispatch`                                                                                                                               |
| **Admin**                             | Eleven tabs, below: check-ins, where data comes from and goes to, how delivery is counted and escalated, branding, and the hierarchy with its links and people                                                                     | `/config/*`, `/admin/ops/sync-status`                                                                                                                                                       |

A destination the viewing role is not offered stays in the navigation, struck
through (Signals for a developer, Admin for everyone but an admin). Chat appears
only when the backend serves the built-in chat. Every panel a role cannot read
says which role opens it; the backend still decides, and a 403 shows its reason.

### Admin tabs (`/admin?tab=`)

| Tab            | What you do                                                                                                                                                | Reads and writes                                                                                                            |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `checkins`     | Every member's days, time zone and reply waits, marked default or own (put one back to the team's); write-back consent per member and the tenant switch    | `/config/checkin-preferences`, `/config/members/{id}/checkin-preference`, `…/writeback-consent`, `/config/tenant/writeback` |
| `sources`      | Health, schedule in words, last and next run, errors and targets of every data source; sync the chat directory now                                         | `/admin/ops/sync-status`, `POST /config/directory/sync`                                                                     |
| `integrations` | Set up, change, test and remove what OpenProgram reads from and sends through: Slack, email, Teams; Jira, GitLab, GitHub; calendar. Secrets are write-only | `/config/integrations`, `…/{connector}`, `…/{connector}/test`                                                               |
| `stages`       | Which of the six delivery steps each tracker status counts as, and which issue types are requirements                                                      | `/config/delivery/stages`, `/config/delivery/statuses`, `…/statuses/preview`                                                |
| `gates`        | The checks a requirement passes before a step, and who signs each off                                                                                      | `/config/gates`                                                                                                             |
| `escalation`   | The matrix: who decides, who is told after how many days for each kind of ask; the tenant's, or a project's own                                            | `/config/escalation`, `…/tenant`, `…/projects/{id}`                                                                         |
| `contacts`     | Per pod, the scrum master and manager that a missed check-in or a waiting ask reaches                                                                      | `/config/pods/{id}/escalation-contacts`, `…/escalation-candidates`                                                          |
| `branding`     | Upload or remove the header logo (PNG, JPEG or WebP, up to 256 KB)                                                                                         | `/config/branding`, `…/logo`                                                                                                |
| `entities`     | Add, change and delete programs, projects, pods and workstreams (optional); the confirmation says what leaves and what stays                               | `/config/{programs,projects,pods,workstreams}`                                                                              |
| `links`        | What is linked to what: projects in a program, pods on a project, people in a pod with a role, workstreams (optional), who holds which task                | `/config/projects/{id}/program`, `/config/pods/{id}/…`, `/config/members/{id}/tasks`                                        |
| `directory`    | Sync the chat directory, import people (one by one, nobody pre-ticked), each member's chat, Jira and Git accounts, fill in from the directory              | `/config/directory/*`, `/config/members/from-directory`, `…/identity-link`, `…/unmapped`, `…/auto-match`                    |

Every tab is for an admin; others see which role opens it. A link or a delete
ends the link from today and leaves earlier days as they were.

### Everywhere

| Piece                 | What it does                                                                                                                                                                                                                                                                        | Reads                                                   |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Jump (⌘K, Ctrl+K)     | A palette over the screens the role is offered, programs, projects, workstreams, pods and people (a person opens their first pod). Keyboard only; it keeps the day shown                                                                                                            | directory                                               |
| A past day (`?asOf=`) | The day field in the header, an amber banner with Back to today, and every screen read-only. Reads that take `as_of` get it; the few that do not (the activity feed, briefs, requests, day reports, chat) show the present and say so. Admin always shows the current configuration | `as_of` on the 27 dated reads, set in `src/api/asOf.ts` |
| Acting as, lens       | Local sign-in only: who you act as (people grouped by role) and which of your roles you view through; the same `localStorage` keys as frontend-v2. Under OIDC the token decides, and the avatar menu has Sign out                                                                   | `/api/v1/auth/status`, `/api/v1/auth/dev-users`         |
| Avatar menu           | The account line, and your own check-in schedule: days and time zone, your own or the team's                                                                                                                                                                                        | `/me/checkin-preference`                                |
| Tenant logo           | The header shows the tenant's logo, or the OpenProgram mark                                                                                                                                                                                                                         | `/config/branding`                                      |

## Run it

```bash
cd frontend-v3
npm install
npm run dev          # http://127.0.0.1:5175, against the API on :8000
```

| Variable                                                   | Default                            | Purpose                                           |
| ---------------------------------------------------------- | ---------------------------------- | ------------------------------------------------- |
| `VITE_API_BASE_URL`                                        | `http://127.0.0.1:8000`            | Backend to call (empty = same origin)             |
| `VITE_AUTH_CSRF_COOKIE_NAME`, `VITE_AUTH_CSRF_HEADER_NAME` | `openprogram_csrf`, `x-csrf-token` | The cookie and header of the backend's CSRF check |

The backend's CORS allowlist includes port 5175 by default. The port is strict:
if it is taken, Vite fails instead of drifting to a port CORS would block. On a
local dev-auth tenant the header has the acting-as and lens pickers; under OIDC
the token decides. For a backend with demo data, see
[`docs/ops/local-demo.md`](../docs/ops/local-demo.md).

### Without the backend

```bash
npm run mock         # builds with a same-origin API and serves it on http://127.0.0.1:5175
```

`scripts/mock-api.mjs` (day reports, risks), `scripts/mock-console.mjs` (the
base data for the other screens) and the stateful mocks in `scripts/mock/`
(`today`, `shell`, `reports`, `admin-structure`, `admin-config`) answer the
endpoints above with demo-shaped data, follow `src/api/generated.ts`, and honour
the acting-as role, so each person in the picker sees their own view and a
change reads back until the server restarts. Pick Kai Thompson (developer), Ira
Novak (scrum master), Mina Patel (product owner), Asha Rao (manager; switch the
lens to Admin) or Elena Fischer (executive). Extend the mocks when a screen
gains an endpoint.

### Screenshots

The pictures in `docs/screenshots/` come from this mock and never from a real
tenant: the repository is public.

```bash
npm run mock                                                # here, and leave it running
uv run python frontend-v3/scripts/screenshots.py            # from the repository root
uv run python frontend-v3/scripts/screenshots.py --only 17 26   # just some of them
uv run playwright install chromium                          # once, if Chromium is missing
```

`scripts/screenshots.json` lists each shot: the path, who is acting, a width
(1440 px, and one phone at 390 px) and the clicks that open the palette or a
dialog. The script drives Playwright's Chromium with the clock fixed to the
mock's day and the zone to UTC, so every run shows the same day and times, and
it refuses a server whose people are not the demo roster.

## Routes

| Path                                                                                    | Screen                                                                                                                                   |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `/today`                                                                                | Today for the viewing role (`?program=` picks a program when there are several)                                                          |
| `/delivery`, `/delivery/:kind/:id`                                                      | Delivery explorer (`kind`: program, project, workstream, pod)                                                                            |
| `/signals?view=`                                                                        | Signals (`everything`, `risks`, `drift`, `flow`, `feed`)                                                                                 |
| `/coordination?brief=`                                                                  | Coordination (`exec`, `weekly_project`, `daily_pod`)                                                                                     |
| `/reports`, `/reports/:projectId/daily?report=`, `/reports/:projectId/overall?release=` | Reports; `report` picks one of a project's day reports, `release` scopes Overall to a release                                            |
| `/chat`                                                                                 | Chat                                                                                                                                     |
| `/admin?tab=`                                                                           | Admin (`checkins`, `sources`, `integrations`, `stages`, `gates`, `escalation`, `contacts`, `branding`, `entities`, `links`, `directory`) |
| any of the above with `?asOf=YYYY-MM-DD`                                                | The same screen for a past day, read-only                                                                                                |
| `/logged-out`                                                                           | Signed-out screen                                                                                                                        |

`/projects/:id/daily|overall` from the first scaffold redirect to `/reports/…`.

## Not in this app (yet)

Everything in the design is built. What the console is waiting for:

- Work-item links and creation (`/config/work-items/*`), and changing or
  deleting a member (members come from the Directory tab).
- "Sync now" for Jira and Git: no endpoint runs the scheduled sync on demand.
  Data sources shows the next run; the chat directory has its own Sync now.
- A bulk read of write-back consent: the Check-ins tab reads each member,
  cached for five minutes.
- Burn-down by story points needs issues that carry story points; without
  them it counts requirements and says why.
- Sprint counts in the unlink and delete confirmations: no read gives a
  project's or pod's sprints.
- Flow by pod: Signals › Flow reads hand-made work items, so it says "not
  measured" on a tenant fed by Jira issues.
- A name for every id: a developer sees "Someone (id)" for a requester outside
  their own lists, because no read names them.

## Checks

```bash
npm run lint && npm run typecheck && npm run format:check
npm test             # node --test on the pure helpers; needs Node 22.18+
npm run build
```

CI runs the same steps on Node 22. `src/api/client.ts` and `src/api/schema.ts`
are copies of frontend-v2's with three small additions (the past day's `as_of`
in `src/api/asOf.ts`, `apiUrl`, one type export); `src/app/RoleProvider.tsx` is
a trimmed port. Keep identity and request rules in step when either changes.
`make openapi-check` regenerates `src/api/generated.ts`.
