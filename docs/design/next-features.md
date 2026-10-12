# Next features: design

Design note. Status: **proposal, not built**. Written 2026-10-12
against `main` at `718ce3e4` plus PR #24 (`fix/v2-round` at `49bea798`). Alembic head is
`0040_release_readiness` on `main`, on PR #24 and on every local branch; no branch has a `0041`.
Code references are to the PR #24 tree (`backend/...`, `frontend-v3/src/...`).

Repo rule: OpenProgram is public. Everything in this file that may reach the repo is generic.
Company names, internal projects and tenant-specific rules (a tenant's change label, its
severity field, its own profile names) are **tenant data**: they live in the tenant's database
and in private notes, never in code, tests, fixtures, docs, commits or PRs.

The user's requests, verbatim:

> 1. "we need to pull data from jira for each user configured in pod/project and use the changes
>    their to infer their status (in case they have not replied) (if this what infer does then ok)
>    - this should solve for capturing the data completely"
> 2. "we need to assign profile to each member in jira (like security, product, dev, tpm) is not
>    already" — clarified: **"assign profile when adding to pod"**.
> 3. "we also need to show what resulted in change in delivery timeline or impacted it - addition
>    of new features, delay in requirement closure etc - ORA to also support this"
> 4. "we also need to run scan in jira and find the jira which does not meet/has the required tag
>    like (CR which means new feature added after planning) and report it"
> 5. "think of more such features which can help in giving the clear picture in today's rapid (and
>    chaotic) software lifecycle starting from planning completion (done by humans) to delivery"
> 6. "project/pod wise timeline / gantt chart visualization will help (showing the progress and what
>    impacted timeline)"

---

## One-page summary

**1. The Jira-observed day.** Today, a check-in that closes unanswered is "inferred" from the
person's **open** In Progress or Blocked Jira issues (read live), the date of their last reply,
risks filed on them and a count of Git events. It does not use **changes**: nothing finished,
nothing moved, no due date that slipped. It also runs only for people who were sent a check-in
and stayed silent. Someone who was never asked, or who answered "nothing from me", gets no
inference at all. In a QA tenant, 14 of 57 day statuses are inferred, and they read like "Inferred from
last check-in reply on Oct 4." **So request 1 is only partly covered.** The design:
- Read Jira's changelog for the issues each sync sees changed, and keep it as `issue_change`
  facts: who moved what, when.
- Build each person's "what moved since their last answer" from those facts and from Git.
- Use that in the close-out; for every configured member who wasn't asked or answered without a
  status; and per task, beside what the person said.
- Show per pod how the day was captured. A day built from Jira is still never green.
- No migration.

**2. Profiles, set when someone is added to a pod.** A **profile** (development, QA, security,
product, design, TPM / delivery, ops, ...) is part of the pod membership, so a person can hold a
different one in each pod. It sits beside the existing free-text "role in pod" label (developer,
scrum master, product owner, manager), which keeps meaning accountability.
- The admin picks a profile in Admin › Links when adding a person to a pod, and in the directory
  import.
- A suggestion comes from the person's directory title and the kinds of Jira and Git work they
  do. It is never set automatically.
- Existing memberships are backfilled from their pod role.
- Routing reads the profile per pod: readiness fix asks, hygiene owners, an escalation level
  "the team's QA", drift's "code work" test, the day report's names, and Ora.
- A profile is a role, never a rating. Migration `0041`.

**3. Why the date moved.** Product owners, managers and executives see what moved the forecast.
- **Planning baseline.** A product owner marks "planning complete" for a project or release,
  which freezes the requirement set.
- **The waterfall.** Each day the forecast is replayed with one input changed at a time, under
  one seed. That gives an exact waterfall of the 85% date:
  - time passed and work finished (pace);
  - scope added, split into change requests, additions without the change label, and bugs;
  - scope removed, reopened work and re-estimates;
  - throughput (the history window changing).
  The parts add up to the real change.
- **Beside the forecast:** the committed date's own moves (who, why) and the team's date (which
  ETA or due date moved). As evidence, never as a share: late requirements, blocked days, and
  bugs that aren't counted as requirements.
- **Where it shows:** the waterfall under the Timeline on Overall (feature 5), one line in the
  day report, and Ora's read-only `delivery_impact` tool.
- Migration `0042` adds the baseline and a per-day forecast record.

