# Planning Baselines and Forecast Days LLD

The requirements agreed when planning was complete, frozen so later scope is
read against them, and each scope's forecast kept per day so its history is
read, not replayed. Feature summary: `features.md` §4.33.

## Model

- `core/domain/baseline.py` is pure: `PlanningBaseline` (scope kind `project`
  or `release`, scope id, project id, kind, `planned_on`, the frozen `items`,
  `reconstructed`, `snapshot_day`, change label, grace, `set_by`, `set_at`,
  note, `superseded_at`), `BaselineItem` (stage, points, due, type; None where
  not known), `since_baseline` and the validators.
- Kinds: `snapshot` (frozen from the requirements as they stood when it was
  marked) and `date` (frozen from an earlier day).
- One baseline is active per scope: the one with no `superseded_at`. A new one
  sets the old one's `superseded_at` and keeps it, so "since the first
  baseline" still reads.
- `since_baseline(baseline, current, labels)`: keys in scope now and not in
  the baseline (`added`), those of them carrying the change label, compared
  without case (`added_with_label`), and keys in the baseline no longer in
  scope (`removed`). Keys only: who added a requirement is never stored or
  shown.
- The change label is the tenant's word for a change agreed after planning:
  one tracker label, no spaces, at most 64 characters, `CR` by default. Grace
  is 0 to 20 working days, 2 by default: how long after planning an added
  requirement still counts as planned. It is stored on the baseline; no read
  applies it yet. A note is at most 300 characters.

## Planning complete

`BaselineService.mark_planning_complete(tenant, project, release_id,
planned_on, change_label, grace_working_days, note, actor)`:

- **Today** (`planned_on` null): the requirements in scope now, as the
  delivery view places them, each with its stage and the story points, due
  date and type its issue carries.
- **An earlier day**, at most a year back (a future day is refused): the
  scope's stored requirement snapshot of that day, or of the last day before
  it that kept one within 7 days (`snapshot_day` says which). Points and type
  come from each issue's last issue fact synced before the day ended in the
  tenant's zone.
  - A release with no snapshot of its own reads the project's, kept to the
    release's requirements of today.
  - With no snapshot at all, the issues in scope today that the tracker says
    were created by then stand in.
  - Both fallbacks are marked `reconstructed`.
  - Nothing to freeze is a 422.
- The actor is stored as `set_by` (a member id or chat id). A read names them
  through `PersonNames` (member record, then the directory), else "a team
  member"; never an id.

## forecast_days

Each project's, release's and pod's forecast as it stood on one day, under the
project it was computed in (`project_id`: the project itself, the release's
project, or for a pod the project whose part of the pod it is):

