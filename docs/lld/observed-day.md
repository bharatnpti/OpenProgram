# Observed Day LLD

What moved on a member's work in the issue tracker and Git, used where a day
has no words from the member: a check-in that closed unanswered, a reply
without a status, and a member the check-in never reached. It reads the
`issue_change` facts of `jira-changes.md`. Feature summary: `features.md` §4.35.

## The digest (pure)

`core/domain/observed_day.py`:
`digest(member_id, since, until, *, issue_changes, issue_facts, vcs_facts,
task_updates, assigned_keys, mapping) -> ObservedDay`.

- **Tasks:** the keys the member holds, plus any task assigned to or away
  from them inside the window.
- **Events per task**, from `issue_change` facts inside `[since, until]`:
  - a status move, worded by the tenant's stage mapping: "moved to QA (in
    development → in testing)", "moved back to …" when the stage went back;
    several moves read as one, first to last, and one that ends where it
    started is none;
  - resolved, reopened (not said beside a move);
  - due date and estimate moves, first value to last;
  - added to, taken out of or moved between releases;
  - assigned to them, assigned away from them.
- **Git**, from the member's own facts: a merge request opened, reviewed or
  merged in the window, by its number (`!12`), on the task its branch or
  title names; commits counted per task their message names. A request that
  names no task is listed on its own. Message and title text is never kept.
- **Worked on:** keys of other people's issues the member changed.
- **Quiet:** a task under way (its latest issue fact `in_progress` or
  `blocked`) with no movement for `QUIET_AFTER_DAYS` (5): its last kept
  change, else when its status became what it is. A task the member updated
  in the console in the window is not quiet. Quiet is not evidence.
- `ObservedDay.has_events` is true when anything moved. `lines(day)` words it,
  most recent first, with caps ("2 more tasks moved", "worked on A, B, C and
  2 more", "1 more task without movement").

`observe_task(key, facts, *, start, until, mapping, names)` gives one task's
line: its status now, since when (the last status change into it), who moved
it, the other changes from `start`, and quiet days.

Values are keys, status and stage names, release names, dates and numbers.

## Reading it

`ObservedDayReader.digest_for(tenant, member, as_of, until, extra_keys)`
(`core/application/observed_day_service.py`):

- the window opens at the later of the member's last `checkin` fact with
  `status_source` confirmed or partial, and their check-in time on their
  previous check-in day (their own time and zone);
- held keys from active `ASSIGNED_TO` edges, plus `extra_keys`;
- each held task's facts over 30 days; the tenant's `issue_change` facts in
  the window (assignments, and the member's changes elsewhere); the member's
  Git facts over 30 days;
- the stage mapping from delivery settings.

## Where it is used

| Path | Lead | With nothing moved |
|---|---|---|
| Close-out of an unanswered check-in (`_fallback_inference`) | "No confirmed check-in after a nudge. Inferred from Jira and GitLab: …" | the earlier rule: open work under way, last reply, risks, Git counts |
| Close-out of replies without a status | "Replied without a status update. Inferred from Jira: …" | unknown, as before |
| The fill (`StatusCollector.fill_day`) | "Not reached in chat. Inferred from Jira: …" ("Not asked today." when the send is off) | stale, naming the last status once, else unknown |

The close-out adds the tracker's live list of the member's open issues to the
held keys, only those naming them as assignee. Every such day is `INFERRED`,
amber in the rollup. `status_summaries.inferred_basis` reads the basis after
any of these leads, so a later stale day names it once.

## The fill

`FillDayPass.run(tenant, now)` (`core/application/fill_day.py`) runs inside the
reconcile tick's plan (`checkin_fanout._checkin_reconcile_dispatch_plan`),
before its dispatches, for today and yesterday in the tenant's zone. It fills
a member from `developers_without_checkin` when:

- their member record exists and was not deleted by the day;
- the day is one of their check-in days;
- `now` is past their check-in time plus their nudge wait, in their zone;
- they have no status for the day and no check-in run for it;
- the calendar does not put them on leave.

It sends nothing, and a failure never stops the tick's dispatches. The tick
exists only while the fan-out and the reconcile are both on.

The tenant switch is `checkin_fill` in `readiness_settings.settings`
(`{enabled, updated_at, updated_by}`); release readiness's save keeps every
key but its own. `GET/PUT /config/checkin-fill` (admin only) reads and sets
it; `OPENPROGRAM_CHECKIN_FILL_ENABLED=false` switches it off for the
deployment. On by default.

## Read side

- `PodTaskDto.observed` and `FocusTaskDto.observed`: `status`, `stage`,
  `since`, `moved_by` (a name, only when not one of the task's own people),
  `changes`, `quiet_days`; null when the task's facts say nothing.
- `PodCheckinsResponse.coverage`: `replied`, `inferred`, `no_data`, `waiting`
  and `causes` (`no_jira_link`, `jira_not_synced`, `no_chat_id`,
  `nothing_moved`). Each developer row gains `evidence` (read back from an
  inferred summary) and `no_data_cause`.
- Ask's `pod_checkins` returns both under the same pod check-in read.

## Storage

No migration: `facts`, the status summary, and a JSON key in
`readiness_settings`.