**4. Jira hygiene scan.** Tenant-defined, per-issue rules run hourly beside release readiness.
They reuse readiness's scope issue sets, matchers, staleness gate, fingerprints, audit and
Admin "try on a project".
- Examples, all off until added:
  - added after the baseline without the change label (the CR rule);
  - a story with no estimate, no acceptance criteria (read from the gates' scan) or no due date;
  - a bug without a severity;
  - an orphan with no epic;
  - stale In Progress;
  - unassigned work in development.
- Each finding names the issue, its owner (the assignee, a profile in the pod, or the decision
  owner) and a suggested fix.
- "Ask" rules turn into one ask per owner per rule and climb the escalation matrix. A rule may be
  blocking before a stage, as a readiness criterion is.
- Overall section, Admin tab, day report, and Ora `jira_hygiene`. Migration `0043`.

**5. Timeline (Gantt) per project and pod** (section 5). One row each for the project, its
releases and its pods, from the planning baseline to the committed date.
- **What each row shows:**
  - progress as a fill;
  - the 50% to 85% forecast band;
  - the team's date;
  - the committed date with ticks for where it stood before;
  - on the day each happened, impact markers for: scope added (with or without the change label),
    blockers opened or closed, requirements closing late, bugs, and date changes. Each marker
    opens its work items.
- **What it replaces:** on Overall, "Dates by pod" and "How the date moved". The waterfall sits
  under it for the selected row. On the Delivery project panel, it replaces the date history.
  The pod panel gets its own row.
- **Who sees what:** the server decides per row. A developer sees only their own pod's row in full.
- **Built as:** inline SVG that scrolls inside its own box on a phone, with a table as its text
  alternative. Ora reaches the same data through `delivery_impact`.
- **Cost:** about 2 days in lane J, right after the attribution lane. No migration.

**6. Twelve more ideas, ranked** (section 6). Top five: what-if on the forecast, scope burn-up
against the baseline, ageing work by stage, a weekly steering pack, and forecast calibration
("how right were we").

**Migrations.** Three, all additive: `0041_pod_member_profiles`,
`0042_baselines_forecast_days`, `0043_jira_hygiene`. The Jira read, the observed day, the
attribution engine and the timeline need none: they use `facts`, JSONB payloads and stored rows.

**Build order.** At most three lanes at a time, in four waves:
1. Jira read v2, Profiles, Baseline + forecast days.
2. Observed day, Why the date moved (engine, day report, Ora), Hygiene.
3. Timeline with the waterfall, What-if and burn-up, Ageing and calibration.
4. Steering pack.

**Biggest risks.**
- The Jira-observed day being read as activity tracking. Mitigation: per task, never counts, the
  same readers as today, still amber.
- Attribution read as blame. Mitigation: drivers are work items; "added by" appears only on the
  item's row, never summed per person.
- Hygiene noise. In a QA tenant, 33 of 33 issues have no estimate and no due date. Mitigation: rules off
  by default; "list only" by default; one ask per owner per rule; preview before enabling.

---

## Decisions

| # | Question | Recommendation | Trade-off | Phase |
|---|---|---|---|---|
| D1 | A new status source for "built from Jira"? | **No.** Keep `INFERRED`; enrich what it is inferred from. The rollup keeps inferred at amber. | Leaders can't tell "inferred, lots moved" from "inferred, nothing moved" by colour. The reason line says it. | MVP |
| D2 | Current state or changes? | **Changes.** Read the Jira changelog of each issue the incremental sync returns, and append whitelisted field changes as `issue_change` facts with the author mapped to a member. | One more Jira call per changed issue, bounded per tick. | MVP |
| D3 | Whom to fill? | **Every member the fan-out would ask, on their check-in days, with no status by the day's close.** That covers not asked (no chat id, send failed, send switched off), and replied without a status. Not people on calendar leave. A tenant switch, on by default. | Members without chat get a status they never gave; it says "from Jira" in its first words. | MVP |
| D4 | Jira versus the person's word | **The person's word wins.** Jira never changes a confirmed or partial status. It adds a "Jira today" line per task, and the existing drift rules flag disagreement. | Two lines per task when they disagree; that is the point. | MVP |
| D5 | Where does the profile live? | **On the pod membership, as its own dated row per (pod, member).** It is not edge metadata: edge identity includes metadata (`postgres_graph.add_edge`), and the AGE mirror would need rewriting on every change. | One join on reads that need it. | MVP |
| D6 | Profile versus role in pod | **Two fields, one dialog.** Role in pod = accountability (unchanged list). Profile = discipline. The profile defaults from the role. | One more picker. Merging them would lose "a QA who is also the scrum master". | MVP |
| D7 | Inferred from Jira? | **Suggested, never set.** From the directory title (already synced, never copied) and the issue types, labels, components and Git work of the last 90 days. The admin accepts. | Admin effort at onboarding. A "Review suggestions" list does it in one pass. | MVP (title, types), P2 (groups, components) |
| D8 | Does a profile grant rights? | **Never.** Rights stay with sign-in roles. A profile only routes: who is asked, named and escalated to. | Gate sign-off still needs the PO or manager role, even for a "product" profile. | MVP |
| D9 | Planning baseline | **An explicit human act**: "Planning complete" per project or release, freezing the requirement keys (with stage, points and due date). One active per scope; re-baselining keeps the old one. A date may be given instead, and the stored snapshot of that day is frozen. | Nothing works "since planning" until someone presses it. Overall prompts for it. | MVP |
| D10 | Attribution method | **Ordered counterfactual replay** of the product's own forecast. One seed, a fixed order, and steps that telescope exactly to the real change. Multi-day periods sum the daily rows. | Order matters slightly; it is documented. Shapley values are P3, if anyone disputes it. | MVP |
| D11 | Which date? | **The 85% date** (the verdict's on-track test), with the 50% date one toggle away. | Two numbers on the chart; the default is the one the verdict reads. | MVP |
| D12 | What is a share and what is evidence? | **Quantified:** pace, scope added and removed, reopened, re-estimated, throughput. **Evidence only:** blocked days, late requirements, bugs not counted as requirements. They explain pace; they are never given a share. | Leaders may want "blockers cost 4 days". We say what we can measure. | MVP |
| D13 | Store the forecast per day? | **Yes: `forecast_days`.** Seven replays per scope per hour is too slow to do on read for 30 days. Today's history read also applies today's minimum and today's committed date to past days. | Migration `0042`. Past days are reconstructed once and flagged. | MVP |
| D14 | "Added by whom" | **The changelog author** of the change that brought the item into scope (created, fix version or label added), else the reporter. Shown on the item's row to the project's delivery readers, **not to executives or developers** by default. Never summed per person. | Executives see "added 8 Oct, no change label", not a name. | MVP |
| D15 | Hygiene engine | **Per-issue predicates over readiness's scope sets and matchers, in tables of their own.** `readiness_findings` is unique per criterion and scope and cannot hold per-issue findings. | A sibling engine, not one more criterion kind. | MVP |
| D16 | Hygiene asks | **One ask per owner per rule per scope**, listing up to five keys. Ask kind `fix` or `decision`. It climbs the matrix from the finding's first day. Rules default to "list only". | Fewer, bigger asks. | MVP |
| D17 | The change label | **Tenant data.** "CR" is a generic example, set per baseline (default from hygiene settings). | — | MVP |
| D18 | Ora | **Three read-only tools** (`delivery_impact`, `jira_hygiene`, `team_profiles`), each gated exactly like its endpoint. `pod_checkins` gains each inferred day's evidence. Investigate gets planning lines for "why did the date move" and "is Jira clean". | Four more guidance lines in the prompt. | MVP |
| D19 | Migrations | **Three, additive**, numbered by merge order. Each lane runs its own in a database copy, never on the shared `openprogram` database. | Lanes rebase `down_revision` on merge. | — |
| D20 | Timeline versus today's date pictures | **One Timeline section replaces "Dates by pod" and "How the date moved" on Overall, and the date history on the Delivery project panel.** The committed date's history becomes ticks on each row, and the waterfall says how the band moved. The slip drawing stays as the project row's "Band over time" detail. | Readers used to the slip chart find it one level down. | lane J |
| D21 | Timeline rows per role | **The server words each row by `NodeAccess`:** a whole row (panel), a dates-only row (dates), or no row (name). Blocker markers need the pod blocker read. Scope items' "added by" follows D14. | A scrum master sees other pods' dates without their markers. | lane J |

---

## 1. Request 1: what inference does today, and the Jira-observed day

### 1.1 What inference does today (read from the code)

**When it runs.** Only in the close-out of a check-in that was **sent and not answered**.
- The nudge ladder ends in `close_checkin_non_response_activity`
  (`backend/infra/workflows/nudge.py:393`), which calls `StatusCollector.record_non_response`
  (`backend/core/application/status_collector.py:1846`).
- `_close_out_status` (:1875) handles the earlier outcomes first:
  - it finalizes a reply that arrived but was not processed;
  - it keeps a status given in the console that day;
  - otherwise it calls `_fallback_inference` (:1983).
- `infer_fallback_status` (:1970) is public and **has no caller** outside tests.

**What it reads.**
- **Issues, live, from Jira:** `IssueTracker.list_active_for(assignee)`, which runs the JQL
  `assignee = <id> AND statusCategory != Done ORDER BY updated DESC`
  (`backend/infra/adapters/jira/jira_adapter.py:198`).
  - The query covers every Jira project, not only the synced ones.
  - The assignee id comes from the identity link: `jira_account_id`, or `jira_email` resolved
    once. Without either it falls back to the chat id, and Jira finds nothing.
- **Facts:** the person's recent facts on their developer entity and their active issues
  (`_recent_facts`, :3852). The window is the last 30 days (`recent_fact_lookback_days`,
  `config/settings.py:203`), newest first, **cut to 10**. In practice that means check-in
  facts, risk facts, and VCS commit and pull request facts.

**The rule.**
- Only work under way is evidence: issues in `IN_PROGRESS` or `BLOCKED` (`_ACTIVE_ISSUE_STATES`,
  :4940).
- If Jira lists the person's issues and none is under way, **nothing is inferred** and the day
  reads "no active work to infer from".
- If there are no active issues and no facts, nothing is inferred.
- Otherwise an `INFERRED` status is recorded, with the blocker string `"no confirmed reply"`.
  The summary (`_inferred_summary`, :4943) names:
  - up to three active issues, each marked "(blocked)" or "(no update for N days)";
  - the date of the last check-in reply and its blocker count;
  - the issues or people that risks are flagged on;
  - counts of commits and pull requests.
- When nothing is inferred, the day is `STALE` (it names the last status once) or `UNKNOWN`.

**What it produces.**
- One sentence per person per day: "No confirmed check-in after a nudge. Inferred from ..."
- **No per-task statement, no ETA, no progress, no blocker change.**
- The rollup gives `INFERRED` a fixed amber factor, "Status is inferred and needs confirmation."
  (`backend/core/application/rollup_service.py:448`).
- The scrum master's board shows "Inferred, not confirmed" (`backend/core/application/attention.py:283`).

**Per-member coverage.**
- The fan-out asks every developer node with no status or check-in for the day
  (`developers_without_checkin`, `backend/infra/workflows/checkin_fanout.py:103`).
- It skips only a weekday outside the person's check-in days (`daily_checkin.py:96`).
- Nobody is inferred when:
  - the send was switched off or failed;
  - a close-out never ran;
  - the person replied without a status. PR #24's "nothing from me" is recorded `UNKNOWN`,
    "Replied without a status update" (`status_collector.py:3524`), with no Jira read.
- A member with no Jira link gets facts only.

**What Jira data exists to use.**
- The sync reads these fields: `summary, status, assignee, updated, project, issuetype, parent,
  duedate, fixVersions, labels, priority, created, resolutiondate`, plus story points
  (`jira_adapter.py:32`).
- It does **not** read the changelog, the reporter or creator, components or issue links.
- Each sync appends one `issue` fact per distinct `updated` value, carrying key, title, state,
  project key, assignee, status name, type and points (`sync_services.py:315, 621`). The result
  is a status timeline **sampled hourly, with no author**.

### 1.2 Is request 1 already covered?

**Partly. The mechanism is in the right place, but it does not "use the changes".** It reads
what is open now, not what moved. A person who closed their last ticket yesterday reads "no
active work". Someone who moved three issues to review reads "3 active issues". Neither result
says anything happened. And "each user configured in pod/project" is not met for people who
were not asked or who replied without a status.

### 1.3 The gaps, for "capturing the data completely"

1. **No changes.** Transitions, resolutions, due date, estimate, fix version and assignee changes
   are not read. Who made a change is unknown.
2. **Done work is invisible**, because the query is `statusCategory != Done`.
3. **To Do-only people are not inferred**, even when they created or groomed issues that day.
4. **Whole person only.** Nothing lands on the task rows that scrum masters read ("Last said").
5. **Only at close-out of a sent check-in.** People who were not asked, failed sends, and replies
   without a status are left out.
6. **Ten facts, counts only.** Git activity is counted and never tied to the task it moved.
7. **Coverage is invisible.** Nobody sees "2 people in this pod have no Jira link, so their
   silence can't be inferred".

### 1.4 Design

#### 1.4.1 Read Jira's changes (lane A, adapter and sync; no migration)

- **New port method.** `IssueTracker.list_issue_changes(tenant_id, key, since) -> list[IssueChange]`:
  - Cloud: the issue changelog endpoint, paged.
  - Data Center: the issue read with `expand=changelog`.
  - It is called only for the issues the incremental sync just returned, capped per tick
    (default 200, the rest next tick) on the existing `openprogram_sync` queue.
- **`IssueChange(history_id, author: UserRef, at, field, from_value, to_value)`.** Only these
  fields are kept: status, resolution, assignee, duedate, fixVersion, labels, story points (the
  configured field), sprint, issuetype, parent / epic, priority, components, and the configured
  severity field. For summary and description, only the fact that they changed is kept, with no
  values. Comments are not in the changelog and are not read.
- **New fact source `issue_change`** on the task entity, appended with `append_fact_once`:
  - correlation id: `issue_change:{tenant}:{key}:{history_id}:{field}`;
  - payload: key, field, from, to, `author_member_id` (through `developers_by_tracker_account`)
    or `unlinked_author_id`, and `project_key`.
- **More fields in the regular read:** `reporter`, `creator` and `components` go into issue
  metadata. The issue fact payload also gains `due_date`, `fix_versions` and `labels`, so their
  history is kept from now on.
- **Configurable severity field.** An optional "severity field" sits beside the story-points
  field in the Jira connection's `settings` JSONB (`integration_connections`, `0034`): picked
  from the connection test's field list, read into metadata as `severity`.
- **First read backfill.** One pass over the changelog of the synced projects' issues updated in
  the last 120 days, so attribution and hygiene have history from day one.

#### 1.4.2 The day's digest (pure domain)

`core/domain/observed_day.py`:
`digest(member_id, since, until, issue_changes, issue_facts, vcs_facts, task_updates,
assigned_keys) -> ObservedDay`.

- **`since`** is the person's last confirmed or partial answer, or the previous working day's
  close, whichever is later.
- **Per task (key)**, the events in order:
  - moved from one stage to another;
  - resolved, reopened;
  - due date moved from X to Y;
  - re-estimated from X to Y;
  - assigned to them, or away from them;
  - added to or removed from a release;
  - merge request opened, merged or reviewed;
  - commits naming it.
  The stage comes from the tenant's stage mapping, so a status moving from "In Review" to
  "QA" reads "In development → In testing".
- **Changes by the person on issues they don't own** (created a bug, groomed a story) are listed
  as "worked on".
- **Quiet active tasks**: a task under way with no event for N days (default 5) reads "no
  movement for N days".
- **Nothing in it is free text.** Keys, titles, stage names, dates and numbers only. Titles are
  already shown everywhere.

#### 1.4.3 Close-out v2

`_fallback_inference` reads the digest instead of `list_active_for` alone, with three changes:
- **Done and moved work counts.** A person whose only issue moved to Done is inferred "CHK-4 moved
  to Done (production)", not "no active work".
- **No active issue is required.** Any event since `since` is evidence.
- **The live read is widened** to `assignee = X AND (statusCategory != Done OR updated >= <since>)`,
  so work in Jira projects that are not synced is still seen. The changelog for those issues is
  read live too, for this person only.

The summary starts the same way: "No reply after a nudge. From Jira and GitLab:". Then:
"CHK-4 moved to In Review, merge request !12 opened; CHK-7 resolved; IDP-3 due date moved 10 Oct
→ 14 Oct. No movement on CHK-9 for 8 days."

`INFERRED` stays amber. The rollup factor names the evidence: "Inferred from Jira: 2 tasks
moved, 1 without movement for 8 days."

#### 1.4.4 Fill the day for everyone configured

- **A new pass, `fill_day`, at each member's reconcile cutoff** (the existing `*/15` reconcile
  tick decides who is past it). It fills every member who:
  - has today as a check-in day;
  - is not on leave (`AvailabilityService`, the check the nudge already makes);
  - has no status for the day;
  - and has no open check-in.

  The person gets an `INFERRED` status from the digest, or `STALE` / `UNKNOWN` as today. The
  summary starts "Not asked today. From Jira:", or "Not reached in chat." when the send failed.
  Idempotent per (member, day).
- **Replies without a status** keep their own first words, "Replied without a status update.",
  and add "From Jira: ...". The source becomes `INFERRED` when the digest has events, and stays
  `UNKNOWN` otherwise.
- **Tenant switch** "Fill the day from Jira for people who were not asked" (Admin › Check-ins). On
  by default; with it off, those people stay missing, as today.

#### 1.4.5 Per task: what Jira says beside what the person said

- The pod's tasks and the developer's own tasks (`PersonaViewService.pod_tasks`, `TaskStatement`)
  gain an `observed` line: "Jira today: In Review since 14:02".
- When a different person made the change, it is named: "moved by Liam". That is attached to the
  work item.
- It is never merged into "Last said". Where the two disagree, the existing drift kinds already
  flag it (`said_done_no_pr`, `merged_issue_open`).
- A partial reply leaves the tasks it did not mention with their Jira line. That is the
  "complete" picture without overriding anyone.

#### 1.4.6 Coverage

- Each pod's check-in line on the scrum master's Today gains the reasons:
  "6 replied · 3 from Jira · 1 no data (no Jira link)".
- "No data" names its cause: no Jira link, no chat id, or Jira not synced. The cause links to
  Admin › Directory, which gains a count of "members with no Jira account" beside the existing
  chat id count.
- No new reader. The same people who read pod check-ins today.

#### 1.4.7 Trust and privacy

- The digest is about tasks. No event counts per person, no "most active", no comparison across
  people, no new reader. Executives still see no pod check-ins.
- Retention follows `facts`. A fact carries only keys, fields, dates and member ids.

### 1.5 Data and migrations

- **None.** Changes are `facts` rows (source `issue_change`). The new fields sit in task metadata
  and issue fact payloads (JSONB). The severity field goes in the connection's `settings` JSONB.
  The fill switch is a tenant setting, stored where the escalation step switches are.
- Volume: one fact per changed field per change, on the order of tens per project per day.

### 1.6 UI (frontend-v3)

- **Today (scrum master):** the check-in panel line with reasons. Inferred people get one line
  of evidence each (`features/today`).
- **Pod tasks and Your tasks:** the "Jira today" line under "Last said"
  (`features/delivery/PodTasks.tsx`, `features/today/TaskList.tsx`).
- **Developer's own check-in card:** "Inferred from Jira: ..." with Confirm and Correct, which
  already exist. Confirming an inferred day records what was confirmed (features 4.9).
- **Admin › Check-ins:** the fill switch. **Admin › Directory:** the "no Jira account" count.

### 1.7 Ora

No new tool. `pod_checkins` adds `evidence` (the digest lines) to each inferred member, under the
pod check-in read it already needs. `ASK_TOOL_GUIDANCE` gets one line: "an inferred day says what
moved in Jira; it is not the person's word".

### 1.8 MVP (about 1.5 days, after lane A)

- **Lane A:** the changelog read, `issue_change` facts, reporter, creator and components, and the
  120-day backfill.
- **Backend:**
  - the digest;
  - close-out v2 (Done and moved work, no active-issue requirement);
  - replies without a status;
  - the `fill_day` pass and its switch.
- **UI:**
  - the "Jira today" task line;
  - the coverage line;
  - `pod_checkins.evidence`.
- **Later (P2):** the live widened read for unsynced projects, the severity field, and a quiet
  task's "no movement" turning into a scrum master ask.

---

## 2. Request 2: profiles, set when someone is added to a pod

### 2.1 Today

- **Members** are graph nodes of kind `developer`, with free-form metadata (`core/domain/graph.py`).
  Identity lives in `identity_links` (chat id, Jira account id or email, VCS user name).
- **App roles** (dev, po, sm, mgr, exec, admin) live in two places that are never synced:
  - Sign-in claims, for authorization.
  - A free-text `app_roles` string on the member node, read by `member_roles`
    (`checkin_drift.py:68`) for three things:
    - the day report's product owners, scrum masters and managers (`_role_holders`,
      `day_report_builder.py:811`);
    - the readiness decider;
    - the "non-code" check-in rules (`NON_CODE_REVIEW_ROLES`).
