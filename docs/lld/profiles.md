# Profiles LLD

The discipline a member brings to each pod they are in, set when they join and
read per pod by the parts that decide who is asked and named. Feature summary:
`features.md` §4.32.

## Model

- `core/domain/profiles.py` is pure: the generic catalog
  (`default_profiles()`: development, QA and test, security, product, UX and
  design, TPM / delivery, ops, data, architecture, engineering management,
  other), each with a label, `code_work` (merge requests are expected) and the
  title words that suggest it; `merged_catalog` puts a tenant's entries over
  the defaults; `profile_for_pod_role` (developer → development, product owner
  → product, scrum master → delivery, manager → management, anything else →
  development); dated `PodMemberProfile` rows; `suggest_profile`.
- Sources: `backfill` (migration), `admin` (an admin or the pod's scrum
  master), `import` (directory import into a pod), `suggestion_accepted`. A
  row of the last three is *checked*. A membership with no row reads its pod
  role's profile and is not checked either.
- The current profile is the latest row with the membership's start ≤
  `valid_from` ≤ the day read. A row from an earlier membership never counts,
  so someone taken out and added back starts from the pod role again.
- A profile grants no rights. Authorization stays with sign-in roles.

## Suggestion

`suggest_profile(catalog, inputs)`, never stored, hidden entries never
suggested:

1. the directory title (`directory_users.title`, else the member node's
   `title`) against each profile's title words: the longest phrase found
   wins, then the earliest ("QA Engineer" is QA, "Program Manager" delivery);
2. else the person's issues in the pod (assigned tasks the pod holds, by
   `pods_for_task`; all their tasks when no task names a pod): half or more of
   at least two of one profile's issue types, or labels and components naming
   one (security, design);
3. else any commit or merge request of theirs in 90 days: development;
4. else the pod role's profile.

Evidence that agrees with the pick is added to its reasons.

## Storage

Migration `0041_pod_member_profiles`, additive: `profile_catalog` (a tenant's
changes; no rows means the defaults) and `pod_member_profiles` (PK tenant, pod,
member, `valid_from`; a second change on the same day replaces that day's
row). Data step: one `backfill` row per `contains` edge from a pod to a
developer that has not ended, from its `metadata.role`, starting on the edge's
own `valid_from` (1970-01-01 when none). It reads `graph_edges` and writes no
existing table or AGE data; the downgrade drops the two tables. In-memory
repository for memory mode and tests.

## Who may

`Capability.SET_POD_PROFILES`: the scrum master (an admin has all), and a
scrum master only where `ForecastService.runs_pod` holds (the pod's scrum
master contact or one of its members). Others get 403 in words. Reading a
pod's profiles needs what the pod's panel needs (`ensure_pod_detail`); the
catalog is read by everyone; changing it needs `MANAGE_CONFIG`.

## API

- `GET /profiles`; `PUT /config/profiles/{slug}` (rename, hide, add).
- `GET /pods/{id}/profiles?as_of=`: each member's role, profile, source,
  checked, and `can_edit` for the caller.
- `GET /me/profiles?as_of=`.
- `GET /config/pods/{pod}/members/{member}/profile?role=`: current, history
  and a suggestion, also before the person joins (the add form preselects it).
- `PUT /config/pods/{pod}/members/{member}/profile` `{profile,
  accepted_suggestion}`: a row from today.
- `GET /config/profiles/review?pod_id=` and `POST
  /config/profiles/review/accept`: the unchecked memberships in the pods the
  caller sets, and accepting the suggestions as shown.
- `POST /config/pods/{pod}/members/{member}` takes `profile`, and
  `POST /config/members/from-directory` takes `pod_id`, `role` and `profile`.
  The profile is checked before anything is linked or imported.

## Readers

- **Code-work test** (`StatusCollector._code_work_keys`): for an issue said to
  be in review, the person's checked profiles in the pods holding it (its own
  pods, else its project's) decide whether a merge request is expected; any of
  them with `code_work` makes it code work. Undecided issues keep the app-role
  rule (`checkin_drift.code_work_keys`).
- **Release readiness**: a criterion's `owner_profile` adds a fix ask
  ("Release 1: create or link the security review") to that profile's holder
  in the gap's pod, or the project's pods by name, beside the decision ask,
  which is unchanged. Nobody holding it reads "nobody in its pods has the
  Security profile yet".
- **Day report**: an owner's heading adds their chosen profiles in the
  project's pods ("Mina Patel (Product, Payments Pod)").
- **Ask**: `team_profiles` (behind `may_ask`), each pod's members with role
  and profile, and for a project the profiles its switched-on criteria ask for
  that none of its pods holds.

## Later

An escalation level "the team's QA", the lead among several holders, Jira
groups mapped to profiles, a cross-person request to "QA" resolved to the one
holder, and a coverage line per release.
