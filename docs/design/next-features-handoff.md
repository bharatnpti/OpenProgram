# Next features: build handoff (2026-10-12)

The design is `docs/design/next-features.md`. Build order is its §7: at most three lanes at a time.
Every branch below is based on `fix/v2-round` (`c5edf725`, the open PR #24). None is merged.

## Rules for every lane

- Lore commits (`.agents/skills/lore-protocol/SKILL.md`). Author `35888424+bharatnpti@users.noreply.github.com`,
  name `bharatnpti` (set repo-locally). **No AI attribution**: no Co-Authored-By trailer, no
  "Generated with" line, in commits, PRs or issues.
- The repo is public: made-up demo data only; no company names, real ids, emails or hosts.
- Never push to `main`, never merge a PR, never force-push. Push lane branches only.
- Fakes and mocks only: never call a real Jira, Slack or GitLab.
- Checks: `TZ=UTC PYTHONPATH=backend OPENPROGRAM_DATABASE_URL=postgresql://x:x@127.0.0.1:1/x
  OPENPROGRAM_REDIS_URL=redis://127.0.0.1:1/0 uv run pytest backend/tests -q -m "not integration and not ui_bdd"`,
  ruff (check and format), mypy, lint-imports, `make openapi-check` (never hand-edit `generated.ts`),
  and for frontend-v3: lint, typecheck, prettier, `npm test`, build.
- Migration numbers go by merge order; rebase `down_revision` when another lands first.
- Live checks on a database copy are done later on the owner's machine; list them as "needs the local DB".

## Lanes

| Lane | Branch | Tip | State |
|---|---|---|---|
| A. Jira read v2 | `feat/jira-read-v2` | `9e0cab0a` | built and checked; two fixes left (below) |
| B. Pod profiles | `feat/pod-profiles` | `29a5a040` | built and checked (migration `0041`); small items left (below) |
| C. Baseline and forecast days | `feat/baselines-forecast-days` | `b28abc18` | built and checked (migration `0042`, `down_revision` 0040), one `wip:` commit; a fix left (below) |
| D. Jira-observed day | not started | — | start from `feat/jira-read-v2` (design §1.8 MVP) |
| E, F, J, G, H, I | not started | — | design §7 |

### A: left
1. **A failing issue must not hold its target.** After 3 failed history reads of one issue, skip it
   (keep `{key: count}` failures and skipped keys in the issue cursor metadata), retry a skipped key
   at most once a day, log one warning per skip naming the key only. Test: 3 failing ticks, then
   skipped, `through` advances, no retry within 24 h, retried after.
2. **No duplicate history reads across overlapping targets in one tick** (a project and its pods
   run in parallel on the sync queue, concurrency 4). Pick the simplest correct approach; an atomic
   per-issue claim on the sync cursor repository works (`swap_cursor`, Postgres + in-memory +
   contract fake). Each target's budget counts only its own reads. Duplicate reads only waste calls
   (facts dedupe), so keep it small. Test: two targets, same issues, each history read once.

### B: left
1. Write its `features.md` section and a short generic LLD.
2. Optional: an Admin › Profiles tab (rename, hide, add) using `apiClient.saveProfile`, with a node test.
3. Click through the mock (`npm run mock` in frontend-v3): Links add, pencil, Accept all, the pod panel
   as a scrum master.
4. Needs the local DB: migration up/down/up on a seed copy, and the live Playwright run.

### C: left
1. **"Set by" shows "a team member" instead of the product owner's name.** In
   `baseline_service.baselines`, resolve names the way `ForecastService._context` does (developer
   nodes by id). Add a unit test where the actor is a member id with a name.
2. Click through the mock (`npm run mock`): the baseline section as product owner (New baseline on
   the project, Mark planning complete on a release) and as executive (no button).
3. Reword `b28abc18` without the `wip:` prefix once 1 passes (a new branch tip is fine; never
   force-push a pushed branch: add a follow-up commit, and the squash happens at integration).
4. Needs the local DB: the 30-day reconstruction compared with the old history read, the Postgres
   integration test, migration up/down/up, and the live role check.

Unit checks for C: `backend/tests/unit/test_forecast_days.py`, `test_baselines.py`, `test_forecast.py`.

## Order of work

1. A's two fixes, B's and C's small items (each on its own branch).
2. Lane D (design §1.8 MVP) on a new branch `feat/jira-observed-day` from `feat/jira-read-v2`
   after A's fixes. Lane B runs in parallel in the design; D reads B's `code_work` switch only after
   integration, so leave drift's code-work test as is.
3. Lanes E (why the date moved, §3.10) from C, and F (Jira hygiene, §4.12) from A + C, as wave 2.
   E and F both add to `day_report_builder.py`: land E's lines first.
4. Lane J (Timeline, §5.8) after E.

## Decisions already taken (defaults the owner may change)

- A day built from Jira stays amber (inferred); silence is never green.
- Fill the day for every member the fan-out would ask who has no status by their cutoff.
- Profiles: admins set them everywhere, scrum masters in the pods they run; set when someone is
  added to a pod.
- Baseline per release (fallback: project), marked by the product owner or a manager.
- The change label is tenant data (default "CR"). Executives don't see who added scope.
- Hygiene rules list only by default; an admin switches a rule to ask.
- Timeline replaces "Dates by pod", "How the date moved" and the Delivery panel's date history.
- No ranking of people anywhere; findings attach to work items; no Jira free text.
