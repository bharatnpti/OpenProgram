# Release Readiness LLD

What a release, project or pod needs before production beyond each
requirement's gates, checked by rules over synced Jira data, with drafted
issues a person creates. Feature summary: `features.md` §4.31.

## Model

- `core/domain/release_readiness.py` is pure: `ReleaseCriterion` (scope kind,
  stage, lead working days, severity, needs-done, "only where" labels and
  types, matchers, draft template), `evaluate_criterion`, `urgency_of`, the
  fingerprint, draft rendering, validation and `default_examples()` (six
  generic criteria, none on by default).
- A matcher is a label, an issue type, a title phrase (case and punctuation
  folded, whole words), the title's words in any order, or the epic (key or
  title phrase). Each counts as evidence or is only a candidate.
- Evaluation, in order: a person's link wins (a key gone from the synced
  issues reads unsure, one closed as not counted reads missing); any evidence
  match covers it, done when any match is done; candidate matches leave it
  unsure; else missing. Not applicable keeps the rules' state for a reopen.
- Urgency: the stage reached (a requirement of the scope at or past the stage
  while not ready), overdue, due soon (within the lead, at least five working
  days, of the due day: the scope's date less the lead; or a requirement in
  the stage just before), no date, later.

## Scopes

- Project: its owned tasks of any type (`owned_project_tasks`).
- Release: the project's owned tasks the release `includes()`. A release
  criterion is judged on the project while it has no releases.
- Pod: the pod's tasks within its remit (`PersonaViewService.pod_tasks`); a pod
  in two projects is judged once, against its earliest committed date.
- Dates come from `ForecastService.project_delivery` (committed, or Jira's
  release date for a release). Stages come from the tenant's stage mapping.

## Storage

Migration `0040_release_readiness`, additive: `readiness_settings`,
`readiness_criteria` (soft delete), `readiness_overrides` (later use),
`readiness_runs` (unique per slot), `readiness_findings` (unique per criterion
and scope, with its fingerprint), `readiness_suggestions` (one per finding for
its whole life) and the append-only `readiness_actions`. In-memory
repositories serve memory mode and tests.

## Runs

- Hourly at :45 (`OPENPROGRAM_READINESS_SCAN_CRON`, switch
  `OPENPROGRAM_READINESS_SCAN_ENABLED`), after the :00 Jira sync. On DBOS a
  superseded tick does nothing and the current one is enqueued on
  `openprogram_sync`; Temporal mirrors it. The run claims its slot first, so a
  retried or doubled tick does nothing new.
- Run check now runs inline for a project (with its releases and pods) or a
  pod, and returns the board.
- An unchanged fingerprint writes and audits nothing. While the issue sync is
  failing or behind, a covered finding is never turned missing and a new
  blocking gap is held out of the day report.

## Create in Jira

The one caller of `IssueTracker.create_issue`. In order: the draft's version,
the readiness `create_in_jira` setting and the tenant's write-back switch (both
read on every press), the scope evaluated again (a criterion covered since is
refused, naming what covers it), a claim `open -> creating`, a search for the
draft's marker label `op-rr-<8 hex>` (an issue an earlier try made is adopted),
then one create: unassigned, the approver as reporter where the tracker allows,
and a footer naming who approved it. The issue is recorded as the next sync
would, and the finding is covered by it. Failures say a fixed category, never
the tracker's text, and leave the draft open.

## Who may

`ACT_ON_READINESS` for the product owner, scrum master and manager (an admin
has all). A scrum master acts only on the pods they run and the projects those
pods work on; an executive reads the project's and releases' rows without
drafts; a developer reads none. A blocking criterion is marked not applicable
only by a manager or an admin. Configuration needs `MANAGE_CONFIG`. Each
finding carries its `can` for the viewer.

## Day report

`ReleaseReadinessService.report_gaps` gives the blocking gaps that are missing
or unsure, undecided, not held and urgent: up to three Most important lines,
then "and N more in Release readiness", and a decision ask each, to the
project's decision owner (a pod's scrum master for a pod's), escalated by the
project's matrix from the day the gap entered its window. With the agent off
or no criteria the report is unchanged byte for byte.

## Later

A model judging the unsure ones (grounded on the candidates only), a live JQL
matcher for evidence outside the scope, per-scope overrides, scope-change
triggers, a queue of its own, and the readiness asks on Today.
