# frontend-v3: status and handoff

The single place to pick this work up in a new session: what is done, what is
left, how to resume, and what was decided along the way.

_Last updated: Wed 7 Oct 2026._

## Snapshot

|                   |                                                                                                                                                                                                                                                                                                      |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Branch            | `feat/frontend-v3` in the main checkout                                                                                                                                                                                                                                                              |
| Last commit       | `c42255c` "fix(frontend-v3): a check-in's words follow the day shown, and a task's source is 'reported', not 'confirmed'", the last code commit (docs and screenshots follow it). 41 code commits are ahead of `origin/feat/frontend-v3` (`908151d`): **not pushed**, and the push is a fast-forward |
| Uncommitted       | Nothing                                                                                                                                                                                                                                                                                              |
| Build health      | frontend-v3: lint, typecheck, prettier, **335/335 unit tests**, production build: all pass. Backend: `pytest` **2010 passed, 27 skipped**; ruff, mypy, import-linter and `make openapi-check` clean. The backend has not changed since its last merge (`b0f877c`)                                    |
| Rendered          | Every screen, per role, on the **mock API** and on a **seeded demo backend**, and at 390 px with no sideways scroll                                                                                                                                                                                  |
| Real integrations | One live round on **a QA tenant on real Slack, Jira and GitLab, with test personas**: monitor A **7/7 PASS**, monitor B **10/14 PASS** (its 3 FAILs are fixed)                                                                                                                                       |
| Size              | About 23,700 lines of TypeScript in `frontend-v3/src` (excluding the generated client), 5,400 lines of unit tests in 38 files, 5,100 lines of mock API in `scripts/`                                                                                                                                 |

**In one line:** every screen in the design is built, and works on the mock, on a
seeded demo backend and against real integrations. What is left is to push and
open the PR, a list of backend items the live round found, and a few endpoints
the console is waiting for.

## Resume in a new session

```bash
# from the repository root
git status                          # clean, on feat/frontend-v3
cd frontend-v3 && npm install       # Node 22.18 or newer: the tests run TypeScript as written
npm run mock                        # http://127.0.0.1:5175, demo-shaped data, no backend needed
```

To run against a seeded backend instead, bring the stack up as in
`docs/ops/local-demo.md` and seed it (`docker compose exec -w /app backend python -m scripts.seed_demo_history --reset`),
then run `npm run dev` in `frontend-v3/`. The mock and the dev server share port
5175: run one at a time.

Read first: this file, then `frontend-v3/README.md` (screens, routes, endpoints,
checks). Pick people in the header to switch role: Kai Thompson (developer), Ira
Novak (scrum master), Mina Patel (product owner), Asha Rao (manager; switch the
lens to Admin), Elena Fischer (executive). ⌘K jumps anywhere; the day field in
the header looks back at a past day.

## Status by screen

Legend: **Done** = built, and checked on the mock and on a seeded demo backend;
**Partial** = built with a named gap; **Open** = waits for the backend or for a
product decision. "Remaining work" is the numbered list further down.

### Navigation and shell

| Item                                                                                                                 | Status | Notes                                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Top navigation on every screen; a tab the role isn't offered is struck through; Chat only when the backend serves it | Done   | Scrolls sideways on phones; `auth/status.chat_enabled`                                                                                                                      |
| Sign-in, signed-out and "can't reach the backend" screens (the address tried, Try again)                             | Done   |                                                                                                                                                                             |
| Acting-as and lens pickers (people grouped by role), avatar menu with Sign out under OIDC                            | Done   | Same `localStorage` keys as frontend-v2; on a phone they sit in the menu                                                                                                    |
| Viewing a past day (`?asOf=`): day field, banner, read-only on every screen but Admin                                | Done   | `as_of` is added in the API client to the 27 reads that take it, and the client refuses a change sent anyway. Admin shows the current configuration, and its writes stay on |
| ⌘K / Ctrl+K palette: screens, programs, projects, workstreams, pods, people                                          | Done   | Keyboard only; keeps the day shown                                                                                                                                          |
| Tenant logo in the header                                                                                            | Done   | `GET /config/branding`; uploaded in Admin › Branding                                                                                                                        |
| A member's own check-in schedule (avatar menu)                                                                       | Done   | `GET/PUT /me/checkin-preference`: days and time zone, own or the team's                                                                                                     |
| Old `/projects/:id/...` links redirect to `/reports/...`                                                             | Done   |                                                                                                                                                                             |

