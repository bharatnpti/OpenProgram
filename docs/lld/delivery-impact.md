# Why the Date Moved LLD

What moved a project's, release's or pod's forecast, taken apart into drivers
that add up exactly, with the work items behind each. Feature summary:
`features.md` §4.34. It builds on the forecast kept per day (`forecast_days`)
and the planning baseline, and reads the `issue_change` facts
(`docs/lld/jira-changes.md`) for who brought an item into scope.

## The decomposition (`core/domain/impact.py`, pure)

Two days of a scope in a row with a snapshot, `a` and `b`. `F(R, W, start,
seed)` is the history forecast of the remaining work `R` over the window's
samples `W`. The keys are partitioned into finished (open at `a`, in production
at `b`), added (open at `b`, not in scope at `a`), removed (open at `a`, gone at
`b`), reopened (in production at `a`, open at `b`) and re-estimated (open at
both, other story points; points mode only). Every replay uses `b`'s seed:

| Step | Replay | Driver |
|---|---|---|
| c0 | the forecast kept for `a` | — |
| c0' | `F(R_a, W_a, a, seed_b)` | rounding (the seed alone) |
| c1 | `F(R_a, W_a, b, seed_b)` | time passed |
| c2 | c1 with `R − finished` | work finished |
| c3 | c2 with `+ added` | scope added |
| c4 | c3 with `− removed` | scope removed |
| c5 | c4 with `+ reopened` | reopened |
| c6 | c5 with `R_b` | re-estimated (and anything item sizes miss) |
| c7 | the forecast kept for `b` | throughput (the window's new day in, its oldest out) |

- Each step is the signed working days between two replays in a row
  (`working_days_moved`, which adds up), for the 50% and the 85% date alike, so
  the steps sum to the kept move exactly.
- Scope added, removed and reopened are shared among their items: equally by
  count, by story points in points mode.
- A day with no forecast on either side, or whose unit changed, is not taken
  apart (`not_taken_apart`); its items are still listed.
- `summed(days)` adds a period's rows. A row that kept no breakdown, or does
  not chain on from the row before, leaves its part as `not_attributed`, so the
  drivers still add up to the period's move. A period that began without a
  forecast reads from its first day with one.
- `scope_group` says what kind of addition an item was, in this order: the
  baseline's change label (tenant data; `CR` is only the default) is a change
  request; else type Bug is a bug; else an item created before it joined is
  moved in; else it is planned (in the baseline, or within its grace working
  days), before planning, or added without the label.

Words (`impact_words.py`): time passed and work finished read together as pace;
parts are listed worst (latest) first; scope added is said by group.

## Storage

`forecast_days.attribution` (JSONB, version 1): `from_day`, `to_day`, `unit`,
both forecasts, the steps, the partition's items (key, title, type, points,
share) and the window's change. Keys, titles, types, numbers and dates only.

- The hourly snapshot run writes it with each row (`record_forecast_days`),
  for every project, pod and release, against the scope's last snapshot within
  7 days.
- The reconstruction writes it on each rebuilt row, and fills it on a row the
  hourly run kept without one (`ForecastDayRepository.fill_attribution`),
  without touching that row's forecast. Pod rows are rebuilt with the pod's
  tasks as the graph held them each day.
- Today is always computed live (`ForecastService.forecast_day`).

## The read (`core/application/impact_service.py`)

`DeliveryImpactService.impact(scope, period, measure, as_of, access)`:

- Periods: since the baseline (the scope's own, else the project's; without
  one, the last 30 days, said in `not_known`), the last 7 or 30 days, since the
  committed date last changed, or from a named day; at most 90 days.
- Drivers: pace, scope added per group, scope removed, reopened, re-estimated,
  throughput, rounding, not attributed; each with working days on the measure,
  both dates' values, and up to 10 items.
- The committed date lane (`delivery_date_changes`: from, to, on, by, note, and
  the additions within 2 working days before it), the team's date lane (the
  latest ETA or due date at both ends, each move with its cause), evidence
  (open requirements past their due date, ETA or baseline due date; undated
  keys), the timeline's markers per day and kind, and the link to the row.
- "Added by" (D14): the author of the change that brought the item in (issue
  type; for a release also its fix version or label), else its creator when it
  was created then, else its reporter. Names only, on the item's own row.

## Who reads it

`DeliveryImpactService.access` decides for the route and for Ora alike:

| Reader | Project or release | Pod |
|---|---|---|
| Product owner, manager, admin | full, with "added by" | full, with "added by" |
| Executive | full, no "added by" | full, no "added by" |
| Scrum master | the projects their pods work on, full | pods they run full; other pods dates only (no items, markers or evidence) |
| Developer | refused | refused |

## Surfaces

- `GET /projects/{id}/delivery/impact?period=&from=&measure=&release_id=&pod_id=&as_of=`.
- The day report's "What changed" gains one line when the 85% date moved since
  the previous snapshot; it leads "Most important" when the verdict worsened or
  the date moved 3 working days later or more.
- Ora's `delivery_impact` (read-only, offered where the route answers), with
  `ASK_TOOL_GUIDANCE`, Investigate's `STEP_GUIDANCE` and `PLAN_MORE_REMINDER`
  lines, and an unread-negative rule for "no scope was added".
