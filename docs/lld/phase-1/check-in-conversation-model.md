# Check-In Conversation Model

Daily check-ins are modeled as one status aggregate per person per local day. A chat transcript is context for that aggregate; it is not the identity of the aggregate.

## Boundaries

- A developer has at most one daily status outcome per local date.
- Follow-up nudges and clarifications stay attached to the same check-in correlation.
- A same-day chat transcript can contain many turns and facts, but it should not create one check-in per visible message.
- Dependency mentions are captured as facts from the reporter's check-in. They do not automatically start a second person-to-person workflow.

## Daily Status

The daily status aggregate should answer:

- what progress the developer reported;
- what blockers they reported;
- whether ETA changed;
- whether the status is confirmed, partial, inferred, stale, or unknown.

The scheduled check-in path enforces one scheduled run per `(tenant_id, developer_id, checkin_date)`. Lower-level storage may still use correlation identifiers, so reply routing must stay conservative when multiple open interactions could match the same user, day, or thread.

## Blocker Lifecycle

Blockers are first-class records owned by the person, spanning days — not
strings inside a single day's status. Each blocker carries identity
(`blocker_id`, a normalized-description key), lifecycle dates
(`first_seen_on`, `last_seen_on`, `resolved_on` with a reason), and optional
attribution to a work item and/or an explicit pod. `DeveloperStatus.blockers`
remains a derived compatibility projection of the open set.

- **Attribution.** A developer can belong to multiple pods, so a blocker's pod
  scope is resolved at read time: an explicit pod wins, else the work item
  resolves to pods through the graph, else the blocker is *unattributed* and
  surfaces in every pod the developer belongs to, flagged. The "link to a work
  item" follow-up anticipated above now happens inline: the parser captures an
  issue key stated in the reply, and a multi-pod developer with a new
  unattributed blocker is asked at most ONE attribution clarification (per
  blocker, ever — a durable `attribution_asked_at` stamp enforces this).
- **Clarification budget.** Required blockers/ETA details and person
  disambiguation always outrank the attribution question within
  `checkin_max_clarifications`.
- **Carry-forward.** Open blockers a reply does not mention stay open without
  advancing `last_seen_on` (that is what keeps blocker age honest). Resolution
  requires model-confirmed resolved ids — the prior-blockers prompt context
  lists bracketed handles the model maps back — or, for unstructured replies
  only, the legacy explicit-resolution text heuristic.
- **Non-response outcomes** (inferred/stale/unknown) never mint, resolve, or
  touch lifecycle rows. The synthetic "no confirmed reply" marker exists only
  in the compatibility strings, never as a lifecycle row.
- **Corrections vs chat.** A UI correction is a total statement (the developer
  edits the full displayed set), so omitting an open blocker resolves it and
  an explicit edit may re-attribute. A chat reply is a partial statement, so
  unmentioned blockers carry forward. This asymmetry is deliberate.

## Dependency Facts

When a reply mentions another person, store the dependency as a fact on the reporter's status instead of automatically messaging the referenced person.

Recommended fields:

```text
tenant_id
source_checkin_id
reporter_id
referenced_person_id nullable
referenced_person_name
summary
kind: waiting_on | blocked_by | needs_review | needs_input
status: open | resolved | stale
first_seen_at
last_seen_at
```

The dependency should be available in reporter status, referenced-person attention surfaces when the person is confidently resolved, project and pod reports, and manager rollups. Notify, ask for clarification, mark resolved, or link to a work item should remain explicit follow-up actions.

## Reply Routing

Route replies in this order:

1. Explicit internal correlation.
2. Chat thread parent or outbound message id.
3. Exact manually-triggered coordination correlation.
4. A single open expected-reply interaction for the user and local day.
5. If multiple open interactions remain, do not guess. Ask for clarification or leave the event unresolved for operator review.

This keeps ambiguous same-day check-ins a safety case instead of a normal path.

## Design Decision

Treat a same-day chat conversation as a context boundary, not an identity boundary. The identity boundary for daily status is the per-person/per-local-day check-in aggregate. The identity boundary for dependencies is the extracted dependency fact.

