# frontend-v3: the role-based console, rebuilt from the design

A new console on the same backend, API and sign-in as `frontend-v2/`, built
fresh from the "OpenProgram by Role" design: one top navigation on every
screen, a Today that is different for each role, and every setting in Admin.
Where the build stands, what is still open and how it was checked:
[`../frontend-v3.md`](../frontend-v3.md).

## Screens

Each role sees only the tabs, sections and buttons it can use; nothing says which
role would open something else. One access map decides it
(`src/app/access.ts`): the tabs, what Delivery lists, the sections inside the
screens, the ⌘K palette's rows, and where a link to a page the role doesn't have
goes instead. The backend still decides, and a 403 that comes back anyway reads
"Not available to you. The server said: …".

### Tabs per role

| Role          | Tabs                                                               |
| ------------- | ------------------------------------------------------------------ |
| Developer     | Today · Reports · Chat                                             |
| Scrum master  | Today · Signals · Coordination · Reports · Chat                    |
| Product owner | Today · Signals · Coordination · Reports · Chat                    |
| Manager       | Today · Delivery · Signals · Coordination · Reports · Chat         |
| Executive     | Today · Delivery · Signals · Coordination · Reports · Chat         |
| Admin         | Today · Delivery · Signals · Coordination · Reports · Chat · Admin |

Chat shows only where the backend serves the built-in chat. A link to a page the
role doesn't have opens the closest one it has, with `replace`: a scrum master's
`/delivery/pod/:id` opens `/today?pod=:id`, a product owner's
`/delivery/project/:id` opens `/today?project=:id`, a developer's
`/coordination` opens `/today#asks`. Only a link that lands on plain Today
says so, in one passing note.

### What each screen shows

Who: **D** developer, **S** scrum master, **P** product owner, **M** manager,
**E** executive, **A** admin.

