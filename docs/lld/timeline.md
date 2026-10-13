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

Both read stored rows only and never run a forecast: the kept `forecast_days`
row of the day (else the last one kept before it), `planning_baselines`,
`requirement_snapshots`, `delivery_date_changes`, `issue_change` and issue facts,
`developer_blockers`, and open `hygiene_findings`. A past day (`as_of`) reads
that day's row and the markers up to it.

`TimelineService` (`core/application/timeline_service.py`) builds the rows;
`core/domain/timeline.py` holds the pure rules: the axis (the earliest start to
past the last date, at least a minimum span), worst-first ordering, the row's
sentence and the no-forecast reason.

## One row

- **Span:** the baseline day to the committed date. With no baseline it starts
  at the first kept forecast day and says so; with no committed date it runs
  open to the axis end.
- **Committed history:** every committed date with the day it was set.
- **Forecast:** the kept 50% and 85% dates, the verdict and the day they were
  kept; else the reason there is none, from the row's own numbers. When the date
  the kept day was measured against has changed since (a date set before the
  next hourly run), the kept band is judged against today's date by the
  forecast's own rule: a comparison, not a forecast run.
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
- **Words:** one sentence per row, for the picture's text alternative.

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

- **Overall › Timeline** (`features/overall/TimelineSection.tsx`), second after
  the answer line in the order `features/overall/sections.ts` keeps. The
  drawing (`TimelineChart.tsx`, pure layout in `timeline.ts`) is inline SVG with
  theme tokens: the row labels sit in a fixed HTML column, the axis scrolls in
  its own box at 28 px a week at the least, and on a phone it opens on today and
  the nearest committed date ahead. Releases fold under the project's row.
  Markers are buttons over the SVG with their own shape and label; arrow keys
  move along a row and between rows. A Table toggle lists every row.
- **Why the date moved** (`ImpactWaterfall.tsx`, `waterfall.ts`) explains the
  row picked (`?row=`), over the period chips, which also shade the axis.
- **Band over time:** the slip chart, folded under the project's row.
- **Delivery:** the project panel's compact timeline and a pod panel's own row
  (`features/delivery/DeliveryTimeline.tsx`), linking to Overall's row.

Retired with it: Overall's "Dates by pod", "How the date moved" as a section,
and the Delivery project panel's date history.

## Later

- A portfolio timeline, one row per project.
- Comparing releases.
- The timeline in a printed steering pack.
