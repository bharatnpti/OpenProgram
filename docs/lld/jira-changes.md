# Jira Changes LLD

What moved on each issue, who moved it and when, read from the issue
tracker's history by the issue sync and kept as facts on the work item.
Feature summary: `features.md` §4.7.

## Port

`IssueTracker.list_issue_changes(tenant_id, key, since) -> list[IssueChange]`,
oldest first, read-only. An `IssueChange` is `history_id`, `author` (a
`UserRef`, or None for an automation), `at`, `field` and `from_value` /
`to_value`.

Only the fields `IssueChangeField` names are kept: status, resolution,
assignee, due date, fix version, labels, story points (the configured field),
sprint, issue type, parent or epic, priority, components and the configured
severity field. A summary or description change keeps no values. Comments are
not part of the history and are never read.

- Values are names, keys, dates and numbers. An assignee is its tracker
  account id, never its name. A parent is the issue key found in the text, or
  nothing.
- A set field (fix versions, labels, components, sprint) lists what left the
  set in `from_value` and what joined it in `to_value`, comma-separated. One
  history's several items for one field are merged into one change, so
  `(history_id, field)` is unique.
- A field that ends where it started is no change.

## Jira adapter

- Cloud: `GET /rest/api/3/issue/{key}/changelog`, paged by `startAt` until
  `isLast` (at most 50 pages of 100). Items carry a `fieldId`.
- Data Center: `GET /rest/api/2/issue/{key}?expand=changelog`. Items name
  their field only. A configured custom field (story points, severity) is
  matched by the name Jira's `/field` list gives it, read once per adapter.
- Sprint, Epic Link and Parent Link are matched by name on both.
- The regular issue read adds `reporter`, `creator` and `components`, and the
  optional severity field, to issue metadata: `reporter_id` and `creator_id`
  (account ids), `components` (names joined), `severity` (an option's value).
- The severity field sits beside the story-points field on the Jira
  connection (`severity_field`, connection `settings`), with the connection
  test suggesting custom select and text fields, severity-looking ones first.
  Without a connection, `OPENPROGRAM_JIRA_SEVERITY_FIELD`.

## Facts

One fact per change, source `issue_change`, on the task entity (the issue
key), observed at the change's time, appended with `append_fact_once`:

- correlation id `issue_change:{tenant}:{key}:{history_id}:{field}`;
- payload `key`, `field`, `from`, `to`, `project_key`, `history_id`, and
  `author_member_id` (through `developers_by_tracker_account`) or
  `unlinked_author_id`; an assignee change also names `from_member_id` /
  `to_member_id` when those accounts are members.

A fact never lands on a person's entity and no name is stored. Nothing counts
or ranks changes per person.

Issue facts also keep `due_date`, `fix_versions` and `labels` from now on, and
the task node `reporter_member_id` / `creator_member_id` when linked.

## The pass

`IssueReadSyncService` runs it after each project or query sync, before the
cursor is recorded, on the same `openprogram_sync` queue tick. Two times in
the issue cursor's metadata hold its place:

- `issue_changes_through`: every issue updated up to it has had its history
  read;
- `issue_changes_since`: every change made up to it is recorded;
- `issue_changes_waiting`: issues seen changed and not yet read.

While `through` is at or past the issue cursor, the issues this sync returned
are exactly the ones to read. Otherwise (a first read of an existing target, or
a backlog) one search from `through` finds them. They are read oldest first,
at most `OPENPROGRAM_JIRA_CHANGES_PER_TICK` (default 200; 0 reads none) per
target and tick; issues updated at the same moment are never split. Until the
backlog is read, every history is read from `since`, so a change made before
an issue's turn is not lost; then both times move to the newest issue read.

The first read of a target goes back `OPENPROGRAM_JIRA_CHANGES_BACKFILL_DAYS`
(default 120). Clearing the cursor reads the window again; the facts are the
same facts.

**One read per issue update, across targets.** A project and its pods often
return the same issues, and their syncs run at once on the queue. Every target
of the tenant's connection shares one marker per issue: a cursor row
(connector `issue_history`, scope `issue:{KEY}`) holding when the issue was
read through and who is reading it now.

- A target claims an issue before reading it, with one conditional write
  (`SyncCursorRepository.swap_cursor`), so of two targets only one reads it.
- An issue already read through its update costs no read. Neither does one
  another target is reading; that one counts once the other has finished.
- Only a target's own reads count against its budget.
- A claim older than 10 minutes is a reader that died, and another target takes
  it over.

**A failing issue.** A history that cannot be read stops the pass where it got
to and never fails the sync.

- The third failure in a row (`issue_changes_failures`) skips the issue. The
  pass moves past it, and one warning names the key and the count, never the
  tracker's text.
- The skipped issue is kept in `issue_changes_skipped` with where its history
  stood, and is tried again at most once a day, or as soon as it changes again.

## Readers kept unchanged

- The non-response inference's newest ten facts leave `issue_change` out.
- A task's colour is never taken from an `issue_change` fact.

## Snapshots

`requirement_snapshots.payload` gains an `item` map, `{KEY: {points, type,
due}}` (`RequirementsSnapshot.details`), written from now on. Older days have
none.

## Storage

No migration: `facts` rows, JSONB metadata, cursor metadata and the
connection's `settings`.