- **Pod membership** is a `CONTAINS` edge from pod to member, dated, with `metadata.role`.
  - The role is required and free text. The UI offers developer, scrum master, product owner and
    manager (`frontend-v3/src/features/admin/structure.ts:128`), suggested from `app_roles`.
  - Its only reader orders the escalation contact pickers (`config_service.py:725`).
  - There is no way to change it in place: re-adding conflicts.
- **Directory import** copies email, handle and avatar, but not the directory `title`, which is
  synced into `directory_users.title`. It never places anyone in a pod.
- **Jira** reads no groups, project roles or components.
- **No "profile", "discipline" or "skill" concept exists** anywhere in the backend or frontend.

### 2.2 The gap

Routing can't tell a QA engineer from a developer, or a security engineer from a product owner:
- A readiness criterion's missing security review can only go to the product owner.
- A tester's "in review" is checked for a merge request.
- Nobody can ask "who is the security contact for Checkout?".

### 2.3 Design: the profile is part of pod membership

**Catalog.** Generic defaults ship in code (`core/domain/profiles.py`, `default_profiles()`). A
tenant may rename, hide or add entries as data.

| Slug | Label | `code_work` | Suggested by (title words; Jira; Git) |
|---|---|---|---|
| `development` | Development | yes | "engineer", "developer"; Story/Task in development; commits and merge requests |
| `qa` | QA and test | no | "QA", "test", "quality"; Bug, Test types; issues in the testing stage |
| `security` | Security | no | "security", "appsec"; labels or components with "security", "vulnerability" |
| `product` | Product | no | "product", "business analyst", "PO"; reporter of Story and Epic; acceptance items kept |
| `design` | UX and design | no | "design", "UX"; label or component "design" |
| `delivery` | TPM / delivery | no | "TPM", "program manager", "scrum master", "delivery" |
| `ops` | Ops / SRE / platform | yes | "SRE", "devops", "platform", "ops"; Incident, Change types |
| `data` | Data and analytics | yes | "data", "analyst", "ML" |
| `architecture` | Architecture | no | "architect" |
| `management` | Engineering management | no | "manager", "head of", "lead" |
| `other` | Other | no | — |

`code_work` says whether merge requests are expected from that person in that pod. It replaces
the person-wide `NON_CODE_REVIEW_ROLES` guess, per pod.

**Data.** One dated row per pod membership: `(pod, member, profile, valid_from, source, set_by,
set_at)`. The source is one of `backfill`, `admin`, `import` or `suggestion_accepted`.
- **Current profile:** the latest row with `valid_from <= day`.
- **Changing a profile** adds a row from today. Earlier days keep the old one, matching how links
  hold (features 4.2). The edge itself is untouched.
- **Default when nothing is set:** taken from the pod role. developer becomes development,
  product_owner becomes product, scrum_master becomes delivery, manager becomes management, and
  any other value becomes development. It is marked "from the pod role" so it reads as unchecked.

**Who sets it.**
- **The admin** (`MANAGE_CONFIG`):
  - in Admin › Links › People in a pod, when adding a person: "Their role in Checkout" (as
    today) plus "Their profile in Checkout", preselected from the suggestion;
  - on an existing person's chip, through a "Change profile" action;
  - in Admin › Directory import, which gains an optional "Add to pod", with role and profile, for
    the people being imported.
- **A scrum master for the pods they run** (`runs_pod`): P2, if the user agrees (open
  question 3).
- **The member** sees their own profiles, read-only, under "Your check-in schedule" in the avatar
  menu ("In Checkout: QA and test").

**Suggestion (never automatic).** `suggest_profile(member, pod)` returns the slug and the reasons
in words ("title says 'QA engineer'; most of their issues in this pod are Bug and Test"). It reads:
1. the directory title's words against the catalog's title words;
2. over 90 days, the issue types, labels and components of the person's issues in the pod's
   remit, and changes they authored (from the `issue_change` facts, lane A);
3. Git: any commits or merge requests at all points to code work.

The reasons are computed on demand, shown only to whoever sets the profile, and never stored. Jira
groups and project roles, mapped by the tenant ("group security-team means security"), are P2:
they need an adapter read.

**Backfill.** Migration `0041` writes one `backfill` row for every pod membership in force,
derived from its pod role. Admin › Links then shows "N profiles set from the pod role: review"
with the suggestions side by side, and **Accept all suggestions** applies them in one go with
an audit entry per person.

