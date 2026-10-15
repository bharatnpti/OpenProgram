# Timeline LLD

A Gantt per project and pod: each scope from its planning baseline to its
committed date, with progress, the forecast band, the team's date, the
committed date's history, and markers on the day each thing that moved the
date happened. Feature summary: `features.md` §4.37. It reads what the planning
baselines and the forecast kept per day (`docs/lld/baselines-forecast-days.md`),
why the date moved (`docs/lld/delivery-impact.md`) and the Jira changes
(`docs/lld/jira-changes.md`) already store. No migration.

## Reads

| Route                                            | Rows                                                                                          | Gate                                                                              |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `GET /projects/{id}/timeline?release_id=&as_of=` | the project's row (or the release's), its releases, then its pods worst verdict first         | the project's readers; whoever sets a pod's date (a scrum master: the pods only)  |
| `GET /pods/{id}/timeline?as_of=`                 | the pod's row for each project it works on, with that project's committed date as a reference | the pod's dates' gate (`GET /pods/{id}/delivery`), a developer's own pod included |

A past day (`as_of`) reads stored rows only and runs no forecast: the kept
`forecast_days` row of the day (else the last one kept before it),
`planning_baselines`, `requirement_snapshots`, `delivery_date_changes`,
`issue_change` and issue facts, `developer_blockers`, and open
`hygiene_findings`, with the markers up to that day.

Today is live, as every read of today is: one delivery forecast of the project
per read (`ForecastService.live_days`, the same scope, seed and inputs as
`forecast_day`, without the breakdown) gives every row its band, verdict and
team's date, whether or not the hourly run has kept today's row yet, and a row
kept earlier today gives way to it. The impact read computes its today the same
way, so a row and the "Why the date moved" under it never show two different
85% dates for now. The markers still read the kept rows: today's breakdown joins
them once the hourly run keeps it.

`TimelineService` (`core/application/timeline_service.py`) builds the rows;
`core/domain/timeline.py` holds the pure rules: the axis (the earliest start to
past the last date, at least a minimum span), worst-first ordering, the row's
sentence and the no-forecast reason.

## One row

- **Span:** the baseline day to the committed date. With no baseline it starts
  at the first kept forecast day and says so; with no committed date it runs
  open to the axis end.
- **Committed history:** every committed date with the day it was set.
- **Forecast:** the 50% and 85% dates, the verdict and the day they are for
  (today's live, else the kept day's); else the reason there is none, from the
  row's own numbers. When the date a past day was measured against changed later
  that day, its band is judged against that day's date by the forecast's own
  rule: a comparison, not a forecast run.
- **Team's date:** the latest ETA or due date of the open requirements, its key,
  and how many have none.
- **Progress and scope:** done of total (story points when every requirement
  has them) and the scope at the baseline and now.
- **Markers:** one per day and kind, from `DeliveryImpactService.timeline_markers`,
  the same builder `delivery_impact` uses: scope added with and without the
  change label, scope removed, reopened, closing late, bugs opened, the
  committed date's moves, and blockers opened and cleared. Each lists its work
  items (key, title, stage) and, by kind, the change label, who brought it in,
  what a blocker waits on, and whether Jira hygiene has a finding open on it.
- **Words:** one sentence per row, for the picture's text alternative, in Why
  the date moved's words (`impact_words.likely_words`): "Checkout Revamp: likely
  by 19 Mar, 3 months after the committed 15 Dec; 2 of 10 done; 2 requirements
  added since planning, 1 without the CR label."

The read carries more than the picture draws (the committed history, the team's
date, a release's Jira date): the table and the words use it.

## Who sees what

Each row carries a level from the reader, on the `NodeAccess` levels:

| Reader         | Rows                                                                                      | Markers                                                |
| -------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Product owner  | every row, whole                                                                          | all but blockers; who added an item; the hygiene ring  |
| Manager, admin | every row, whole                                                                          | all; who added an item; the hygiene ring               |
| Executive      | every row, read-only                                                                      | no blockers, no "added by", no hygiene ring            |
| Scrum master   | their pods whole, the project's other pods dates only (span, ticks, band), no project row | all, on their own pods                                 |
| Developer      | no project timeline; their own pod's row, whole                                           | their pod's, blockers included; no "added by", no ring |

The hygiene ring follows hygiene's own readers: an executive reads its counts
only and a developer has no hygiene read. `OpenFindingKeys`
(`core/application/hygiene_open_findings.py`) gives the impact service the open
findings' issue keys in the scope's project, never an owner. The scan keeps no
history, so a past day's ring says how findings stand now.

## Console (frontend-v3)

Drawn to be read in five seconds:

- **Overall › Timeline** (`features/overall/TimelineSection.tsx`), second after
  the answer line in the order `features/overall/sections.ts` keeps. The
  drawing (`TimelineChart.tsx`, pure layout in `timeline.ts`) is inline SVG with
  theme tokens. Per row: the name, the verdict and one plain line (`rowLine`:
  "Likely by 19 Mar · committed 15 Dec · 2 of 10 done", the team's date only
  when a week off); a grey track from planning to the committed date with the
  share done filled dark; the likely finish (50% to 85%) as one segment in the
  verdict's colour, with its dates beside it when late; the committed tick; and
  today's line. Each day with changes is one dot with a count (`rowDots`), red
  when a change pushed the date, opening its plain words and items. Under the
  chart, "What changed" (`changeList`) gives each row's scope since planning and
  at most five days, newest first. The legend has at most five entries. Month
  ticks only; the axis ends a little after the latest likely finish. Labels sit
  in a column on a wide screen and above each bar on a phone or a Delivery card;
  the axis scrolls in its own box only when it must. Releases fold under the
  project's row. Arrow keys move along a row's dots and between rows; a Table
  toggle lists every row.
- **Why the date moved** (`ImpactWaterfall.tsx`, `waterfall.ts`) explains the
  row picked (`?row=`) over its period chips: the server's sentence, at most
  three causes as bars from zero (red right, green left), short lines for the
  likely date, the committed date, the team's date and the undated keys, and a
  closed Details fold for the measured date and a table.
- **How the likely date moved, day by day:** the slip chart, folded under it.
- **Delivery:** the project panel's timeline and a pod panel's own row
  (`features/delivery/DeliveryTimeline.tsx`), linking to Overall's row.

Retired with it: Overall's "Dates by pod", "How the date moved" as a section,
and the Delivery project panel's date history.

## Later

- A portfolio timeline, one row per project.
- Comparing releases.
- The timeline in a printed steering pack.