| Screen                                | Who                          | What it shows                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Reads                                                                                                                                                   |
| ------------------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Today** (developer)                 | D                            | Your check-in (which day it is from, how it stands, the blockers tied to no task) with Confirm and Correct details (summary and those blockers), and where it rolls up in one line. **Your tasks**, below. Your asks                                                                                                                                                                                                                                                                                                                                                                          | `/me/status` (and `confirm`, `correct`), `/me/focus`, `POST /me/tasks/{id}/update`, `/me/cross-person-requests`, directory                              |
| ↳ **Your tasks**                      | D                            | One list: blocked, past due, an ETA after the due date, in progress, to do; done folded away. Each row: the tracker's status, Due, Your ETA, the task's blockers with their age, "Last said … in chat" or "in OpenProgram". **Update** opens the row: status (To do, In progress, In review, Blocked, Done), ETA with Clear, resolve or add a blocker (Blocked needs one), a note, and "Also move CHK-4 to … in Jira" for the issue's assignee while write-back is on (ticked under auto, unticked under ask). The row then says what the save did and what Jira did. Read-only on a past day | `/me/focus` (`tasks[]`, `write_back`), `POST /me/tasks/{id}/update`                                                                                     |
| **Today** (scrum master)              | S                            | Pod chips (`?pod=`); the pod's dates (DateStrip) with Set date; open blockers first; check-ins; why the pod has its colour, beyond what is listed above; the tasks the pod's people hold, with **Last said** and **ETA**; your check-in, compact; Your asks                                                                                                                                                                                                                                                                                                                                   | `/pods/{id}/checkins`, `/blockers`, `/rollup`, `/tasks`, `/delivery`                                                                                    |
| **Today** (product owner)             | P                            | Project chips (`?project=`); the project's dates (DateStrip) with Change date; progress; what needs attention, with "Show all N tasks"; why the project has its colour; workstreams, when any hold work; your check-in, compact; Your asks                                                                                                                                                                                                                                                                                                                                                    | `/projects/{id}/progress`, `/workstreams`, `/delivery`                                                                                                  |
| **Today** (manager, executive, admin) | M E A                        | The verdict with its reason (a program picker when there are several); **Delivery dates**, one compact strip per project, worst first; portfolio heat with a reason and the due date under each tile, and a "no pod" row; the oldest open risks with All risks; 30-day momentum; the newest executive brief; your check-in, compact; Your asks                                                                                                                                                                                                                                                | `/portfolio/attention`, `/portfolio/heatmap`, `/projects/{id}/delivery`, `/persona/program/{id}/trend`, `/persona/briefs`, directory                    |
| **Delivery**                          | M E A                        | Navigator of programs, projects, workstreams that hold work and pods (no pods for an executive); a panel per kind, each project and pod panel opening with its DateStrip and each program panel listing its projects with the compact one; status, every reason once and by name, related links, progress, check-ins, blockers, tasks                                                                                                                                                                                                                                                         | `/programs/{id}/tree`, `/projects/{id}/progress`, `/workstreams/{id}/progress`, `/pods/{id}/*`, `/projects/{id}/delivery`, `/pods/{id}/delivery`        |
| **Signals**                           | S P M E A                    | **Flow** (the scrum master's and product owner's only view, on their own pod or project first): the flow through review as a band, stage times, the worst jam, where the work goes by type. **Risks** (manager, executive, admin; their first view): one list across projects, worst project first, each risk with its severity, age, the owner's last word and the evidence link; drift joins it with a label                                                                                                                                                                                | `/portfolio/pr-flow`, `/portfolio/risks`, `/projects/{id}/risks`                                                                                        |
| **Coordination**                      | S P M E A                    | The requests board (Open, Acknowledged, Needs resolution; not for an executive), on "Your pods", "Your projects" or "Everything" by role, with Acknowledge and Resolve only on the cards whose people may act; briefs, opening on the role's own kind; Ask the graph. Waiting on you and Raised by you are on every Today, as Your asks                                                                                                                                                                                                                                                       | `/portfolio/cross-person-requests`, `/cross-person-requests/{id}/status`, `/persona/briefs`, `/ask`                                                     |
| **Reports**                           | everyone                     | Every project, worst first (a developer's: the projects of their pods), each card with a compact date strip, **Daily** and **Overall**; set up, change or remove a day report                                                                                                                                                                                                                                                                                                                                                                                                                 | `/day-reports…`, `/projects/{id}/delivery`, `/pods/{id}/delivery`                                                                                       |
| ↳ **Daily**                           | everyone                     | Schedule, audience, Send now (scrum master for their projects, manager, admin), today's note (product owner, manager, admin), the report as it will be sent, past sends with each destination's outcome                                                                                                                                                                                                                                                                                                                                                                                       | `/day-reports/{id}/preview`, `/runs`, `/send`, `/note`                                                                                                  |
| ↳ **Overall**                         | per role                     | The DateStrip (committed date, forecast or "not enough history", the team's date) with its reasons, burn-down, requirements by stage, acceptance gates and questions (keep, dismiss, sign off, add), risks and drift, scope by release. A developer gets gates and questions; a scrum master dates by pod (their own editable), gates, risks and questions; an executive reads everything and edits nothing. An admin gets a link to Admin › Escalation                                                                                                                                       | `/projects/{id}/requirements`, `/gates`, `/risks`, `/releases`, `/release-candidates`; writes `/delivery-date`, `/gate-items/{id}/*`, `/questions/{id}` |
| **Chat**                              | everyone, where it is served | Your check-in thread; an admin gets every thread, can ask someone for a check-in and clear history                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `/test/chat-simulator/*`, `/admin/workflows/checkin/dispatch`                                                                                           |
| **Admin**                             | A                            | Eleven tabs, below: check-ins, where data comes from and goes to, how delivery is counted and escalated, branding, and the hierarchy with its links and people                                                                                                                                                                                                                                                                                                                                                                                                                                | `/config/*`, `/admin/ops/sync-status`                                                                                                                   |

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

A link or a delete ends the link from today and leaves earlier days as they
were.

### Everywhere

| Piece                 | What it does                                                                                                                                                                                                                                                                        | Reads                                                   |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Jump (⌘K, Ctrl+K)     | A palette over the role's tabs and the directory rows its Delivery lists; for a role without Delivery a row opens its own screen (a pod `/today?pod=`, a project `/today?project=` or its Daily). Keyboard only; it keeps the day shown                                             | directory                                               |
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
(`today`, `shell`, `reports`, `admin-structure`, `admin-config`, `flow`,
`redesign`) answer the endpoints above with demo-shaped data, follow
`src/api/generated.ts`, and honour the acting-as role, so each person in the
picker sees their own view and a change reads back until the server restarts.
Kai's tasks hold one of each kind the list sorts, and his updates follow the
backend's rules and show on Ira's pod table; write-back is "ask"
(`MOCK_WRITE_BACK=auto` or `off` to change it). Pick Kai Thompson (developer), Ira
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

| Path                                                                                    | Screen                                                                                                                                                                             |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/today`                                                                                | Today for the viewing role (`?program=` picks a program when there are several, `?pod=` a scrum master's pod, `?project=` a product owner's project, `#asks` scrolls to Your asks) |
| `/delivery`, `/delivery/:kind/:id`                                                      | Delivery explorer (`kind`: program, project, workstream, pod)                                                                                                                      |
| `/signals?view=`                                                                        | Signals (`flow`, and `risks` for a manager, executive or admin; `?scope=all` reads every repository)                                                                               |
| `/coordination?brief=&requests=`                                                        | Coordination (`brief`: `exec`, `weekly_project`, `daily_pod`, `all`; `requests`: the board's scope)                                                                                |
| `/reports`, `/reports/:projectId/daily?report=`, `/reports/:projectId/overall?release=` | Reports; `report` picks one of a project's day reports, `release` scopes Overall to a release                                                                                      |
| `/chat`                                                                                 | Chat                                                                                                                                                                               |
| `/admin?tab=`                                                                           | Admin (`checkins`, `sources`, `integrations`, `stages`, `gates`, `escalation`, `contacts`, `branding`, `entities`, `links`, `directory`)                                           |
| any of the above with `?asOf=YYYY-MM-DD`                                                | The same screen for a past day, read-only                                                                                                                                          |
| `/logged-out`                                                                           | Signed-out screen                                                                                                                                                                  |

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
- A batch read of every project's delivery: the portfolio Today's Delivery
  dates reads each project's own, which is fine for a demo tenant.
- The briefs' "ETA changes" count only the ETAs said in a check-in, not one
  set on a task here.
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