**Where the profile is used** (always read per pod; the person's profile in *that* pod):

| Use | Today | With profiles | Phase |
|---|---|---|---|
| Check-in questions; drift `said_in_review_no_mr` | `app_roles` (person-wide) | the profile's `code_work` in the issue's pod, `app_roles` as fallback | MVP |
| Readiness criterion | a decision ask to the decider only | `owner_profile` on a criterion adds a **fix** ask ("create or link the security review") to that profile's member in the scope's pods. The decision ask is unchanged | MVP |
| Hygiene rule owner (section 4) | — | `owner: profile:<slug>` | MVP |
| Escalation matrix | team scrum master, team manager, named member | a new level source, **the team's `<profile>`** (for example review asks go to the pod's QA first). Several holders: the one marked lead, else the first by name | P2 |
| Day report asks | "Mina" | "Mina (Product, Checkout)". A coverage line: "No QA profile in any pod of Release 1", which attaches to the scope | MVP (names), P2 (coverage) |
| Gate sign-off | sign-in roles | unchanged (D8). The ask names the likely signer: acceptance goes to product, test cases to QA | MVP |
| Cross-person request "need QA to look at X" | needs resolution (no name) | resolves to the pod's member with that profile when exactly one holds it | P2 |
| The observed day (section 1) | — | Git is not expected from a profile without `code_work`; a tester's Test and Bug moves count | MVP |
| Ora | — | `team_profiles`; people named with their profile | MVP |

### 2.4 Data and migrations: `0041_pod_member_profiles` (additive)

```text
profile_catalog          tenant overrides of the shipped defaults (no rows = defaults)
  tenant_id text, slug text, label text, code_work bool, title_words jsonb,
  sort int, hidden bool, updated_by text, updated_at timestamptz
  PRIMARY KEY (tenant_id, slug)

pod_member_profiles      the profile per pod membership, dated
  tenant_id text, pod_id text, member_id text, profile text,
  valid_from date, source text CHECK (source IN ('backfill','admin','import','suggestion_accepted')),
  is_lead bool DEFAULT false, set_by text, set_at timestamptz
  PRIMARY KEY (tenant_id, pod_id, member_id, valid_from)
```

- **Data step.** Insert a `backfill` row for each `graph_edges` row with `kind = 'contains'`, a
  pod as `from_node_id`, a developer as `to_node_id`, and `valid_to` null or in the future. The
  profile comes from the role mapping, and `valid_from` is the edge's own (or `1970-01-01` when
  it has none).
- **Downgrade** drops both tables. It touches no existing table and no AGE data.
- In memory mode, a new repository with the same port.

### 2.5 UI

- **Admin › Links:** the add-person dialog with two pickers, profile chips on people ("Ana · QA"),
  "Change profile", and the review list.
- **Admin › Directory:** the optional pod, role and profile on import.
- **Admin › Profiles** (P2): rename, hide or add catalog entries.
- **Overall** (P2): a "Who covers what" row per scope, listing the profiles present in its pods.

### 2.6 Ora

**`team_profiles`.** Parameters: `pod_id` or `project_id`, and optionally `profile`. It returns
each pod's members with their role in the pod and their profile, plus, for a project, any profile
that its readiness criteria or hygiene rules need and no pod holds.
- Gate: Ask's `may_ask`. Names are directory data, which every role reads.
- Guidance line: "who is the security, QA or product person for a pod or project → `team_profiles`".

### 2.7 MVP (about 1 to 1.5 days)

- **Data:** migration `0041`, the defaults, the repository, and `GET/PUT
  /config/pods/{pod}/members/{member}/profile`. The add-member and import routes take `profile`.
- **Logic:**
  - the suggestion (title, types, Git);
  - the review list with Accept all;
  - drift's `code_work` per pod;
  - the readiness `owner_profile` fix ask;
  - profiles in day report names;
  - `team_profiles`.
- **Later (P2):** the matrix level source, catalog editing, scrum masters setting profiles, Jira
  groups, profile mentions in requests.

---

## 3. Request 3: why the date moved

### 3.1 Today

- **The forecast** (`core/domain/forecast.py`) is a 2000-run bootstrap. Each simulated working day
  draws one past day's completions, and the forecast is the day the remaining work runs out.
  - Remaining work: open requirements, or points when every requirement has them.
  - Samples: requirements (or points) that reached production on each working day of a 30-day
    window, needing at least 10 working days.
  - Seed: `"{scope}:{day}"`. It is deterministic.
  - Verdict: on track if p85 is on or before the target, at risk if p50 is, otherwise off track.
- **Team dates:** each open requirement's check-in ETA (from `eta_stated` facts) or its Jira due
  date. The latest wins.
- **Snapshots** (`requirement_snapshots`) keep, per project per day, each requirement's **stage
  and title only**. Taken hourly at :50; the last run of the day wins.
- **Forecast history** (`GET /projects/{id}/delivery/history`) is **recomputed** for each past day
  from the snapshots. It applies today's minimum, and the delivery view does not filter committed
  changes by day.
- **Committed date changes** are append-only (`delivery_date_changes`: date, who, when, note).
- **The day report's "what changed"** gives stage moves, "new, in Raised", "left the scope", a net
  "Scope +2 requirements" line, and date moves with who and why.
- **Overall's "How the date moved"** (`features/overall/SlipChart.tsx`) draws the committed date
  over the 50% to 85% band.
- **Nothing says why the band moved.** Nothing knows when an item joined the scope, who added it,
  whether it was planned, or what throughput did.

### 3.2 The gap

What's missing:
- a planning baseline;
- per-item size, type and dates in snapshots;
- who or what brought an item into scope (the changelog, lane A);
- the forecast's inputs per day;
- a decomposition of a change;
- a home for it on Overall, in the day report and in Ora.

### 3.3 The planning baseline (shared with section 4)

- **Who sets it, and how.** "Planning complete" on Overall, for a project or a release, by the
  product owner, a manager or an admin. That is `SET_PROJECT_DATES`, the same people who commit a
  date. The baseline freezes the current requirement set: each key with its stage, points, due
  date and type.
- **By date instead.** "Planning was complete on 1 Oct" freezes the stored snapshot of that day.
  Before snapshots existed, it falls back to the issues in scope today with a Jira `created_at`
  on or before that date, flagged "reconstructed".
- **Extra fields:** a change label (default from hygiene settings, for example `CR`), a grace
  period in working days (default 2) for issues entered late, and a note.
- **Re-baselining** adds a new active baseline (for example after a change is approved) and keeps
  the old ones. Overall shows "Baseline 2, set 14 Oct by Mina: +3 since. Since the first
  baseline: +7".
- **Pods** use their project's baseline, restricted to their keys, as the forecast already does.

### 3.4 The decomposition (pure, `core/domain/impact.py`)

Take two consecutive snapshot days, `a` and `b`. Use `F(R, W, start, seed)` for the history
forecast of remaining work `R` with window samples `W`. Partition the keys:
- **added:** in scope at `b`, not at `a`, still open;
- **removed:** open at `a`, gone at `b` (won't do, moved out of the release, type changed);
- **finished:** open at `a`, in production at `b`;
- **reopened:** in production at `a`, open at `b`;
- **re-estimated:** open at both, with different points (points mode only).

Every step below is replayed with **`b`'s seed** (common random numbers), in this fixed order:

| Step | Replay | Driver |
|---|---|---|
| c0 | `F(R_a, W_a, a, seed_a)` (stored) | the date on day `a` |
| c0' | `F(R_a, W_a, a, seed_b)` | **rounding** (seed change; shown only when not zero) |
| c1 | `F(R_a, W_a, b, seed_b)` | **time passed** (working days between `a` and `b`) |
| c2 | c1 with `R − finished` | **work finished** |
| c3 | c2 with `+ added` | **scope added**, split per item: equal shares by count, or by points |
| c4 | c3 with `− removed` | **scope removed** |
| c5 | c4 with `+ reopened` | **reopened** |
| c6 | c5 with re-estimates | **re-estimated** |
| c7 | `F(R_b, W_b, b, seed_b)` (stored) | **throughput**: the new day's sample in, the oldest out |

- c7 is the real forecast on `b`, so the steps sum exactly to the real move.
- Leaders read "time passed" and "work finished" together as **pace**: "+1 day: nothing finished
  yesterday", or "−2 days: 3 finished, more than usual".
- For a period ("since the baseline", "last 7 days"), the daily rows are summed. Per-item scope
  shares sum across days too.
- **The scope-added items are grouped** using the baseline and the changelog:
  - **change requests:** carry the change label;
  - **added without the change label**, after the baseline plus grace (each also a hygiene
    finding, section 4);
  - **bugs:** type Bug, where bugs count as requirements;
  - **moved in:** added to the release from another one (a fix version change).

  Each item carries its key, title, type, the day it joined the scope (changelog or first
  snapshot), and who brought it in (D14).
- **Throughput** is explained in words: "The 30-day window lost a 3-requirement day (21 Sep);
  yesterday finished 0. The average went from 0.6 to 0.5 a day."
- **Cost:** seven replays per scope per hourly tick. With 2000 runs and up to about 100 steps,
  that's around 1 s per scope in Python, on the sync queue. Today's row is provisional until the
  day's last tick.

### 3.5 Evidence, not shares (D12)

Listed under the waterfall, linked to the pace driver:
- **Requirements closing late:** open past their due date, their ETA, or (with a baseline) their
  baseline due date. Each with the days late and its stage: "CHK-4, due 2 Oct, 6 working days
  late, In testing."
- **Blocked days:** blocker-days on the scope's requirements in the period (`developer_blockers`
  with a work item in scope), with what each waited on.
- **Bugs opened in the scope** that are not counted as requirements (from issue facts of type
  Bug).
- **Requirements without a date** (already a forecast reason), now with their keys.

### 3.6 The other two dates

- **Committed date lane.** Each move with who, when and why, from `delivery_date_changes`. When a
  move falls within 2 working days after scope was added, it is shown beside that addition: "15 Dec
  → 22 Dec (Priya, 8 Oct: 'two changes approved'), after CHK-20 and CHK-21 were added".
- **Team's date lane.** When there is no history forecast (fewer than the minimum days), this lane
  is the main answer. It shows the latest open requirement's date, before and after, with the item
  and cause:
  - "CHK-12's ETA moved 14 → 21 Oct (stated 9 Oct)";
  - "CHK-20 added with a due date of 30 Oct";
  - "CHK-3 finished, so CHK-9 (due 18 Oct) is now the latest".

  ETA history is already kept; due date history starts with lane A.

### 3.7 Storage: `forecast_days` and the baselines (migration `0042`)

