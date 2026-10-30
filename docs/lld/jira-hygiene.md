# Jira Hygiene LLD

Per-issue rules a tenant keeps, checked over the synced Jira issues of each
project, release and pod, with an owner and a suggested fix per finding. Read
only on Jira. Feature summary: `features.md` §4.36.

## Model

- `core/domain/hygiene.py` is pure: `HygieneRule` (scope kind, `Select`,
  `Condition`, severity `list_only` or `ask`, need `fix` or `decision`,
  `Owner`, optional `blocking_before`, suggested fix), `HygieneSettings`
  (switch, change labels, stale working days), `evaluate_rule`, the
  fingerprint, words (`rule_words`, `ask_text`, `count_line`, `render_fix`),
  validation and `default_examples()` (eight generic rules, all list only).
- `Select`: counted requirements (the stage mapping's types, placed on a
  stage) or every issue type; a stage filter (any, before a stage, in a stage
  or later); readiness's matchers (`release_readiness.match`), all of which
  must match.
- Conditions: `added_after_baseline_without_label`, `missing_label`,
  `missing_field`, `missing_gate_kind`, `stale_in_status`, `past_due_open`,
  `too_big`. Each returns a `Breach` with fixed words, a detail of field
  names, stages, labels, keys, numbers and dates, and the day an ask waits
  from.
- The change label rule: an issue in scope, not among the baseline's keys,
  without any of the rule's labels (empty: the baseline's label and the
  tenant's change labels), that joined after the baseline day plus its grace
  in working days. The join day is its created day when after planning, else
  the first kept change after planning to its fix version, type, parent or
  status (`issue_change` facts); unknown, it is found once the grace has passed.
- Per-issue data comes from readiness's `ScopeIssue`, which carries stage,
  requirement, created and due days, points, priority, fix versions,
  components, severity, assignee and reporter for hygiene; readiness ignores
  them.

## Scopes and owners

- `HygieneService` reads `ReleaseReadinessService.scope_contexts`: the project,
  each release, each pod working on it. A pod in two projects is judged once.
- An owner is resolved at run time: the rule's kind (assignee, reporter, a
  profile's holder in the issue's pods by `ProfileService.profiles_on`, the
  decider), else the pod's scrum master (escalation contact, else a member
  with the role), else the decider (the matrix's decision owner, else the
  project's owner, else a product owner of its pods). The finding keeps the
  member id, how they were found and the pod they stand in.

## Storage

Migration `0043_jira_hygiene`, additive: `hygiene_settings`, `hygiene_rules`
(spec as JSON, soft delete), `hygiene_runs` (unique per slot),
`hygiene_findings` (one open per rule, issue and scope by a partial unique
index; resolved and decided rows are kept) and the append-only
`hygiene_actions`. In-memory repositories serve memory mode and tests.

## Runs

- Inside the readiness tick (`infra/workflows/readiness.py`, hourly at :45),
  behind `OPENPROGRAM_HYGIENE_SCAN_ENABLED`; the tick's schedule stays while
  readiness or hygiene is on. A hygiene failure never fails readiness's result.
- The run claims its slot, then judges every enabled rule on every scope of
  its kind. An unchanged fingerprint (rule version, key, scope, detail, owner,
  the issue's update time, held) writes and audits nothing.
- Staleness gate (the issue sync's health): no finding closes, and a new
  finding of an asking rule is `held` until fresh data confirms it.
- A waived or not-applicable finding stands while the breach does; reopen
  judges it again. Check now runs one project inline.

## Surfaces

- API (`api/routers/hygiene.py`): `GET /projects/{id}/hygiene` (`release_id`,
  `pod_id` filters), `GET /pods/{id}/hygiene`, `POST /projects/{id}/hygiene/run`,
  `POST /hygiene-findings/{id}/waive|not-applicable|reopen|recheck`,
  `GET /hygiene-findings/{id}/history`, and `/config/hygiene` (settings, rules,
  preview) for admins. Reads keep `readiness_access`; an executive's board has
  rule counts only (`counts_only`). Actions take `ACT_ON_READINESS`.
- Day report (`day_report_hygiene.py`): the count line under Where we stand
  (and the console's progress notes), and one ask per owner per asking rule
  per scope, built as an `Ask` and escalated by `escalated()`. An owner the
  scan could not name goes to `readiness_decider`.
- Ora: `jira_hygiene` (`ask_hygiene.py`), offered where a readiness board
  answers (`reads_some_board`), never to a developer.
- Console: Overall's Jira hygiene section and Admin › Jira hygiene with Try on
  a project; `access.hygiene` mirrors the readiness readers.

## Later

Blocking before a stage in Most important and the readiness summary row,
the developer's quiet hint on their own issue, and Fix in Jira behind a Jira
writes switch of its own.