- `p50` and `p85` dates, the verdict and the `target_date` it was measured
  against (committed, else the tracker's release date);
- the team's latest date and the requirement carrying it;
- the inputs: remaining, unit, sample days, completed in the sample, and the
  minimum sample days that day;
- `reconstructed`, `computed_at`, and `attribution` (empty for now, for the
  breakdown of why the date moved).

**The hourly write.** The delivery snapshot run
(`run_delivery_snapshot_activity`) stores the day's requirement snapshots,
then calls `ForecastService.record_forecast_days(tenant, day)`. That writes one
row per project and release, from the same delivery view the console reads. A
later run the same day replaces the rows, so the day's last run stays. If the
write fails, the failure is logged and the snapshots stay stored; the history
read replays the day.

A pod's row is its part of one project's work: that project's keys, date and
seed. A pod that works on two projects keeps one row a day under each, from each
project's own delivery view and breakdown, and every reader asks for the row
under the project it reads (`list_between` with the scope's project).

## Reconstruction

`ForecastService.reconstruct_forecast_days(tenant, as_of, days)`, 90 days by
default, at most 365:

- Each day before `as_of` with a stored snapshot is forecast the way the
  history read replays it: the same snapshots, keys, seed and today's minimum.
- It is measured against the committed date as it stood at the end of that
  day, and the ETAs stated by then (check-ins or the console).
- Due dates are today's: their history is not kept.
- A release's window is the project's, kept to the release's keys of each
  day.
- Rows are written `reconstructed`. A day the hourly run kept is never
  replaced.
- It returns how many scopes it covered, how many days it wrote and how many it
  left as they were.

It runs as the snapshot workflow with `reconstruct_days` on its input, so it
needs no workflow connector of its own. `POST
/admin/workflows/forecast-days/reconstruct` dispatches it.

## History read

`ForecastService.forecast_history(tenant, project, as_of, release_id, days)`
(at most 90 days) backs `GET /projects/{id}/delivery/history`:

- A day before today reads its kept row: the forecast as it stood that day,
  with that day's minimum.
- A day nobody kept is forecast the way `project_delivery` forecasts it on that
  day, and so is today, which is always live.
- Each day says `reconstructed`, so the console can tell a rebuilt day from one
  kept on the day.

After a reconstruction, the read equals the replay on every rebuilt day and
equals `/delivery?as_of=<day>`; it is much faster, because past days are rows,
not simulations.

## Storage

Migration `0042_baselines_forecast_days`, after `0041_pod_member_profiles`.
It is additive and has no data step.

- `planning_baselines`:
  - PK `id`;
  - the frozen keys in `payload`:
    `{"keys": {KEY: {stage, points, due, type}}, "reconstructed", "snapshot_day"}`;
  - a partial unique index for one active baseline per (tenant, scope kind,
    scope);
  - indexes by scope and by project.
- `forecast_days`: PK (tenant, scope kind, scope, project, day) since
  `0044_forecast_days_project` (before it, (tenant, scope kind, scope, day),
  so a pod on two projects kept no row on those days), with an index by
  project and day.

`0044_forecast_days_project` only changes the key: `project_id` has been on
every row, NOT NULL, since 0042, so there is nothing to backfill. Its
downgrade drops a pod's days kept under more than one project (the old rule)
and keeps the row computed last for any other scope kept under two.

The downgrade drops both tables; nothing else is touched, neither the graph nor
the AGE mirror. Memory mode and the tests use in-memory repositories.

## Who may

| Action                      | Capability              | Roles                                    |
| --------------------------- | ----------------------- | ---------------------------------------- |
| Read the baselines          | `READ_PROJECT_PROGRESS` | product owner, manager, executive, admin |
| Mark planning complete      | `SET_PROJECT_DATES`     | product owner, manager, admin            |
| Dispatch the reconstruction | `DISPATCH_WORKFLOWS`    | admin                                    |

- The baselines read takes the same right as the forecast's history.
- Planning complete takes the right that commits the project's and the
  releases' dates.
- The tenant always comes from the principal.
- Others get 403.

## API

- `GET /projects/{id}/baselines?release_id=&as_of=`: the scope's baselines as
  they stood on `as_of`, newest first. Each comes with its `number`, counts
  (`requirements`, `open`, `points` when every requirement carried them) and
  `since`: the added, labelled and removed counts and keys against that day's
  scope. Also returns the active one, `in_scope`, and the default label and
  grace.
- `POST /projects/{id}/baselines` with
  `{release_id, planned_on, change_label, grace_working_days, note}`: 201 with
  the new baseline. 404 for an unknown
  project or release; 422 for a refused label, grace, note or day, or nothing
  to freeze.
- `POST /admin/workflows/forecast-days/reconstruct` `{days}`: the workflow id.
- `ForecastDayResponse` (the history read) gains `reconstructed`.

## Console (frontend-v3)

Overall › Planning baseline sits under the Timeline (`docs/lld/timeline.md`),
for the forecast's readers:

- the active baseline with who set it and when;
- what was added since (and how many without the change label) and what is
  gone;
- the earlier baselines.

Mark planning complete (New baseline once one exists) shows only to roles with
`setProjectDates`, and not while a past day is shown. Its dialog offers now or
an earlier day, the label, the grace and a note. The slip chart (the project
row's Band over time) notes rebuilt days ("The forecast up to … was rebuilt
afterwards").

## Later

- The breakdown of why the date moved, written to `forecast_days.attribution`
  (p50, p85 and `reconstructed` stay as they are).
- Reads that apply the grace period to added scope.
- Pod rows.
- Due-date history for reconstructed days.