```text
planning_baselines
  id text PK, tenant_id text, scope_kind text CHECK (IN ('project','release')),
  scope_id text, project_id text, kind text CHECK (IN ('snapshot','date')),
  planned_on date, payload jsonb  -- {keys: {KEY: {stage, points, due, type}}, reconstructed}
  change_label text, grace_working_days int DEFAULT 2,
  set_by text, set_at timestamptz, note text, superseded_at timestamptz NULL
  INDEX (tenant_id, scope_kind, scope_id, set_at)

forecast_days
  tenant_id text, scope_kind text, scope_id text, project_id text, day date,
  p50 date, p85 date, verdict text, target_date date, team_latest date, team_latest_key text,
  remaining numeric, unit text, sample_days int, completed_in_sample numeric,
  min_sample_days int, attribution jsonb, reconstructed bool DEFAULT false, computed_at timestamptz
  PRIMARY KEY (tenant_id, scope_kind, scope_id, day)
```

- **No data step.** After deploy, a one-off job (`reconstruct_forecast_days`, admin dispatch)
  rebuilds the last 90 days from the stored snapshots, the way the history read does today. It
  filters committed changes by day, which fixes the gap in 3.1, and marks the rows
  `reconstructed`.
- **Downgrade** drops both tables.
- **Snapshot payload extension (no migration).** `requirement_snapshots.payload` gains an `item`
  map, `{KEY: {points, type, due}}`, written from now on. Older days read their points from the
  issue facts' `story_points` timeline.
- The existing history endpoint then reads `forecast_days` first.

### 3.8 UI

**Overall › Timeline › "Why the date moved".** This is the Timeline section's second picture
(section 5). It explains the row selected on the timeline (the project's by default) over the
period the chips pick. It is shown to the roles that read that row's forecast: product owner,
manager, executive (read-only) and admin, and a scrum master for the pods they run. It is built
in lane J with the timeline, from lane E's data.
- **Period chips:** Since planning · Last 7 days · Last 30 days · Since the date last changed.
  Before a baseline exists, the first chip is "Mark planning complete".
- **A horizontal waterfall, in working days,** from the 85% date then to the 85% date now, with
  the calendar dates at both ends. One bar per driver, worst first. Scope added is split by
  group, and a bar opens its items.
- A table view (like the stage timeline's), a "50% date" toggle, the two date lanes, and the
  evidence list.
- **One answer line:** "The 85% date moved 9 working days later since planning: +6 from 4
  requirements added (2 without a change label), +3 from slower finishing; the committed date
  moved once (+5 days, Priya, 'two changes approved')."
- **No forecast yet:** "No forecast yet: 4 of 10 working days of history". The scope ledger and
  the team's date lane are still shown.

**Day report.**
- "Where we stand › What changed and why" gains one line when the 85% date moved since the
  previous report: "The 85% date moved 3 working days later: +2 scope added (CHK-20, no change
  label), +1 nothing finished."
- "Most important" leads with it when the verdict worsened, or when the move is at least 3
  working days. It counts toward the existing cap of 8 lines.

**Delivery project panel:** the answer line and the compact timeline (section 5), linking to
Overall's Timeline with the row selected.

### 3.9 Ora: `delivery_impact`

```text
delivery_impact(project_id | pod_id, release_id?, period: since_baseline | last_7_days |
                last_30_days | since_date_change | {from: YYYY-MM-DD}, measure: p85 | p50)
-> {scope, from: {day, p50, p85, committed}, to: {...}, moved_working_days,
    drivers: [{kind, working_days, items: [{key, title, type, joined_on, change_label,
               group}] (max 10), note}],
    evidence: {late: [...], blocked: [...], bugs_opened: n, undated: [keys]},
    committed_changes: [{from, to, on, by, note}], team_date: {from, to, key, cause},
    markers: [{day, kind, count, keys}],   # the timeline's own markers, same visibility
    link: "/reports/<project>/overall?row=<kind>:<id>#timeline",
    reconstructed_days: n, not_known: [..]}
```

- **Gate:** the delivery forecast's own rule (`DeliveryScopeService.ensure_project_read`,
  `ensure_pod_dates`, `readable_projects`), as `delivery_forecast` uses (`ask_service.py:1255`).
  A refusal is returned as `{"error"}`. A developer gets no tools.
- **Names:** `by` on committed changes is kept, since dates are already shown with who. An item's
  "added by" is left out for executives (D14).
- **Prompt updates:**
  - `ASK_TOOL_GUIDANCE`: "why a date moved, what changed or impacted the timeline → delivery_impact;
    never explain a move from colours or briefs".
  - Investigate's `STEP_GUIDANCE`: a "why did it move" question gets one `delivery_impact` step
    per scope, then a blockers step when pace leads.
  - `_UNREAD_NEGATIVES` and `_READ_WORDS` get their entries.

### 3.10 MVP (lane E about 1.5 days, after lane C, using lane A; its Overall UI is in lane J)

- **Engine and data:**
  - migration `0042`;
  - "Planning complete" (snapshot or date);
  - the snapshot `item` map;
  - the decomposition with count and points;
  - `forecast_days` written at :50;
  - the 90-day reconstruction.
- **Explanation:**
  - grouping of added scope (change label, no label, bug, moved in);
  - the two date lanes;
  - evidence: late requirements and undated keys.
- **Surfaces:**
  - the day report line;
  - `delivery_impact` and its guidance.
- **In lane J (section 5), not lane E:** the Overall waterfall with its chips, table and answer
  line. That way the timeline and the waterfall are designed once, as one section.
- **Later (P2):** blocked days and uncounted bugs as evidence, and the weekly steering pack
  (idea 4). **P3:** Shapley values.

---

## 4. Request 4: the Jira hygiene scan

### 4.1 Today

- **Release readiness** (`core/domain/release_readiness.py`, `release_readiness_service.py`)
  answers one question per scope: does any issue match? It provides:
  - scope issue sets (`owned_project_tasks`, `Release.includes`, `pod_tasks`);
  - matchers (label, type, title phrase, title words, epic), with a pure `match(matcher, issue)`;
  - a staleness gate on the Jira sync;
  - fingerprints, so an unchanged run writes nothing;
  - a run slot claim, the audit, `ACT_ON_READINESS`, and the Admin tab with examples and "Try on a
    project";
  - report gaps that lead "Most important", plus decision asks.
- **Gates** read acceptance criteria from descriptions (`gate_extraction.py`). The day report
  already asks "no acceptance criteria yet" when a requirement sits just before the gate
  (`day_report_asks._kind_asks`).
- **Risks** have a stale rule, but only for work-item nodes (`risk_service.py:454`), not Jira
  tasks.
- **There is no planning baseline and no per-issue rule engine.**

### 4.2 The gap

Missing:
- per-issue predicates (missing field, missing label after the baseline, stale in status);
- a per-issue findings store (`readiness_findings` is unique per criterion and scope);
- owners per issue;
- suggested fixes;
- asks per owner.

### 4.3 The rule model (`core/domain/hygiene.py`, pure)

```text
HygieneRule
  rule_id        slug, unique per tenant ("cr-label-after-planning")
  version        +1 on every save
  name           "Added after planning without a change label"
  scope          project | release | pod                (which issue set: readiness's builders)
  select         0-8 readiness matchers (label, issue_type, title_phrase, epic), all must match
                 + stage filter: any | before <stage> | at or past <stage>
                 + counted requirements only (default) | every issue type
  condition      one of:
    added_after_baseline_without_label  {labels: [..]}           (needs a baseline)
    missing_label                       {any_of: [..]}
    missing_field                       {field: story_points | due_date | parent | fix_version |
                                          priority | assignee | components | severity}
    missing_gate_kind                   {kind: acceptance | test_cases | ...}  (gates' scan)
    stale_in_status                     {stage | status_category, working_days}
    past_due_open                       {grace_working_days}
    too_big                             {points_over}
  severity       list_only | ask                         (default list_only)
  need           fix | decision                          (the ask kind)
  owner          assignee | reporter | profile:<slug> | decision_owner | pod_scrum_master
                 (falls back along: the named one, else the pod's scrum master, else the decider)
  blocking_before   optional stage: a breach is blocking once the issue reaches it (readiness tie)
  suggested_fix  template; placeholders only from a whitelist: {key} {title} {stage} {release}
                 {label} {field} {days} {baseline_date}
  enabled        on | off
```

- **`evaluate_rule(rule, issue, context) -> Breach | None`** is a pure function. The context holds
  the baseline, the stage placement, the gate items, the changelog-derived "joined scope on" and
  "last changed on", and the dates.
- **Per-issue data.** `ScopeIssue` gains `created_at`, `due_date`, `story_points`, `priority`,
  `fix_versions`, `components`, `severity`, `assignee_id` and `reporter_id`
  (`release_readiness_service._scope_issue`). Readiness itself ignores them.
- **The acceptance criteria rule** reads the gates' scan: the issue was scanned
  (`issue_scans.last_scanned`), and no acceptance item is either suggested or confirmed. When the
  gate's own ask already covers the issue, the finding is listed but asks nothing, so there is
  one ask per issue.
- **Stale In Progress** reads the last `issue_change` (or `issue` fact) that changed the issue, and
  the snapshot's `in_stage_since`. It is not the risk rule.

### 4.4 Default examples (none on until an admin adds one)

| Example | Condition | Owner | Need | Suggested fix |
|---|---|---|---|---|
| Added after planning without a change label | `added_after_baseline_without_label {labels: [CR]}` | decision owner | decision | "Label {key} {label} if it was agreed as a change, or take it out of {release}." |
| Story without an estimate | `missing_field story_points`, Story, before In development | pod scrum master | fix | "Estimate {key} before it starts." |
| Requirement without acceptance criteria | `missing_gate_kind acceptance`, before In development | `profile:product` | fix | "Write {key}'s acceptance criteria under an 'Acceptance criteria' heading." |
| Requirement without a due date in development | `missing_field due_date`, at or past In development | assignee | fix | "Give {key} a due date, or an ETA in your check-in." |
| Bug without a severity | `missing_field severity` (or priority), Bug | reporter, else `profile:qa` | fix | "Set {key}'s severity." |
| Orphan story or task | `missing_field parent`, Story or Task | `profile:product` | fix | "Link {key} to its epic." |
| Stale In Progress | `stale_in_status in_development, 5 working days` | assignee | fix | "{key} has had no Jira change for {days} working days in {stage}: move it on, or say what holds it." |
| Unassigned in development | `missing_field assignee`, at or past In development | pod scrum master | fix | "Give {key} an owner." |