### Today

| Role                      | Item                                                                                                                                    | Status  | Notes                                                                                                                                                                                     |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Developer                 | Check-in card: which day the status is from, how it stands, blockers with age                                                           | Done    | One vocabulary on every screen: replied, confirmed, partly replied, replied without a status, no reply, inferred, carried forward. "Confirmed" is only what the person did in the console |
| Developer                 | Confirm; Correct details (summary, ETA change, resolve or add blockers)                                                                 | Done    | Sends only what changed, each restated blocker by id. Confirm on a status that records nothing makes the person green: a backend question (Remaining work 3)                              |
| Developer                 | Focus today, Your tasks, Waiting on you, where you roll up                                                                              | Partial | Waiting on you lists requests, not questions tracked on Jira issues (Remaining work 5)                                                                                                    |
| Developer                 | A person with no member record                                                                                                          | Done    | Says so and points to Directory, instead of a red failure                                                                                                                                 |
| Scrum master              | Pod chips (the pods they run, by the backend's rule), check-ins, open blockers oldest first, why the pod has its colour, Waiting on you | Done    |                                                                                                                                                                                           |
| Product owner             | Project chips (worst first), progress ring and counts, workstreams, what needs attention, Waiting on you                                | Done    | A tie opens on the project of the person's first pod: their role in each pod is not in the directory                                                                                      |
| Manager, executive, admin | Verdict (the program's own colour and reason first when worse), program picker, 30-day momentum, newest executive brief                 | Done    | Older executive briefs show as one paragraph, not a verdict and bullets                                                                                                                   |
| Manager, executive, admin | Portfolio heat with a reason under each tile, the "no pod" row, oldest open risks                                                       | Done    | The no-pod tile cannot say "replied without a status" until the backend does (Remaining work 3)                                                                                           |

### Delivery

| Item                                                                                                                                    | Status | Notes                                                                           |
| --------------------------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------- |
| Navigator of programs, projects, workstreams, pods, worst first; selection in the URL                                                   | Done   | `/delivery/:kind/:id`                                                           |
| Program, project, workstream and pod panels: status, every reason once and by name, related links, progress, check-ins, blockers, tasks | Done   | A workstream that holds no work says so; workstreams are optional               |
| Pod delivery date: pod against project date, the reason typed, how it moved                                                             | Done   | `GET /pods/{id}/delivery`; set by the pod's scrum master, a manager or an admin |
| Locked panels say which role opens them                                                                                                 | Done   |                                                                                 |

### Signals

| Item                                                                                                      | Status  | Notes                                                                                                                                            |
| --------------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Counts, filters kept in the URL, risk and drift cards ("owner says" beside "signals say"), 7-day activity | Done    | One risk per merge request; the feed is current whatever the day                                                                                 |
| Flow                                                                                                      | Partial | Flow reads hand-made work items, so on a tenant fed by Jira issues it says "not measured". A flow by pod needs a backend read (Remaining work 4) |

### Coordination

| Item                                          | Status | Notes                                                                                                     |
| --------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------- |
| Requests board, Waiting on you, Raised by you | Done   | Only the person asked acknowledges; they or the requester resolve; anyone else gets a 403, admin included |
| Briefs with a kind filter, Ask the graph      | Done   | Ask has no project scope                                                                                  |

### Reports

| Item                                                                                                                                                        | Status  | Notes                                                                                                                                                                                                                                |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Reports home; set up, change and remove a day report                                                                                                        | Done    | A new report starts in the team's time zone; destinations that are not connected say why                                                                                                                                             |
| Daily: schedule, audience, Send now, today's note, the report as sent, past sends with each destination's outcome                                           | Done    | Each lock names who may act. On a past day the report is still built live, and says so                                                                                                                                               |
| Overall: verdict and its cause ("At risk because 3 open requirements have no ETA or due date"), committed date and how it moved, each pod's date and reason | Done    | The console words the cause itself until the backend returns it (Remaining work 3)                                                                                                                                                   |
| Overall: burn-down                                                                                                                                          | Partial | By story points when every day kept points (the timeline carries `points` and `has_points`), else by requirement count with the reason. **It needs Jira issues that carry story points**; a tenant without them gets the count chart |
| Overall: requirements by stage, 30-day chart, moves, table                                                                                                  | Done    | "In stage since" is the first snapshot day at best, so the first day reads "or earlier"                                                                                                                                              |
| Set the project's, a release's or a pod's delivery date, with a reason                                                                                      | Done    | Product owner, manager, admin; a pod's by its scrum master, a manager or an admin                                                                                                                                                    |
| Release scope (`?release=`); create a release from a Jira fix version or label; remove one                                                                  | Done    | Creation ran on a backend copy seeded with fix versions; the QA tenant had none to offer. Removing a release does not stop day reports that cover it, and the confirmation says so                                                   |
| Acceptance gates: keep or dismiss suggestions, sign off met, failed or waived, add an item, Read Jira now                                                   | Done    | The rule for each kind is said first; a 403 shows the server's reason                                                                                                                                                                |
| Questions: keep or dismiss, status, add                                                                                                                     | Done    | A status nobody chose reads "Not answered yet"; a Jira reply from the person asked is shown as read from Jira                                                                                                                        |
| Risks and drift; escalation matrix                                                                                                                          | Done    | The matrix is for admins                                                                                                                                                                                                             |

### Chat

| Item                                                                                 | Status  | Notes                                                                                |
| ------------------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------ |
| Own thread (bot questions, follow-ups, nudges, replies), composer, reply in a thread | Done    | `/test/chat-simulator/*`; purpose labels are the backend's own (`status_checkin`, …) |
| Admin: roster, anyone's thread, Ask for a check-in, Clear history                    | Done    | Ask sends tenant and member only: a chat id would override where the message goes    |
| Chat on a backend that serves it                                                     | Partial | Only with the built-in chat provider; checked on the mock, not on a backend          |

### Admin

| Tab                                                                                                                            | Status  | Notes                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Check-ins: days, time zone, reply waits marked default or own, "Use team default" per field, write-back consent, tenant switch | Done    | A field nobody touched is never sent                                                                                                                                    |
| Check-ins: the consent column                                                                                                  | Partial | One read per member, cached five minutes: there is no list endpoint (Remaining work 4)                                                                                  |
| Data sources: health, schedule in words, last and next run, errors, targets; Sync now for the chat directory                   | Done    | `/admin/ops/sync-status`                                                                                                                                                |
| Data sources: Sync now for Jira and Git                                                                                        | Open    | No endpoint runs the scheduled sync on demand; the existing routes sync one project key or repository and would duplicate a project (Remaining work 4)                  |
| Integrations: Slack, email, Teams, Jira, GitLab, GitHub, calendar: set up, change, test, remove                                | Done    | Secrets are write-only; Test is off while a changed address would reuse a stored secret                                                                                 |
| Delivery stages, Gates, Escalation, Escalation contacts, Branding                                                              | Done    | The server's refusals are said in words; a logo is judged by its bytes                                                                                                  |
| Entities: add, change, delete programs, projects, pods, workstreams                                                            | Done    | The confirmation says what leaves and what stays, but cannot count sprints: no read exposes them (Remaining work 4). A pod may list only repositories its projects list |
| Links: projects in a program, pods on a project, people in a pod, workstreams, task assignment                                 | Done    | Link dates are not editable; the lists have no paging                                                                                                                   |
| Links: work items                                                                                                              | Open    | Work-item links and creation (`/config/work-items/*`) are not in v3                                                                                                     |
| Directory: sync, import people, chat, Jira and Git accounts, fill in                                                           | Done    | One person at a time, nobody pre-ticked, no select-all                                                                                                                  |

## Known risks and limits

- The backend decides. The console's role flags only say up front who opens a
  panel; every panel handles a 403 and shows the server's reason.
- OIDC sign-in has not been run (dev auth only). With no acting-as person the
  console takes the signed-in subject as the member.
- "Today" is the server's day, learned from the `as_of` its dated reads echo;
  until the first answer it is the later of the UTC day and the browser's. A
  backend on a non-UTC clock can disagree with the console around midnight.
- An id stays an id when nothing names it ("Someone (id)"); a developer's names
  are only what the directory gives.
- With several programs, the backend's portfolio verdict and risk list read the
  whole tenant, not one program; the screen says so beside the list.
- The mock follows `generated.ts`, so shapes match, but a real tenant's values
  can differ. Real data taught most of the wording rules (see the log below).

## Verification log

| Check                                                                                  | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `npm run lint`, `npm run typecheck`, `prettier --check .`, `npm test`, `npm run build` | All pass at `c42255c`: **335 / 335** tests in 38 files. CI runs the same steps on Node 22; the branch is not pushed, so CI has not run on it                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Backend: `pytest`, ruff, mypy, import-linter, `make openapi-check`                     | 2010 passed, 27 skipped (93% coverage); the rest clean                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Mock, per role                                                                         | Every screen as developer, scrum master, product owner, manager, executive and admin, and at 390 px                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Per-role smoke on a seeded demo backend                                                | By each build lane on its own copy, then once on the merged build: every screen once per role, 0 failed requests, 0 console errors. Every write done once through the UI and read back: check-in confirm and correct, request acknowledge and resolve, project and pod dates, releases, gate items and sign-off, questions, day-report set-up and Send now, the own schedule, Admin settings and structure                                                                                                                                                                                                            |
| Two code reviews                                                                       | Reports with the shell, and Today with the Admin lanes: 0 blockers, 4 major, 17 minor, all fixed except sprint counts (no read exposes sprints). The majors: a correction could resolve the wrong blocker, a connection test sent a stored secret to an unsaved address, Reports writes stayed on for a past day, dialogs lost focus                                                                                                                                                                                                                                                                                  |
| Read-only pass on real data                                                            | Every screen and role on the QA tenant, before the round: 0 blockers, 4 major, 20 minor, 6 cosmetic. The console, and a few small backend rules, were fixed (one risk per merge request, a risk's days at today's age, an unmatched ask stays on its requester's list, no raw JSON in Ask answers)                                                                                                                                                                                                                                                                                                                    |
| **One live round on the QA tenant**                                                    | Real Slack, Jira and GitLab, 11 test personas. The bot's check-in went to each, who answered in chat, did real merge-request and issue work, then used the console as themselves; two read-only monitors compared the systems with what was expected. **Monitor A: 7/7 PASS** (check-in DMs, replies processed, a request acknowledged and resolved, a silent person nudged, escalated and closed never green, a day report to its two people only, console writes stored, health). **Monitor B: 10/14 PASS, 3 FAIL, 1 UNEXPECTED.** Jira (34 issues) and GitLab (14 merge requests) matched the console on every row |
| After the round                                                                        | The 3 FAILs, all about why a verdict has its colour or reason, are fixed in the console (six commits). The UNEXPECTED (a reply with no status still reads as unanswered) and the backend side of one FAIL are backend items (Remaining work 3). Re-checked on a demo copy (dates with reasons, a day report set up from another time zone, a correction by blocker id, a connection test with a cleared secret, unlink and delete keeping past days) and read-only on the QA tenant: no console errors, no failed requests                                                                                            |

## Remaining work, in order

1. **Push `feat/frontend-v3` and open the PR.** The push is a fast-forward from
   `origin/feat/frontend-v3`. Before it, check that every author is the noreply
   address and no message carries an AI attribution line.
2. **Rebuild any long-running environment (QA, demo) from this branch's
   backend.** One started earlier still has the old request, link and
   correction rules and no per-day story points.
3. **Backend items from the live round**, most important first; none is in this
   branch:
   - The portfolio headline ignores the program's own colour, and the rule that
     turns a node red when two different blockers are open has no reason of its
     own. The console words both itself.
   - A reply with no status counts as unanswered wherever the day is read.
   - A pod board cannot tell a chat reply from a confirmation, and the backend
     says "confirmed" where it means "replied".
   - The day report escalates a manager's ask down to a scrum master, and takes
     the team from the person's first pod instead of the work item's pod.
   - Product call: Confirm on a status that records nothing makes the person
     green. Silence should never be green.
   - "At risk" cannot name the requirements that have no date, and under ten
     days of history one undated requirement is enough, however far off the
     date is (a product call). A pod with nothing in scope reads "Done".
   - Smaller: an ask about another project shows in a project's day report; a
     merge-request risk names neither the merge request nor the issue; a
     person's reasons contradict themselves ("confirmed, no blockers" beside
     "open blocker"); a Jira comment by the person asked counts as the answer;
     replies, confirms and corrections do not refresh the rollup, so a confirmed
     person reads "inferred" for up to an hour; the day report reads the
     project's date, not a pod's; the close-out summary prints a raw chat id;
     extraction stores "waiting on review" as a blocker.
4. **Endpoints and reads the console is waiting for:** start the scheduled Jira
   and Git sync from Admin; a list of write-back consent; a project's or pod's
   sprints (for the unlink and delete confirmations); flow by pod; a requester's
   name on a request; work-item links and creation.
5. **Console follow-ups:** Waiting on you should list tracked questions asked of
   the person (`asked_to` on `/projects/{id}/gates`); open a product owner's tie
   on their role in each pod; split older executive briefs into a verdict and
   bullets.
6. **Not yet run live:** OIDC sign-in; the built-in Chat on a backend that serves
   it; release creation on the QA tenant (no fix versions there); Test
   connection against a real host; gate template edits; a directory import with
   people left to import.
7. **Config:** the day report's footer links to `OPENPROGRAM_CONSOLE_URL`. Point
   it at this console once it replaces the older one.
8. **Decide where this file lives.** The repo keeps working notes out (decisions
   go in commit trailers), so it could move to `frontend-v3/docs/` or fold into
   the README before the PR merges.
9. Optional: an end-to-end smoke test (Playwright) per role in CI; make the demo
   seed link pods with `contains`, so a pod shows its scope instead of "Nothing
   in scope".

## Repo changes

Since `908151d`, the last pushed commit (41 code commits):

- `frontend-v3/`: the console rebuilt around the roles and finished, with its
  mocks, tests and screenshots (about 31,000 lines added).
- Backend, small and tested, one commit each: per-day story points on the
  requirements timeline (no migration); one risk per open pull request, with
  days worded at today's age; an unmatched ask stays on the requester's list and
  Ask answers no longer leak raw JSON; only the people on a request change it,
  and the actor is recorded; a connection test uses a stored secret only where
  it was saved; unlinking or deleting ends the link instead of erasing it, so
  past days keep it; a correction's blockers match by id; the own-status 404
  tells "no member record" from "no status yet".
- The three API clients regenerated (`frontend/`, `frontend-v2/`,
  `frontend-v3/`), with the older consoles' request board and connection form
  following the new rules; `features.md` and `docs/lld/graph-of-truth.md` record
  the backend rules.
- This file, `frontend-v3/README.md` and 29 screenshots.

From the first pass, already pushed: CORS port 5175 in `backend/config/settings.py`,
`.env.example` and the `docker-compose.yml` fallback, Makefile `frontend-v3-*`
targets in `verify` and `openapi-check`, a CI job on Node 22.

## Code map (`frontend-v3/src/`)

| Path                                                                          | Purpose                                                                                                                                                                                                                                                                                                                            |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `App.tsx`                                                                     | Routes, auth gate (sign-in, backend unreachable), lazy pages                                                                                                                                                                                                                                                                       |
| `app/Layout.tsx`, `app/nav.ts`                                                | Header (logo, jump button, day field, acting-as, avatar), the seven destinations with role gating                                                                                                                                                                                                                                  |
| `app/role.ts`, `app/RoleProvider.tsx`, `app/roleWords.ts`, `app/authWords.ts` | Identity and capability flags (`canReadProjectProgress`, `canReadAggregate`, `canReadPodDetail`, `canReadPortfolio`, `canSetUpDayReports`, `canManageConfig`, `chatEnabled`); role and sign-in wording                                                                                                                             |
| `app/ViewingDateProvider.tsx`, `app/viewingDate.ts`, `lib/viewingDate.ts`     | The past day: `useViewingDate()`, `useReadOnly()`, the banner's words                                                                                                                                                                                                                                                              |
| `app/queryCache.ts`, `app/titles.ts`                                          | Every query key carries the day (`sameOnEveryDay` opts one out); the browser tab's title                                                                                                                                                                                                                                           |
| `app/directory.ts`, `app/scope.ts`, `app/names.ts`                            | Directory queries and `useProgramChoice()`; the pods, projects and programs of a person; `useNames()` per role                                                                                                                                                                                                                     |
| `api/client.ts`, `api/schema.ts`, `api/generated.ts`, `api/openapi.json`      | Every endpoint, typed (client and schema copied from frontend-v2)                                                                                                                                                                                                                                                                  |
| `api/asOf.ts`                                                                 | Adds `as_of` to the 27 reads that take it; learns the server's day                                                                                                                                                                                                                                                                 |
| `components/shell/`                                                           | `AccountMenu`, `ActingAs`, `CommandPalette` with `paletteRows`, `HeaderLogo`, `TabTitle`, `ViewingDate`                                                                                                                                                                                                                            |
| `components/ui/`, `PanelState.tsx`, `Dialogs.tsx`                             | Rings, chips, cards, pills; loading, locked, 403, error and empty states; confirm and text dialogs                                                                                                                                                                                                                                 |
| `pages/`                                                                      | One file per destination; `TodayPage` switches by role                                                                                                                                                                                                                                                                             |
| `features/today/`                                                             | `DeveloperToday`, `ScrumMasterToday`, `ProductOwnerToday`, `PortfolioToday`, `WaitingOnYou`; `checkin.ts` and `heat.ts` (pure)                                                                                                                                                                                                     |
| `features/delivery/`                                                          | `ProgramPanel`, `ProjectPanel`, `WorkstreamPanel`, `PodPanel`, `PodDeliveryCard`, `DateHistory`, `factors.ts`                                                                                                                                                                                                                      |
| `features/reports/`, `features/daily/`, `features/overall/`                   | Reports header, set-up and date dialogs, `access.ts` (who may change what); Daily header, preview and past sends; Overall's forecast, burn-down, requirements, gates with `IssueGatesDialog`, risks, questions, release scope                                                                                                      |
| `features/signals/`, `coordination/`, `chat/`, `checkin/`                     | Signals wording; the raised-request rule; chat purposes; the member's own schedule dialog                                                                                                                                                                                                                                          |
| `features/admin/`                                                             | One tab per file (`CheckinsTab`, `DataSourcesTab`, `IntegrationsTab`, `DeliveryStagesTab`, `GatesTab`, `EscalationTab`, `ContactsTab`, `BrandingTab`, `EntitiesTab`, `LinksTab`, `DirectoryTab`), their dialogs, and the pure form and wording modules beside them (`*Form.ts`, `structure.ts`, `adminWords.ts`, `adminErrors.ts`) |
| `lib/`                                                                        | `format.ts` (days and times), `status.ts`, `words.ts`, `checkinWords.ts`, `errors.ts`, `zones.ts`, `utils.ts`                                                                                                                                                                                                                      |
| `../scripts/`                                                                 | `mock-api.mjs`, `mock-console.mjs` and `mock/*.mjs` (`npm run mock`); `screenshots.py` with `screenshots.json`                                                                                                                                                                                                                     |

Tests sit beside what they test (`*.test.ts`) and cover the pure modules, which
import only types so `node --test` can run them.

## Screenshots

Mock data, not a real backend. Times are UTC and the day is the mock's, Tue 6 Oct 2026. They are regenerated, never edited: `npm run mock` in `frontend-v3/`, then
`uv run python frontend-v3/scripts/screenshots.py` from the repository root (see
the README).

|                                                                                                                             |                                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| ![Today, developer](frontend-v3/docs/screenshots/01-today-developer.png) **Today, developer**                               | ![Today, scrum master](frontend-v3/docs/screenshots/02-today-scrum-master.png) **Today, scrum master**                 |
| ![Today, product owner](frontend-v3/docs/screenshots/03-today-product-owner.png) **Today, product owner**                   | ![Today, manager](frontend-v3/docs/screenshots/04-today-manager.png) **Today, manager**                                |
| ![Delivery, pod](frontend-v3/docs/screenshots/05-delivery-pod.png) **Delivery, pod**                                        | ![Delivery, program](frontend-v3/docs/screenshots/06-delivery-program-executive.png) **Delivery, program (executive)** |
| ![Signals](frontend-v3/docs/screenshots/07-signals.png) **Signals**                                                         | ![Coordination](frontend-v3/docs/screenshots/08-coordination.png) **Coordination**                                     |
| ![Reports](frontend-v3/docs/screenshots/09-reports.png) **Reports**                                                         | ![Daily](frontend-v3/docs/screenshots/10-daily-manager.png) **Daily**                                                  |
| ![Overall, admin](frontend-v3/docs/screenshots/11-overall-admin.png) **Overall (admin)**                                    | ![Overall, developer](frontend-v3/docs/screenshots/15-overall-developer.png) **Overall (developer, locked panels)**    |
| ![Chat](frontend-v3/docs/screenshots/12-chat-developer.png) **Chat**                                                        | ![Today on a phone](frontend-v3/docs/screenshots/16-today-mobile.png) **Today at 390 px**                              |
| ![The palette](frontend-v3/docs/screenshots/17-command-palette.png) **⌘K palette**                                          | ![A past day](frontend-v3/docs/screenshots/18-past-day.png) **A past day, with its banner**                            |
| ![Overall, delivery date](frontend-v3/docs/screenshots/19-overall-delivery-date.png) **Overall, the delivery-date dialog**  | ![Overall, gate dialog](frontend-v3/docs/screenshots/20-overall-gate-dialog.png) **Overall, the gate dialog**          |
| ![Admin, check-ins](frontend-v3/docs/screenshots/13-admin-checkins.png) **Admin, check-ins**                                | ![Admin, data sources](frontend-v3/docs/screenshots/14-admin-data-sources.png) **Admin, data sources**                 |
| ![Admin, integrations](frontend-v3/docs/screenshots/21-admin-integrations.png) **Admin, integrations**                      | ![Admin, delivery stages](frontend-v3/docs/screenshots/22-admin-delivery-stages.png) **Admin, delivery stages**        |
| ![Admin, gates](frontend-v3/docs/screenshots/23-admin-gates.png) **Admin, gates**                                           | ![Admin, escalation](frontend-v3/docs/screenshots/24-admin-escalation.png) **Admin, escalation**                       |
| ![Admin, escalation contacts](frontend-v3/docs/screenshots/29-admin-escalation-contacts.png) **Admin, escalation contacts** | ![Admin, branding](frontend-v3/docs/screenshots/25-admin-branding.png) **Admin, branding**                             |
| ![Admin, entities](frontend-v3/docs/screenshots/28-admin-entities.png) **Admin, entities**                                  | ![Admin, links](frontend-v3/docs/screenshots/26-admin-links.png) **Admin, links**                                      |
| ![Admin, directory](frontend-v3/docs/screenshots/27-admin-directory.png) **Admin, directory**                               |                                                                                                                        |

## How we got here

1. **Design page** (<https://claude.ai/artifact/5vRcnsnsPHE6oRunvXYaJY>, private,
   "OpenProgram by Role"): the product shown through its six roles, with a
   console mock per role and an access matrix. This build follows it.
2. **Reports asked for:** an end-of-day report for action, a forecast with
   burn-down and completion, risks with who resolves them, dependencies and
   escalation, and an AIDLC requirements view with business acceptance and test
   cases. Then restructured: AIDLC folded into **Overall**, two views **Daily** and
   **Overall**, gates explained in plain words; the sample daily report set the
   shape of Daily.
3. **Separate UI** on the same backend: the first pass (Reports only) is
   `908151d`, the last pushed commit.
4. **A first run against the backend** showed no navigation and none of the role
   screens. The decision was to **build fresh from the design**, and to build
   everything in the design.
5. **The build-out:** five lanes in parallel, each in its own worktree and on its
   own backend copy, each smoke-tested on that backend before it built: Today
   with Signals and Coordination; the shell (past day, palette, logo, own
   schedule) with Delivery and Chat; Reports (dates, releases, gates,
   questions, story points); and two Admin lanes, settings and structure. They
   were merged, reviewed twice and fixed, then read on real data, tested live
   and fixed again.
6. **Decisions worth keeping:**
   - Admin shows the current configuration, and its writes stay on, while a past
     day is shown.
   - "Today" is the server's day, not the browser's.
   - People are imported one at a time, never all.
   - "Sync now" is offered only where an endpoint runs the same path as the
     schedule (the chat directory).
   - Several programs get a picker, not a merged verdict.
   - Backend rules found on the way (requests, connection tests, link history,
     correction ids) were fixed in separate small branches and merged, not
     folded into the console commits.

## Notes for the agent in the next session

- Work in the main checkout on `feat/frontend-v3`. A worktree made from `main`
  has no `frontend-v3/` until the PR merges; make it from the branch tip.
- Node 22.18 or newer runs `npm test`. Never `npm install` from another OS into
  the same `node_modules`.
- The mock lives in `scripts/mock-api.mjs` (day reports, risks),
  `scripts/mock-console.mjs` (base data) and `scripts/mock/*.mjs` (stateful lane
  mocks). Extend them when a screen gains an endpoint, and keep them following
  `src/api/generated.ts`. In `mock-api.mjs` the order matters: admin-config
  answers first.
- Screenshots come from the mock only, never from a tenant with real people or
  workspaces: the folder is public. `uv run playwright install chromium` once.
- The repository is public: no real chat, Jira or Git identifiers, emails,
  workspace names or hosts, and no local paths beyond `~/`, in docs, screenshots
  or commit messages. Describe a real test as "a QA tenant with test personas".
- A database restored from a `pg_dump` of the graph can answer 500 on every
  link, unlink and delete ("graph with oid … does not exist"): the restored
  schema gets a new oid while `ag_catalog.ag_graph.graphid` keeps the old one.
  Point `ag_graph.graphid` and `ag_label.graph` at the schema's oid after a
  restore.
- Decisions go in commit trailers (see `AGENTS.md`); run `lore constraints` on a
  path before you change it.