The label value is tenant data. The repo's example says `CR`, a generic term.

### 4.5 The run

- **When.** In the readiness tick: hourly at :45 after the :00 Jira sync, through the same derived
  step. It has its own switch (`OPENPROGRAM_HYGIENE_SCAN_ENABLED`) and a tenant setting, and runs
  on demand with "Check now". Readiness off doesn't stop hygiene, or the other way round.
- **Writes only what changed.** Each finding has a fingerprint over the rule version, the key, the
  breach detail, the owner and the issue's `updated_at`.
- **Staleness gate (reused).** While the Jira sync is failing or behind:
  - a finding never closes;
  - a new `ask` finding waits for fresh data before the day report says it.
- **Closing.** A finding closes when the breach is gone. If the issue leaves the scope, the
  finding closes as "left the scope".

### 4.6 Findings, owners and actions

- **A finding** holds: rule, key, title, scope, stage, what is wrong (fixed words), owner, the
  suggested fix, first seen, and days open.
- **Actions** (`ACT_ON_READINESS`, reused): *Waive* with a reason, or *Not applicable* (only a
  manager or admin when the rule is blocking). *Re-check* after a fix in Jira.
- Every action, the scan's included, is audited.
- **Fix in Jira** is P3: add the change label or set the parent, behind a new Jira writes switch
  "Apply hygiene fixes" (off), only on a person's press.

### 4.7 The readiness tie

- A rule with `blocking_before` uses readiness's urgency for each issue. Reaching that stage with
  the breach open is `stage_reached`, and the finding then leads "Most important" as a blocking
  readiness gap does (sharing the 3-line readiness slot: "and N more in Jira hygiene").
- The Release readiness section shows one summary row per scope: "Jira hygiene: 7 open, 2 blocking
  before In testing".
- That is the whole tie. There is no hygiene criterion kind in readiness.

### 4.8 The escalation tie

- **`ask` rules:** each produces one `Ask` per (owner, rule, scope) through a new
  `hygiene_asks(...)`, added to `DayReportBuilder._asks` beside the readiness asks.
  - `need` comes from the rule.
  - The text lists up to five keys and "and N more": "Label 2 additions after planning as changes
    or take them out: CHK-20, CHK-21".
  - `waited_days` counts from the earliest finding's first day, or from the baseline plus grace
    for the change-label rule.
- `escalated()` then climbs the project's matrix unchanged. With profiles (P2), a matrix level can
  name "the team's QA".
- `list_only` rules never ask. They show on Overall and in a count line in the day report: "Jira
  hygiene: 7 open (2 new today)".

### 4.9 Data and migrations: `0043_jira_hygiene` (additive)

```text
hygiene_settings   tenant_id PK, enabled bool, change_labels jsonb, stale_working_days int,
                   updated_by, updated_at
hygiene_rules      tenant_id, rule_id, version, spec jsonb, enabled, deleted_at, updated_by,
                   updated_at; PK (tenant_id, rule_id)
hygiene_runs       id, tenant_id, slot, trigger, status, started_at, finished_at, counts jsonb;
                   UNIQUE (tenant_id, slot)
hygiene_findings   id, tenant_id, rule_id, rule_version, issue_key, scope_kind, scope_id,
                   project_id, state CHECK (IN ('open','resolved','waived','not_applicable')),
                   owner_member_id, detail jsonb, fingerprint, first_seen_at, last_seen_at,
                   resolved_at, resolution text
                   UNIQUE (tenant_id, rule_id, issue_key, scope_kind, scope_id) WHERE state = 'open'
hygiene_actions    append-only audit: id, tenant_id, finding_id NULL, rule_id, action, by, at, note
```

- The downgrade drops these five tables.
- `detail` holds field names, dates and keys only, never Jira text.
- The severity field sits on the Jira connection (section 1.4.1); it needs no migration.

### 4.10 UI

- **Overall › "Jira hygiene"** (product owner, manager, admin; executive read-only; a scrum master
  for their pods' projects; not developers).
  - One row per rule: the count, new today, and the oldest.
  - A rule opens its findings: key and title, what is wrong, owner, suggested fix, days open, and
    the actions.
  - A filter by pod or release.
  - The rule is stated first, as gates and readiness do.
- **Admin › Jira hygiene.** Settings (switch, change labels, stale days), the rule list, an editor,
  "From an example", and **Try on a project**, which previews counts before a rule is switched on.
  That last one is how a QA tenant's "33 of 33 without an estimate" gets seen before it reaches anyone.
- **Developer's Your tasks** (P2): a quiet hint on their own issue ("Jira: needs an estimate"),
  never a list of their breaches.

### 4.11 Ora: `jira_hygiene`

```text
jira_hygiene(project_id | pod_id, release_id?, rule_id?, state: open | all)
-> {rules: [{rule_id, name, open, new_today}], findings: [{key, title, rule, what, owner,
    owner_profile, suggested_fix, open_days, stage, blocking}] (max 20), held_for_stale_data}
```

- **Gate:** the readiness board's read rule (`readiness_access.project_viewer` and `pod_viewer`).
- **Guidance:** "what is missing or wrong in Jira, scope added without a change label → jira_hygiene".
- **Investigate:** "is Jira clean / scope creep" gets a `jira_hygiene` step and a
  `delivery_impact(since_baseline)` step.

### 4.12 MVP (about 2 days, after lanes A and C; uses B for profile owners)

- **Engine:**
  - migration `0043`;
  - the rule model and evaluation;
  - the examples above, off;
  - the run in the readiness tick with the staleness gate.
- **Surfaces:**
  - Overall section and Admin tab with Try on a project;
  - day report asks and count line;
  - matrix climbing;
  - `jira_hygiene`.
- **Profile owners** fall back to the pod's scrum master until lane B lands.
- **Later:** blocking-before-stage and the readiness summary row (P2), the developer hint (P2),
  Fix in Jira (P3).

---

## 5. Timeline (Gantt) per project and pod

The user's words: "project/pod wise timeline / gantt chart visualization will help (showing the
progress and what impacted timeline)".

### 5.1 Today

**Overall** (project or release, for forecast readers) draws dates three ways, all as inline SVG:
- **The answer line and its date strip:** committed date · forecast 50% to 85% · team's date.
- **"How the date moved"** (`features/overall/SlipChart.tsx`, `slip.ts`): the committed date as a
  step line over the forecast band as it stood each day, for the last 30 days.
  - It sits in an `overflow-x-auto` box.
  - Its `role="img"` label speaks the finding.
- **"Dates by pod"** (`features/overall/PodDatesSection.tsx`, `podDates.ts`):
  - each pod's and release's date as a line from today, against the project's date, in its
    verdict's colour;
  - dashed with a red chip when a row has no date;
  - a scrum master gets `ScrumMasterPodDates` instead.

**Delivery** panels:
- The project panel opens with `ProjectDateStrip`, then `DateHistory` (the three newest date
  changes).
- The pod panel opens with `PodDateStrips`.

**What none of them shows:**
- when the work started, against the plan;
- how much is done for the time used;
- which events moved a date, on the day they happened.

### 5.2 What it shows

**Rows.** The project row comes first, or the release row when `?release=` is set. Then the
project's releases (folded), then each pod, worst verdict first. A row runs from its planning
baseline (lane C) to its committed date.

| Part of a row | Drawn as | From |
|---|---|---|
| Span | a bar from the baseline day to the committed date. With no baseline it starts at the first snapshot, faded, labelled "no baseline". With no committed date it runs to the right edge with a dashed open end, labelled "no date" | `planning_baselines`, `delivery_date_changes` |
| Progress | a fill from the left: the scope's share in production, by points when every requirement has them, else by count. The label reads "8 of 22 · 36%" and scope since the baseline, "18 → 22" | `requirement_snapshots` (today's, live) |
| Forecast band | 50% to 85%, tinted in the verdict's colour. A band past the committed date overhangs the bar's end, so lateness is visible. Pods use their own forecast, as today | `forecast_days` |
| Team's date | a diamond at the latest open requirement's ETA or due date; hollow with "+N undated" when some have none | `forecast_days` (team_latest, key) |
| Committed date and its history | a solid tick at the current date, with a small hollow tick for each earlier one, joined by a thin arrow. "Moved twice, +12 days" reads from the row. The Jira release date appears as a dotted tick when it differs | `delivery_date_changes`, task metadata |
| Today | one vertical line across all rows | — |
| Impact lane | a thin lane under each bar, with markers on the day each thing happened (below) | lanes E, A, F |

**Impact markers.** Each has a shape, never colour alone. Each is tied to work items and is never
sorted or counted by person.

| Marker | Shape | From | Shown to |
|---|---|---|---|
| Scope added after the baseline, with the change label | filled up-triangle | lane E's daily attribution (`scope_added` items, group `change_request`), joined-on day from `issue_change` | the row's readers |
| Scope added without the change label | hollow up-triangle, ringed when a hygiene finding is open on it | the same, plus `hygiene_findings` (lane F, when merged) | the row's readers |
| Scope removed; reopened | down-triangle; loop | lane E | the row's readers |
| A requirement closing late (passed its due date or ETA while open) | flag | snapshots, plus due date history (lane A) and `eta_stated` facts | the row's readers |
| Bug opened in the scope | cross | `issue_change` and `issue` facts of type Bug | the row's readers |
| Blocker opened; closed | filled dot; hollow dot | `developer_blockers` (`first_seen_on`, `resolved_on`) with a work item in the row's scope | readers of that pod's blockers only |
| Committed date changed | the tick history above | `delivery_date_changes` | the row's readers |

- Markers of one kind on one day merge into one glyph with a count ("+3"). On an axis longer than
  12 weeks they merge per week.
- A marker opens a small list:
  - its work items (key, title, stage) and the day;
  - for scope added, whether each carries the change label, and who brought it in, by D14's rule;
  - for blockers, what each waited on.
- Selecting a row (by click, tap or keyboard) points the "Why the date moved" waterfall below it at
  that row (section 3.8). The waterfall's period chips also shade that period on the timeline's
  axis.

### 5.3 Where it shows, by the v3 rules

The v3 rules: hide what a role can't use; say each thing once.

**Overall: one "Timeline" section replaces two pictures (D20).**
- **"Dates by pod" is retired.** Its pods and releases against the project's date, in verdict
  colours and dashed when undated, are now timeline rows. `ScrumMasterPodDates` goes too.
- **"How the date moved" is retired as a section.** The committed date's history is the ticks on
  each row, and the band's movement is said by the waterfall ("85% date then → now"). The slip
  drawing is reused as the project row's **Band over time** detail, opened from the row, for
  anyone who wants the daily band.
- **The new order of Overall:**
  1. the answer line with its date strip;
  2. **Timeline** (the Gantt, then the waterfall for the selected row);
  3. requirements by stage;
  4. gates;
  5. readiness;
  6. Jira hygiene;
  7. risks;
  8. questions.

**Delivery project panel.** A compact timeline (project and pod rows, with markers but no
waterfall) under `ProjectDateStrip`. It replaces `DateHistory`: those three changes are now the
row's ticks, and the table lists them. "Why it moved" opens Overall's Timeline with the row
selected.

**Delivery pod panel.** The pod's own row under `PodDateStrips`, with its project's committed date
as a reference tick.

**Per role.** The server sets each row's level by the `NodeAccess` that `delivery_scope` already
computes: a whole row (`panel`), dates only (`dates`: span, ticks, band; no progress, no markers),
or no row (`name`). The console never asks for a row the person can't read.

| Role | Rows | Markers |
|---|---|---|
| Product owner | every row of the project, whole | all except blockers (no pod blocker read); names on scope items |
| Manager, admin | every row, whole | all; names on scope items |
| Executive | every row, read-only | no blocker markers; no names on scope items |
| Scrum master | the pods they run or belong to, whole; the project's other pods, dates only; no project row (no project forecast read) | all, on their own pods |
| Developer | Overall: unchanged (gates and questions; no forecast read). Scoped Delivery: **their own pod's row, whole**, on its pod panel; no project row, no other pods | their pod's, blockers included (their pod panel shows them); no names on scope items |

### 5.4 Data and API (no migration)

Two new reads: `GET /projects/{id}/timeline?release_id=&as_of=` and
`GET /pods/{id}/timeline?as_of=`.

```text
TimelineResponse
  axis:  {start, end, today}
  rows:  [{scope_kind, scope_id, name, access: panel | dates, verdict,
           baseline: {day, reconstructed} | null,
           committed: {date, history: [{date, on}]}, jira_release_date,
           forecast: {p50, p85} | null, forecast_reason,
           team: {date, key, undated}, progress: {done, total, unit} | null,
           scope: {at_baseline, now} | null,
           markers: [{day, kind, count, items: [{key, title, stage}],
                      change_label?, added_by?, waits_on?}]}]
  text:  [{scope_id, sentence}]     # the spoken summary per row, worded by the server
```

- **What it reads.** Stored rows only, with no simulation on read:
  - `forecast_days`: the band, verdict and team's date, plus each day's attribution items for
    the scope markers;
  - `planning_baselines`;
  - `requirement_snapshots`: progress, and late crossings;
  - `delivery_date_changes`;
  - `issue_change` and `issue` facts: bugs, due dates, joined-on days;
  - `developer_blockers`;
  - `hygiene_findings`, once lane F is in.
- **Who may read it:**
  - project rows: the rule of `GET /projects/{id}/delivery` (`ensure_project_read`);
  - pod rows: the rule of `GET /pods/{id}/delivery` (`ensure_pod_dates`), with markers only for
    `panel` access;
  - blocker markers: the pod blocker read;
  - `added_by`: D14.
- **A past day** reads that day's `forecast_days` row and the markers up to it, as every other
  read does.

### 5.5 Drawing

- **Tech.** Inline SVG, no chart library, the same way as `SlipChart.tsx` and the daily pictures:
  - colours from `features/overall/viz.tsx` and `vizStyles.ts` (`--op-green`, `--op-amber`,
    `--op-red`, greys);
  - a pure layout module `features/overall/timeline.ts`, tested with `node --test`;
  - a component `TimelineChart.tsx`.
- **Phone width:**
  - Row labels are HTML in a sticky left column.
  - The time axis is SVG in its own `overflow-x-auto` box, labelled "Timeline, scrolls sideways",
    at least 28 px per week.
  - Below 640 px the box opens scrolled to today and the committed dates, and labels shorten to
    the row's name and "36%". The page itself never scrolls sideways.
- **Input:**
  - rows are focusable, and the arrow keys move between markers;
  - a marker's list opens on hover, focus or tap;
  - every marker has an `aria-label` ("9 Oct: 2 requirements added without a change label").
- **Text alternative:**
  - The SVG is `role="img"`, with the server's sentence per row: "Checkout Revamp: 8 of 22 done;
    the 85% date, 2 Dec, is 10 working days after the committed 18 Nov; 4 requirements added
    since planning, 2 without a change label".
  - A **Table** toggle, as the stage timeline has, lists per row: start, the committed date and
    its moves, 50% and 85%, the team's date, done, and the events by date.
- **A past day** is drawn as of that day, under the banner that already says so.

### 5.6 Wireframe

```text
Timeline                               [Since planning] [7 days] [30 days] [Since date change]
Checkout Revamp · baseline 1 Oct · 18 → 22 requirements                              [Table]
┌───────────────┬────────────────────────────────────────────────────────── scrolls sideways ┐
│               │ 1 Oct      15 Oct      1 Nov      15 Nov   ┊today    1 Dec      15 Dec     │
│ Project       │ [█████████████░░░░░░░░░░░░░░░░░░┤░░░░░░░░░░░┊░░│▓▓▓▓▓▓▓▓▓▓▓▓]              │
│ 8/22 · 36%    │  ▲     △△  ✕   ⚑                ●          ┊  ◇│            at risk       │
│ at risk       │                       (was 4 Nov)┤──────────►┊ 18 Nov committed             │
│               │                                              ┊                             │
│ Payments Pod  │ [██████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░┊░░░░│▓▓▓▓]                   │
│ 5/9 · 56%     │       △2        ●  ○                         ┊ ◇  │        on track         │
│               │                                              ┊                             │
│ Identity Pod  │ [█████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░┊░░░░│░░░░░▓▓▓▓▓▓▓▓▓▓▓▓]      │
│ 2/8 · 25%     │   ⚑ ⚑          ✕✕            ●               ┊          ◇ +2 undated        │
│ off track     │                                              ┊                             │
│ Storefront    │ [· · · · · · · · · · · · · no date · · · · · · ┊ · · · · · · · · · · · · ·>  │
│ 1/5 · no date │                                              ┊                             │
└───────────────┴─────────────────────────────────────────────────────────────────────────────┘
 █ done  ░ to go  ▓ forecast 50%–85%  ◇ team's date  │ committed  ┤ earlier committed date
 ▲ added with change label  △ added without one  ▼ removed  ⚑ closing late  ✕ bug
 ● blocker opened  ○ blocker closed

 △ 9 Oct · 2 added without a change label           (the list a marker opens)
   CHK-20 Saved cards   Story · Raised   no change label (Jira hygiene)
   CHK-21 Card art      Story · Raised   no change label (Jira hygiene)

Why the 85% date moved · Project · since planning (1 Oct)         [85%] [50%]    [Table]
  scope added      ████████████  +6   4 requirements: 2 change requests, 2 without a label
  pace             ██████        +3   fewer finished than usual; CHK-4 and CHK-9 are late
  throughput     ██              −1   a slow day left the 30-day window
  ──────────────────────────────────
  moved            +8 working days    18 Nov → 2 Dec
  Committed date:  4 Nov → 18 Nov (9 Oct, "two changes approved")
```

### 5.7 Ora

- "Show me what moved Payments Pod's date" becomes
  `delivery_impact(pod_id=<Payments Pod>, period=since_baseline)` (section 3.9).
- The tool returns:
  - the drivers;
  - the timeline's own `markers`, with the same visibility;
  - a `link` to the row (`/reports/<project>/overall?row=pod:<id>#timeline`), which the console
    shows as a reference.
- Ora writes the drivers in words and does not draw. The gate is the same as in 3.9.
- Investigate's `STEP_GUIDANCE`: "show me / what moved X's timeline" is one `delivery_impact` step
  for X.

### 5.8 Lane J and its MVP

**Lane J, Timeline.**
- **When:** the first lane of wave 3, started as soon as lane E merges. Wave 2 already runs three
  lanes, and the markers need E's attribution rows.
- **Needs:** C (`forecast_days`, `planning_baselines`), E (the daily attribution) and A
  (`issue_change`: bugs, due dates, joined-on days). F is optional: the hygiene ring lights up
  once it is merged.
- **Takes from E:** the Overall waterfall, so that the section is designed once.
- **Size:** about 2 days, and no migration:
  - the two reads and their DTOs: 0.5 day;
  - layout and SVG: 1 day;
  - roles, table, phone width, the Delivery panels and retiring the three old pictures: 0.5 day.

**MVP:**
- Overall Timeline:
  - project, release and pod rows;
  - progress, band, team's date, committed ticks;
  - markers for scope added (with and without label), late, bugs, blockers and date changes;
  - the waterfall below it;
  - the table and the per-row sentences.
- The Delivery project panel's compact timeline, and the pod panel's single row.
- Retired: "Dates by pod", "How the date moved" and `DateHistory`, with Band over time as the row
  detail.
- `delivery_impact.markers` and `link`.

**Later (P2):** a portfolio timeline (one row per project) on the manager's Today, a release
comparison, and printing into the steering pack (idea 4).

---

## 6. More features, ranked by value to leadership against effort

Effort is S (under a day), M (1 to 2 days) or L (more), counted after the lanes above.

| # | Feature | What it shows | Data it needs | Value | Effort |
|---|---|---|---|---|---|
| 1 | **What-if on the forecast** | "Drop CHK-20 and CHK-21, or add 2 people's worth of throughput: the 85% date moves to 3 Nov" | the counterfactual engine from 3.4, snapshots | high | S |
| 2 | **Scope burn-up against the baseline** | done against total scope since planning, with the change-request and unplanned bands, per release | snapshots, baselines | high | S |
| 3 | **Ageing work by stage** | requirements older in their stage than the scope's usual (its 85th percentile), worst first | snapshots (`in_stage_since`) | high | S |
| 4 | **Weekly steering pack** | one page per project or program: answer line, the timeline and its waterfall, scope since planning, top asks, readiness and hygiene gaps; scheduled like day reports | sections 3 and 4, day report destinations | high | S–M |
| 5 | **Forecast calibration** | for each past milestone, the 85% date shown 2, 4 and 6 weeks ahead against the day it was actually done | `forecast_days` | high (trust) | S |
| 6 | **Dependency radar** | which pod waits on which, on what, for how long: blockers on other teams' issues, requests across pods, Jira "is blocked by" links | `developer_blockers`, `cross_person_requests`, issue links (adapter read) | high | M |
| 7 | **Unplanned work share** | per pod and release, the share of finished work not in the baseline (changes, bugs, unlabelled) | baselines, snapshots, issue type | medium-high | S |
| 8 | **Decision log** | decisions waiting on the decision owner or management, their age, and what each holds up (with what-if days) | decision asks, tracked questions, readiness decisions | high | M |
| 9 | **Release quality trend** | bugs opened against closed per release, bugs found after production, reopened requirements | issue facts (type, created), `issue_change` (resolution) | medium-high | M |
| 10 | **Commitment stability** | how often and how far each scope's committed date moved, and moves made with no scope change | `delivery_date_changes`, `forecast_days` | medium | S |
| 11 | **Delivery pipeline status** | last good deploy per environment and repository, failing pipelines on a release's work | a CI sync behind the existing `CiProvider` port | medium-high | L |
| 12 | **Capacity outlook** | working days available per pod in the forecast window, from leave and public holidays, as counts only | calendar availability (live), a holiday calendar | medium | M (works-council review first) |

**Top five:** 1, 2, 3, 4, 5. Ideas 1, 5 and 10 come almost free once `forecast_days` and the
engine exist.

---

## 7. Build order: lanes, at most three at a time

Each lane:
- runs on its own branch and worktree;
- runs migrations only in a copy of the database (`pg_dump openprogram | psql openprogram_<lane>`),
  never on the shared `openprogram`;
- regenerates the OpenAPI clients (`make openapi-check`);
- writes its `features.md` section and a short generic LLD.

**Migration numbers go by merge order.** Whichever lane merges first takes `0041`; the others
rebase their `down_revision` before merging.

| Wave | Lane | Contents | Needs | Migration | Size |
|---|---|---|---|---|---|
| 1 | **A. Jira read v2** | changelog → `issue_change` facts; reporter, creator, components; due date, fix versions and labels in issue facts; the severity field; the snapshot `item` map; the 120-day backfill | — | none | 1 day |
| 1 | **B. Profiles** | section 2 MVP | — | `0041` | 1–1.5 days |
| 1 | **C. Baseline and forecast days** | `planning_baselines`, `forecast_days`, Planning complete, the reconstruction job, the history read from `forecast_days` | — | `0042` | 1 day |
| 2 | **D. The Jira-observed day** | section 1 MVP | A | none | 1.5 days |
| 2 | **E. Why the date moved** | the decomposition and `forecast_days.attribution`, the day report line, `delivery_impact` (its Overall waterfall moves to J) | C (A for who and when) | none | 1.5 days |
| 2 | **F. Jira hygiene** | section 4 MVP | A, C (B for profile owners) | `0043` | 2 days |
| 3 | **J. Timeline (Gantt) with the waterfall** | section 5 MVP: Overall Timeline and waterfall, the compact Delivery timelines, the two timeline reads, retiring "Dates by pod", "How the date moved" and `DateHistory` | C, E, A (F optional, for the hygiene ring) | none | 2 days |
| 3 | **G. What-if and burn-up** | ideas 1 and 2 | E | none | 1 day |
| 3 | **H. Ageing and calibration** | ideas 3 and 5 | C | none | 1 day |
| 4 | **I. Steering pack** | idea 4, with the timeline picture | E, F, J | none (reuses `day_reports`) | 1 day |

**Conflicts to plan for.**
- E and F both add to `day_report_builder.py`: E a "what changed" line, F asks. Land E's lines
  first, or give both to one owner.
- D and B both touch drift's code-work test. B owns the `code_work` switch, and D reads it.
- A changes the Jira adapter's contract tests. D and F start on fakes until A merges.
- J and G both draw on Overall: J owns the Timeline section and G adds its what-if to J's
  waterfall, so G starts after J's layout lands. H's ageing list sits in "requirements by stage".
- **Critical path:** C → E → J, about 4.5 days. Everything else fits beside it.

---

## 8. Migrations, all in one place

| Migration | Tables | Data step | Downgrade | Lane |
|---|---|---|---|---|
| `0041_pod_member_profiles` | `profile_catalog`, `pod_member_profiles` | one `backfill` row per pod membership in force, from its pod role | drops both; nothing else touched | B |
| `0042_baselines_forecast_days` | `planning_baselines`, `forecast_days` | none (a reconstruction job runs after deploy) | drops both | C |
| `0043_jira_hygiene` | `hygiene_settings`, `hygiene_rules`, `hygiene_runs`, `hygiene_findings`, `hygiene_actions` | none | drops all five | F |
| none | `facts` source `issue_change`; new keys in task metadata, issue fact payloads, `requirement_snapshots.payload`, `integration_connections.settings`; the timeline reads stored rows only | — | — | A, D, E, J |

Rollout on the shared QA database: back it up first, and run the AGE repair after any restore, as
for `0040`. No step touches `graph_edges` or the AGE mirror.

---

## 9. Privacy and the works council

- **Findings attach to work items.**
  - Hygiene findings are about issues.
  - Attribution drivers are about scope and pace.
  - The observed day is about tasks.
- **No ranking of people anywhere.** No per-person counts of events, breaches, additions or
  activity, and no "most" or "least" lists. Aggregates stop at pod, release and project.
- **A profile is a role, not a rating.**
  - It is set by an admin and visible to the person.
  - Suggestion reasons are computed on demand and never stored.
  - It grants no rights.
- **Names only where an owner is needed.** Asks and the committed date's "by" name people, as
  today. "Added by" sits on the item's row and is hidden from executives by default.
- **No new reader of personal data.** Executives still see no pod check-ins or blockers, and
  the timeline shows them no blocker markers.
- **Timeline markers are events on work items.** They are counted per day and kind, never per
  person, and never sorted by who.
- **No free text from Jira.** Nothing quotes Jira text in facts, findings, drafts or Ora answers;
  keys, titles, field names and dates only.

---

## Open questions for the user

1. **A day built from Jira with clear progress:** keep it amber like every inferred day (the
   default: silence is never green), or allow green?
2. **Fill the day from Jira:** for everyone not asked (the default), or only for people with no
   chat account?
3. **Profiles:** may a scrum master set them in the pods they run, or only an admin? Do you need
   profiles beyond the list in 2.3?
4. **Planning complete:** per project or per release? Set by the product owner only, or a manager
   too? Is your change label `CR`?
5. **"Added by" on scope items:** shown to everyone who reads the project's delivery except
   executives (the default), to executives as well, or to nobody?
6. **Hygiene asks:** which rules should ask and escalate? The default is only "added after
   planning without a change label"; the rest list only.
7. **Timeline:** retire "How the date moved" and "Dates by pod" for it (the default, with the
   daily band one level down), or keep the slip chart beside it?

---

## Appendix: what each existing piece is reused for

| Existing | Used for |
|---|---|
| `status_collector._close_out_status`, `_fallback_inference`, `status_summaries` | close-out v2; the wording leads |
| `checkin_fanout.developers_without_checkin`, the reconcile tick, `AvailabilityService` | who the fill pass covers, when, and leave |
| `sync_services.IssueReadSyncService`, `append_fact_once`, `developers_by_tracker_account` | `issue_change` facts with the author mapped |
| `task_update_service.TaskStatement`, `persona_views.pod_tasks` | the "Jira today" line beside "Last said" |
| `checkin_drift` kinds, `member_roles`, `NON_CODE_REVIEW_ROLES` | disagreement flags; `code_work` per pod |
| `config_service.link_pod_member`, `structure.ts` `POD_ROLES` and `suggestPodRole` | the add-person dialog and the profile default |
| `directory_users.title` | the profile suggestion |
| `forecast.history_forecast`, `_scope_forecast`, `daily_completions` | the counterfactual replays |
| `requirement_snapshots`, `stage_moves`, `_in_stage_since` | partitions; stale in stage; ageing |
| `delivery_date_changes`, `checkin_drift` `eta_stated` facts | the committed and team date lanes |
| `release_readiness`: matchers, scope builders, staleness gate, fingerprint, run claim, audit, Admin "Try on a project", `report_gaps` | the hygiene engine and its surfaces |
| `gate_extraction`, `issue_scans`, `_kind_asks` | the acceptance criteria rule, without double asks |
| `day_report_asks.Ask`, `escalated()`, escalation matrix | hygiene and readiness fix asks; the profile level (P2) |
| `ask_service.tools_for`, `readiness_access`, `DeliveryScopeService` | the three Ora tools' gates |
| `ask_investigation.STEP_GUIDANCE`, `deep_investigation` prompts | planning lines for the new questions |
| `SlipChart.tsx`, Overall sections, the stage timeline's table view | where the waterfall sits and how it reads |
| `SlipChart.tsx` (its `overflow-x-auto` box and spoken `role="img"`), `PodDatesSection.tsx` and `podDates.ts`, `DateHistory.tsx`, `viz.tsx` and `vizStyles.ts` | the timeline's drawing conventions; the rows it takes over; the slip drawing as "Band over time" |
| `delivery_scope.NodeAccess`, `GET /me/delivery-tree`, `ensure_project_read`, `ensure_pod_dates` | which timeline rows each role gets, and how much of each |
