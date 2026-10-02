# OpenProgram Features and Business Requirements

## 1. Product Summary

OpenProgram is an agentic program-management system for continuously collecting, reconciling, rolling up, and presenting delivery status across software organizations.

The application replaces manual status chasing and meeting-driven reporting with:

- a graph-backed source of truth for programs, projects, workstreams, pods, developers, and tasks;
- proactive developer check-ins through chat;
- read-only ingestion from delivery systems such as Jira, GitHub, and GitLab, with live calendar availability checks;
- an opt-in, audited write-back of issue state that is off by default;
- deterministic RAG rollups from developer/task level to pod, workstream, project, program, and portfolio level;
- signal-derived risk and drift findings, scheduled narrative briefs, and questions answered from the graph;
- a role-specific console for developers, scrum masters, product owners, managers, executives, and admins;
- runtime configuration screens for the organization hierarchy, check-in schedules, and data-source status.

## 2. Business Goals

- Reduce manual status collection across teams and programs.
- Prevent silence from being interpreted as healthy progress.
- Give each persona a view that maps directly to their daily decisions.
- Make rollups explainable by linking high-level red/amber status back to source developers, tasks, blockers, or facts.
- Keep the platform provider-neutral so chat, issue tracker, VCS, calendar, workflow, LLM, and persistence providers can be swapped through adapters.
- Support both local/demo operation with fake providers and container/runtime operation with real services.

## 3. Primary Personas

| Persona | Business Need | Application Support |
|---|---|---|
| Developer | Know current focus, blockers, tasks, and check-in expectations. | Today with the check-in to confirm or correct, focus list, tasks, requests waiting on them, and where their status rolls up; proactive chat check-ins; own check-in schedule (days and time zone). |
| Scrum Master | Track team check-in completeness and active blockers. | Today with pod selector, check-in board, and blocker aging; Delivery pod panels with tasks and rollup reasons; Signals; Coordination. |
| Product Owner | Track project progress, task health, and delivery risk indicators. | Today with project selector, progress ring, task breakdown, and a needs-your-attention list; Delivery project and workstream panels; Signals; Coordination. |
| Manager | Review team/program health across multiple delivery layers. | Portfolio Today (verdict, momentum, executive brief, heat, top signals); Delivery at every level, including pod check-ins, blockers, and tasks; Signals; Coordination and Ask the graph. |
| Executive | View portfolio health at a glance without raw developer-message access. | The same portfolio Today as the manager; Delivery program, project, and workstream panels, but not pod check-ins or blockers; Signals; Coordination and Ask the graph. |
| Admin | Configure hierarchy, members, links, assignments, check-ins, and workflow dispatch. | Admin screen (Entities, Links, Directory, Check-ins, Data sources), config APIs, directory sync/import, workflow dispatch and ops APIs. |

## 4. Current Implemented Feature Set

### 4.1 Graph of Truth

**Business requirement:** The system shall maintain a single canonical graph of delivery entities so status can be rolled up and drilled down consistently.

Functional requirements:

- The system shall model programs, projects, workstreams, pods, developers, tasks, work items, repos, and sprints as graph nodes.
- The system shall model hierarchy and assignment relationships with typed edges such as `CONTAINS`, `ASSIGNED_TO`, and `DEPENDS_ON`.
- The system shall support drill paths from program to project, workstream, pod, developer, and task.
- The system shall treat a workstream as a delivery level of its own: a project contains workstreams, a workstream contains tasks and work items, and pods are assigned to the workstreams they serve.
- The system shall record a workstream's type, phase, owner, TPM, scrum master, and target date.
- The system shall support matrixed relationships, including pods linked to multiple projects, pods serving several workstreams, and developers linked through pod membership.
- The system shall store source facts as append-only evidence with timestamps, source identifiers, entity references, payloads, and correlation IDs.
- The system shall expose directory views for configured programs, projects, workstreams, and pods, including relationship IDs, latest rollup status where available, and the names of the people a node refers to, such as a workstream's owner, TPM, and scrum master.

### 4.2 Runtime Configuration Management

**Business requirement:** Admins shall configure the business hierarchy at runtime instead of depending on hardcoded seed data.

Functional requirements:

- Admins shall create, view, update, and delete programs.
- Admins shall create, view, update, and delete projects.
- Admins shall create, view, update, and delete workstreams.
- Admins shall create, view, update, and delete pods.
- Admins shall create, view, update, and delete members/developers.
- Admins shall link and unlink projects to programs.
- Admins shall link and unlink pods to projects.
- Admins shall link and unlink workstreams to projects, pods to workstreams, and tasks to workstreams.
- Admins shall link and unlink members to pods with a role-in-pod label.
- Admins shall assign and unassign tasks to members.
- Admins shall create work items (directly, or from a branch or pull request), record work-item transitions, and link work items to workstreams through the config API.
- Admins shall search synced directory users.
- Admins shall add selected directory users as configured members.
- Admins shall trigger directory sync and see synced/deactivated counts through API responses.
- Admins shall set each member's identity links (chat, issue-tracker, and VCS IDs), fill missing chat IDs and issue-tracker emails from the directory without overwriting what an admin set, and see how many members have no chat ID.
- Admins shall set a pod's escalation contacts (scrum master and manager) by picking members, the pod's own members listed first, instead of typing chat IDs.
- Admins shall set each member's write-back consent, and the admin screen shall say whether write-back is switched on for the tenant (see 4.24).
- The admin screen shall group this work into Entities, Links, Directory, Check-ins, and Data sources tabs.
- The system shall reject invalid configuration mutations such as duplicate links, self-links, wrong node kinds, missing references, and conflicting IDs.

### 4.3 Check-In Preference Management

**Business requirement:** Check-ins shall respect each member's check-in days and reply windows. Check-ins go out at one tenant-wide time (see 4.4).

Functional requirements:

- A member shall open their own check-in schedule from the avatar menu and change the days the bot asks them and their time zone.
- The member's own schedule shall refuse any other field: reply windows and write-back consent stay with an admin, and there is no per-person check-in time.
- Only a configured member shall have a check-in schedule; the avatar-menu entry shall not be offered to anyone without a member record, since the bot never asks them.
- A member's time zone shall decide which day their reply counts for.
- Admins shall view check-in preferences for all configured members.
- Admins shall update a member's timezone.
- Admins shall choose active weekdays for check-ins.
- The system shall refuse an empty list of check-in days, from an admin or from the member, because it would stop that member's check-ins without saying so.
- Admins shall configure reply wait and final reply wait windows as an amount and a unit, with presets, so a stored value is shown exactly and saving a member without changes leaves their windows as they were.
- The system shall reject a negative reply window, or one too large to store.
- The console shall not show or set a per-member check-in time, because check-ins are sent on the single tenant-wide schedule. The API keeps the stored field, and the scheduler does not use it to decide when to send.
- A changed preference shall apply from the member's next check-in, never to one already sent.
- A member shall follow the team default for any field not set for them: check-in days, time zone, and reply windows. Only values set for the member shall be stored, so a later change to a default reaches everyone who hasn't set that field.
- The admin screen shall show which of a member's values are the team default and which were set for them, and let an admin put a field back to the team default. A member shall be able to go back to the team's time zone from their own schedule.

### 4.4 Daily Status Collector

**Business requirement:** The system shall proactively collect status from developers through chat and convert free-text replies into structured delivery signals.

Functional requirements:

- The system shall dispatch developer check-ins through the workflow layer.
- The system shall send the day's check-ins to every member on one tenant-wide schedule (`OPENPROGRAM_CHECKIN_FANOUT_CRON`), skipping a member whose check-in days exclude that weekday.
- The system shall run a reconcile pass after a configured local cutoff that dispatches a check-in to any member who has none recorded for the day.
- The system shall send at most one check-in per member per day.
- The system shall send direct chat messages with correlation IDs.
- The system shall record outbound check-ins, chat message references, asked timestamps, and developer context.
- The system shall accept inbound chat webhook replies.
- The system shall resolve replies to the correct check-in by explicit correlation, thread-local matching, or user-local matching for the developer's local date.
- The system shall avoid guessing when multiple candidate check-ins are ambiguous.
- The system shall record conversation turns for recent context within the configured retention policy.
- The system shall use the LLM/parser layer to classify whether a reply is a status update.
- The system shall parse status replies into structured signals such as progress note, blockers, ETA change, and mood.
- The system shall ask clarification questions when a status reply is insufficient and the clarification cap has not been reached.
- The system shall finalize the check-in when enough information is available or the clarification cap is reached.
- The system shall acknowledge non-status replies without falsely recording them as healthy status.
- The system shall ignore duplicate message IDs and already-processed replies.
- The system shall turn asks of other people found in a reply into cross-person requests (see 4.22).
- The system shall pass issue-state claims found in a finalized reply to the gated write-back path (see 4.24); a write-back failure shall never lose the recorded check-in.

### 4.5 Non-Response and Nudge Handling

**Business requirement:** Missing replies shall be visible and shall not silently become green status.

Functional requirements:

- The system shall send a follow-up nudge for a pending check-in once the member's reply wait has passed.
- The system shall refuse to nudge a check-in that already has a reply.
- The system shall read the developer's calendar availability before a nudge and hold the nudge back when an event blocks availability, such as time off.
- The system shall escalate a still-unanswered check-in to the pod's scrum master and then its manager, each after a tenant-wide wait, using the pod's escalation contacts; a step with no contact shall send nothing.
- Each escalation step shall be switchable off in tenant settings.
- The system shall record stale or unknown status when the developer does not respond within configured windows.
- A stale status shall name the last confirmed, partly answered, or inferred status once, with its date, however many days in a row the developer has not answered.
- The system shall be able to infer limited status from recent facts when no human confirmation exists.
- The system shall mark inferred or missing status with reduced confidence/source quality.

### 4.6 Status Parsing and Conversation Context

**Business requirement:** Developer messages shall be interpreted using useful recent context while avoiding raw-message exposure in persona views.

Functional requirements:

- The system shall include recent conversation turns as parser context.
- The system shall expose a conversation-history tool to the status parsing agent within bounded limits.
- The system shall carry forward unresolved prior blockers unless the developer clearly resolves them.
- The system shall avoid clearing blockers on negated or ambiguous language.
- The system shall prioritize blocked and critical active issues in check-in context.
- The system shall avoid exposing raw DM content through public persona APIs or dashboard views.

### 4.7 Read-Only Delivery Integrations

**Business requirement:** The system shall ingest hard delivery signals without writing to source systems. The only write path is the gated, opt-in issue-tracker write-back in 4.24.

Functional requirements:

- The system shall sync issue-tracker projects into graph project nodes.
- The system shall sync issue-tracker sprints into graph sprint nodes.
- The system shall sync issue-tracker issues into graph task nodes.
- The system shall link issues to projects or matching sprints.
- The system shall create developer nodes and task assignments from issue assignees.
- The system shall append issue facts with status, assignee, project, and observed timestamps.
- The system shall sync VCS repositories into graph repo nodes.
- The system shall append commit facts to developer or repo entities.
- The system shall append pull request facts to developer entities.
- The system shall read calendar availability live from the provider when it is needed (see 4.5) rather than syncing calendar events; the calendar sync records nothing.
- The system shall store provider-neutral sync cursors for incremental syncs.
- The system shall record sync metadata such as last checked time and item count.
- The system shall record each sync run's outcome per target, and a failure only as a fixed category (credentials, provider unavailable, or unexpected), never the provider's error text.
- Admins shall see on the Data sources tab, per data source (issue tracker, VCS, calendar, directory), its configured targets, when it last synced, its last attempt, the newest item seen, and a health of healthy, stale, failing, never synced, not configured, or disabled. A source with no recorded run shall never read as healthy.
- The sync status view shall be built only from recorded runs and shall never call a provider.
- The sync status view shall show a source whose provider cannot be built (for example a missing chat token) as failing, with a reason made only of fixed text and the error type, never the error message or a credential value.

### 4.8 Rollup Engine

**Business requirement:** The system shall compute explainable health status across the delivery hierarchy.

Functional requirements:

- The system shall compute RAG values: green, amber, red, and unknown.
- The system shall calculate developer status from confirmed, stale, inferred, unknown, and blocker signals.
- The system shall escalate status when blockers exist.
- The system shall escalate a single blocker on a critical-path task.
- The system shall escalate multiple blockers to red.
- The system shall keep stale or inferred status out of green.
- The system shall aggregate child statuses into pod, workstream, project, and program status.
- The system shall roll a workstream up from its tasks, and shall add an amber factor when the workstream's target date is approaching.
- The system shall record rollup factors explaining why a node has a given RAG value.
- The system shall store node rollups on a schedule (hourly by default), backfilling missed weekdays, so stored history does not depend on who opened which screen.
- Read paths shall compute a rollup that is not stored yet for the response only, and shall never persist it.
- The system shall keep task nodes as source items rather than rollup nodes.

### 4.9 Persona Home (Today)

**Business requirement:** Each user role shall land on one screen focused on the decisions they own.

Functional requirements:

- The console shall open every role on Today, showing the view for the role being viewed as.
- The developer view shall show today's check-in summary and open blockers, where the status came from (answered in chat, confirmed in the console, partly answered, inferred from delivery signals, stale, or carried forward from an earlier day), a focus list ranked by urgency, and assigned tasks with RAG, status source, and confidence.
- A developer shall confirm the day's status, or correct its summary, blockers, and ETA change, from the console. A correction is a full statement, so a blocker left out of it is resolved.
- Confirming a status the developer didn't give that day (inferred, stale, unknown, or carried forward from an earlier day) shall record what was confirmed: the earlier day's status with its date, or what the inference was drawn from. It shall not keep the "no confirmed check-in" wording or the no-reply placeholder blocker.
- The developer view shall show the pods, projects, and programs the developer's check-in rolls up into, each with its current colour, and shall say so plainly when the developer is in no pod.
- The scrum master view shall default to the pods the person belongs to, and shall show each member as confirmed, partial, stale, or missing, plus open blockers with owner, source, and age.
- The product owner view shall default to the projects of the person's pods, and shall show project progress, task counts by RAG, and the tasks that need attention.
- The developer, scrum master, and product owner views shall list the cross-person requests waiting on the person (see 4.22).
- Manager, executive, and admin shall see the same portfolio view: a one-line verdict on the program with the oldest open risk as its reason, a 30-day momentum line measured only over days that reported a status, the newest executive brief, portfolio heat rows for projects, workstreams, and pods, and the three oldest open risks.
- Heat rows shall order items worst first, show four per row, say "worst N of M" when a row is cut short, and open the matching Delivery panel when a tile is clicked.
- A loading, failed, or empty reading shall never be presented as on track.
- The console shall populate pod, project, and program selectors from runtime directory data.
- Screens shall show appropriate loading, unavailable, and unauthorized states, and shall explain which role a refused read needs rather than show a denial as empty data.

### 4.10 Delivery Explorer

**Business requirement:** Users shall walk the delivery graph and see, for any node, its status and why it has it.

Functional requirements:

- The Delivery screen shall list every program, project, workstream, and pod in a navigator, and shall keep the selection in the URL (`/delivery/:kind/:id`) so any panel can be linked.
- Project, workstream, and pod panels shall show the node's status chip, a one-line reason naming the worst rollup factor and whom it comes from, links to related nodes coloured by their status, and, when there is more than one reason, a card listing each reason with its sources.
- A node shall never read as on track when nothing is recorded for it: no reasons reads as "nothing recorded".
- The program panel shall name what sets the program's status, list its projects worst first with each one's top reason and its workstreams and pods, and count what rolls up into it. The reasons behind each status shall need a manager, executive, or admin role.
- The project and workstream panels shall show progress, task counts by RAG, source, confidence, and the task list.
- The workstream panel shall show its type, phase, target date, and its owner, TPM, and scrum master by name. An ID that matches no member shall be shown as an ID, never as a name.
- The pod panel shall show members, confirmed check-ins, open blockers, and the pod's tasks: those assigned to its members within the pod's remit (the pod itself, the workstreams it serves, or a project that contains it), blocked first, each with its owners and the open blockers attributed to it.
- A panel the viewing role may not read shall say which role opens it, instead of showing a denial as dashes or a 0% ring.

### 4.11 Portfolio Heat and Signals

**Business requirement:** Executives and managers shall review program-level health, and the risks behind it, from a portfolio view.

Functional requirements:

- The console shall show portfolio heat on the manager, executive, and admin Today (see 4.9).
- The portfolio heatmap API shall return rollup cells by entity kind and entity ID.
- The portfolio heatmap shall use an explicit program root when one is supplied.
- The portfolio heatmap shall fall back to the first configured program when no root is supplied.
- The portfolio view shall return an empty state when no programs are configured.
- The Signals screen shall be offered to every role with a team or executive read, and not to developers.
- Signals shall count open risks, watermelons, and drift findings, and shall list them in one stream filterable by Everything, Risks, Drift, Flow, and Feed.
- Risk and drift cards shall put what the owner says beside what the signals say (see 4.19).
- The Flow view shall show active items, features in flight, stale (7 days or more) and abandoned (21 days or more) counts, average cycle time, and average pull-request age for the portfolio, and active, stale, and abandoned counts per workstream.
- The Feed shall list the latest activity (work-item changes, pull requests, commits, issue updates, check-ins, risks, and cross-person requests) from the last 7 days, up to 50 items.

### 4.12 Role-Based Authorization

**Business requirement:** Access to capabilities and sensitive fields shall be explicit and default-deny.

Functional requirements:

- The system shall define roles for developer, product owner, scrum master, manager, executive, and admin.
- The system shall check capabilities centrally in the authorization policy.
- Every role shall read its own work and directory data, since anyone with a member record is asked to check in.
- Developers shall hold no aggregate read; they may write back only to their own issues (see 4.24).
- Scrum masters shall read directory data, team aggregates, pod blockers, and pod check-ins.
- Product owners shall read directory data, team aggregates, and project and workstream progress.
- Managers shall read directory data, team aggregates, executive aggregates, project and workstream progress, pod blockers, pod check-ins, program rollups, and portfolio heatmaps. The manager is the last step of the non-response escalation, so they can open the pod they are escalated about.
- Executives shall read directory data, executive aggregates, project and workstream progress, program rollups, and portfolio heatmaps, but not pod blockers or pod check-ins.
- A pod's task list and rollup reasons shall need both the pod check-in and pod blocker reads, so scrum masters, managers, and admins open them.
- Signals, flow, feed, trends, briefs, the portfolio requests board, and Ask the graph shall need a team or executive aggregate read.
- A cross-person request's status shall be changeable by a team or executive reader, or by the request's own requester or counterpart.
- Admins shall manage configuration and dispatch workflows.
- Admin role shall short-circuit to allowed for capabilities.
- No role, admin included, shall be granted raw DM content: there is no capability or field for it. Budget-like sensitive fields shall be readable only by admins and executives.
- Unauthorized persona API calls shall return forbidden responses.

### 4.13 Admin Workflow Dispatch

**Business requirement:** Admins shall be able to trigger operational workflows manually.

Functional requirements:

- Admins shall dispatch a developer check-in workflow.
- Admins shall dispatch issue-tracker sync for a project/container scope.
- Admins shall dispatch VCS sync for a repository.
- Admins shall dispatch calendar sync for a user/date range.
- Dispatch APIs shall return a workflow ID.
- Workflow dispatch shall be admin-only.
- Admins shall list open dead letters and re-arm one, revert an applied write-back, and read the per-source sync status (see 4.7) through the ops API.

### 4.14 Chat Webhook Handling

**Business requirement:** The system shall process chat-provider callbacks safely and idempotently.

Functional requirements:

- The system shall support chat webhooks under provider-specific paths.
- The system shall return provider verification challenges where required.
- The system shall ignore unsupported providers.
- The system shall ignore uncorrelated replies.
- The system shall detect duplicate replies for already-replied check-ins.
- The system shall map provider payloads into provider-neutral inbound messages before application processing.

### 4.15 Integrations and Provider Adapters

**Business requirement:** External systems shall be replaceable behind stable ports.

Functional requirements:

- The system shall provide a chat provider port and fake, Slack, and built-in chat implementations; Slack events shall arrive over HTTP webhooks or Socket Mode.
- The system shall provide a chat webhook mapper and fake/Slack implementations.
- The system shall provide an issue tracker port and fake/Jira implementations.
- The system shall provide a VCS port and fake/GitHub/GitLab implementations.
- The system shall provide a calendar provider port and fake/Google Calendar implementations.
- The system shall provide a directory provider and fake, Slack, and built-in chat directory implementations.
- The system shall provide an LLM provider and fake/LiteLLM implementations.
- The system shall provide workflow scheduler/worker adapters for fake, DBOS, and Temporal modes.
- The system shall use Redis for rate limiting and chat conversation caching when not in memory mode.
- The system shall use encrypted secret storage for connector credentials.

### 4.16 Observability, Health, and Readiness

**Business requirement:** Operators shall be able to verify health and trace requests across the stack.

Functional requirements:

- The API shall expose `/health` with status, environment, tenant ID, and correlation ID.
- The API shall expose `/ready` with dependency readiness checks, a boolean per dependency, and a short reason for any dependency whose probe gives one.
- The LLM readiness probe shall ask the configured OpenAI-compatible endpoint to list its models with the configured key, so it shows the endpoint is reachable and accepts the key without spending a token, and shall fall back to a LiteLLM gateway's readiness path for an endpoint that does not list models.
- A readiness reason shall say what went wrong (unreachable, key missing or rejected, rate-limited, failing upstream, or not an OpenAI-compatible endpoint), and shall never carry credentials, hosts, or response bodies, since `/ready` is unauthenticated.
- The API shall expose Prometheus metrics at `/metrics`.
- The API shall attach or generate correlation IDs for requests.
- The API shall return the correlation ID on responses.
- The API shall emit OpenTelemetry spans for HTTP requests.
- The API shall observe request method, route, status code, and duration metrics.
- The application shall support structured logging and tracing configuration.

### 4.17 Persistence and Runtime Modes

**Business requirement:** The app shall run locally with fake/in-memory dependencies and in container/runtime mode with persistent services.

Functional requirements:

- The app shall support memory runtime mode for local and test execution.
- The app shall support Postgres-backed graph, status, rollup, sync cursor, conversation, vector, directory, and time-series repositories.
- The app shall use Redis in non-memory runtime mode for cache/rate-limit functions.
- The app shall use settings-based provider selection.
- The app shall support one-command local infrastructure via Docker Compose.
- The app shall avoid automatic demo seeding on API startup; sample seed data is an explicit bootstrap action.

### 4.18 Frontend Application Shell

**Business requirement:** Users shall access every screen and admin workflow through one typed React console (`frontend-v2/`).

Functional requirements:

- The frontend shall use a typed API client generated from OpenAPI.
- The frontend shall send a unique correlation ID header with API requests.
- The header shall offer Today, Delivery, Signals, and Coordination, plus Chat when the backend serves the built-in chat (see 4.25). Signals shall be hidden from developers, who hold no aggregate read.
- The avatar menu shall offer the member's own check-in schedule (see 4.3), the check-in chat when it is served, and Admin configuration for the admin role only; the Admin route shall send any other role back to Today.
- On a dev-auth tenant, the avatar menu shall offer "View as" for the roles the acting person holds, and the console shall remember the chosen role in local storage.
- On a local dev-auth tenant with demo mode on, the header shall carry a searchable person picker, grouped by each person's most senior role. Choosing a person shall re-issue every request as them, and changing person or role shall drop all cached data. The picker shall be absent under any real auth provider.
- A command palette (⌘K or Ctrl+K) shall jump to any screen, program, project, workstream, or pod.
- The header shall carry the viewing-date control (see 4.20).
- Under a real auth provider, the console shall ask an unauthenticated user to sign in, and shall show a signed-out page after logout.
- The frontend shall provide reusable UI primitives for cards, fields, dialogs, pills, progress rings, RAG chips, segmented bars, and sparklines.
- The original console in `frontend/` shall remain buildable as a reference; CI lints, typechecks, format-checks, and builds both apps.

### 4.19 Risk and Drift Detection

**Business requirement:** The system shall surface delivery risk from hard signals, independently of what an owner says, and shall keep a risk's severity honest as it ages.

Functional requirements:

- The system shall detect a feature that has been active past a threshold with no linked pull request.
- The system shall detect a work item with no state change past a staleness threshold.
- The system shall detect a pull request open past an age threshold, scoped to the project's repositories.
- The system shall resolve each threshold per workstream, falling back to a global default when the workstream does not set one.
- The system shall rate a finding amber at its threshold and red at double it.
- The system shall present a finding's age as the age it has on the date being read, not the age it had when the rule fired.
- The system shall re-derive a finding's severity from that current age, so an open finding escalates as it ages without needing to be re-detected.
- The system shall keep a finding's stored reason as a statement about detection time, since it describes the evidence that opened the finding.
- The system shall record a fact when a finding opens and when it clears, and shall not duplicate a finding that is already open.
- The system shall show the owner's own narrative beside the signal that contradicts it.
- The system shall detect drift such as work reported done with no pull request, and claimed progress with no activity.
- The system shall detect watermelon risk where a green parent hides a red child.
- The system shall order open findings by severity, so the most severe reach a reader first.

### 4.20 Viewing a Past Date

**Business requirement:** Users shall be able to see how delivery stood on an earlier day, and shall not be able to change anything while they look back.

Functional requirements:

- The header shall carry one viewing-date control for the whole console. It defaults to today and allows no later day.
- A past viewing date shall live in the URL as `?asOf=YYYY-MM-DD`. No parameter means today, and a malformed, impossible, or future value shall be dropped from the URL.
- The viewing date shall carry over to in-app links, and back and forward shall restore the date each history entry had.
- While a past day is viewed, every screen shall show a banner naming the day, with a "Back to today" action.
- While a past day is viewed, nothing shall be changeable. Confirm, correct, acknowledge, resolve, send, request check-in, and clear history shall be disabled, and the client shall refuse any change request as a backstop. Asking the graph and signing out stay allowed.
- These reads shall honour the viewing date: directory statuses and portfolio heat; the developer's status, focus list, and tasks; pod check-ins, blockers, rollup reasons, and tasks; project and workstream progress; the program tree; the 30-day momentum line; open risks and drift; flow metrics; and Ask the graph answers.
- These shall show current state whatever the viewing date, and the screen shall say so: the Signals feed (left out of Everything on a past day), narrative briefs, cross-person requests, and the chat, which is read-only.
- The Admin screen shall always show current configuration, and the viewing-date control shall be hidden there.
- The console shall move "today" forward when the day changes, so a console left open never keeps asking for yesterday.

### 4.21 Narrative Briefs

**Business requirement:** Leaders shall get short written summaries of delivery without anyone assembling them by hand.

Functional requirements:

- The system shall generate three kinds of brief on configurable schedules: a daily brief per pod on weekdays, a weekly brief per project, and a weekly executive brief for the whole portfolio.
- A brief shall summarize only the delivery facts, feed items, and status rollups it is given (one day of activity for a pod brief, seven days for the others) in two to four sentences, with no recommendations and no raw chat or reply content.
- When the model call fails or returns nothing usable, the system shall store a brief built directly from the same facts.
- The system shall store each brief with its title, body, generation time, and the sources it drew on.
- Reading briefs shall need a team or executive aggregate read, and the read shall filter by kind on the server.
- Coordination shall list the newest briefs, one per scope per day, with a filter for All, Exec, Weekly project, and Daily pod that is kept in the URL.
- The manager, executive, and admin Today shall lead with the newest executive brief, link to Coordination filtered to executive briefs, and say so when none has been generated yet.
- Brief generation shall be switchable off. Briefs are not sent to chat in the current phase.

### 4.22 Cross-Person Requests

**Business requirement:** An ask one person makes of another in a check-in shall be tracked until it is resolved, instead of being lost in a chat thread.

Functional requirements:

- The system shall detect asks of other people in check-in replies (a dependency, a review, or input) and record each as a cross-person request with its requester, note, and source check-in.
- The system shall match the named person to a directory user, and shall mark a request whose person cannot be matched as needing resolution.
- When counterpart notification is on (it is by default; an operator can switch it off with `OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY=false`), the system shall send the counterpart one DM about the request. The DM shall name who asked, the kind of ask (review, input, or dependency) and a short note, kept under the outbound DM length cap, and shall never quote the requester's reply.
- The system shall send no DM about a request whose person was not matched or is the requester. Such a request stays visible to the requester, and a DM that fails to send shall not lose the request.
- The system shall retry a counterpart DM that failed to send, from a scheduled pass, with the same message. It shall count every attempt, the first included, stop after a configured number (5 by default) with a wait that doubles after each failure (5 minutes after the first by default), never send the DM twice when passes overlap or the DM was recorded meanwhile, and send nothing while counterpart notification is off. A request recorded while notification was off is never DMed later.
- The requester's "Raised by you" list and the board shall say when the DM is still being retried and when it was not delivered.
- The counterpart's reply shall acknowledge or resolve the request, and the requester shall be told when it is resolved. A reply after the request is resolved shall change nothing.
- The system shall record each status change as a fact, so requests appear in the activity feed.
- Coordination shall show requests in Open, Acknowledged, and Needs resolution columns, naming requester and counterpart, with Acknowledge and Resolve actions on each card.
- Team and executive readers shall see the portfolio-wide board; a developer shall see the requests waiting on them.
- Coordination shall show "Raised by you": the open and acknowledged requests the person asked of others, and where each one has got to.
- The developer, scrum master, and product owner Today shall show "Waiting on you": the open and acknowledged requests where the person is the counterpart.

### 4.23 Ask the Graph

**Business requirement:** Leaders shall be able to ask a plain-language question about delivery and get an answer drawn from the graph, not a free-form guess.

Functional requirements:

- Coordination shall offer a question box to every role with a team or executive read; a developer shall see an explanation instead.
- The system shall answer through a tool-calling loop over the delivery graph. It shall tell the model which day the question is asked for, and shall resolve named periods (today, yesterday, this week, last week, the last 7 days, the last 30 days) against that day, never the server's clock.
- The system shall offer each role only the tools whose matching endpoint that role may read. Every team or executive reader gets graph search and neighbours, recent activity over a window of up to 31 days, workstream and portfolio flow, and open risks and drift findings. Workstream progress needs the project-progress read, the portfolio heatmap needs the heatmap read, and pod check-ins and blockers need the pod reads.
- The answer shall be concise prose plus the IDs of the nodes it rests on, shown as references, and shall never contain raw DM or reply content.
- A question asked while a past day is viewed shall be answered as of that day, and the answer shall keep that date after the viewing date changes.

### 4.24 Issue-Tracker Write-Back

**Business requirement:** A developer's own check-in may move their issues forward in the tracker, but only when the tenant, the role, and the developer all allow it, and every write shall be reversible.

Functional requirements:

- The system shall write back only when three default-deny gates all hold: the tenant switch is on (off by default; set with `OPENPROGRAM_JIRA_WRITEBACK_ENABLED` or `PUT /config/tenant/writeback`), the acting principal holds the issue-tracker write capability, and the developer's consent allows it.
- A developer shall write back only to their own issues, from the claims in their own finalized check-in.
- Consent shall be always ask (the default), auto apply, or never. Always ask shall record a proposal and ask the developer in chat, applying it on yes and recording a decline on no. Auto apply shall write at once. Never shall write nothing.
- A write shall transition the issue to the claimed state and add the developer's note as a comment.
- Every write shall be audited with the issue's prior state, shall be idempotent per issue, target state, and check-in, and shall be revertible once by an admin through the ops API.
- The console shall only read the tenant switch, and shall say whether write-back is on wherever an admin views or sets consent.

### 4.25 Built-In Chat (Local Demo)

**Business requirement:** The whole check-in loop shall be demonstrable on one machine without a connected chat workspace.

Functional requirements:

- The system shall provide a built-in chat provider that stands in for the chat workspace. It shall be enabled by configuration and served only on a local tenant.
- The Chat screen shall show one thread per person, with the bot's check-in question, follow-ups, and nudges, and a composer whose reply travels the same webhook correlation path as real chat, so the parsed status reaches the rollups.
- Everyone shall read and answer their own thread. An admin shall also get the roster, open and write in anyone's thread, request a check-in for that person, and clear the chat history for everyone.
- A message asking someone for a review or input shall offer a Reply action. Replying shall post in that message's thread, as in a real chat workspace, and shall show under the message it answers. It acknowledges the request, or resolves it when the text says the work is done, and is never opened as a check-in or read as a status update. A thread reply under any other message is kept in the conversation and otherwise ignored.
- The same surface shall be available to scripts through test-support endpoints.
- The Chat screen and its nav entry shall be absent when the backend does not serve the built-in chat.

## 5. Key Business Data Flows

### 5.1 Read Sync Flow

1. Admin or scheduler dispatches a sync workflow.
2. Workflow invokes the relevant read sync service.
3. Adapter fetches provider-neutral DTOs from the issue tracker or VCS provider (Jira, GitHub, or GitLab).
4. Sync service upserts graph nodes and edges.
5. Sync service appends immutable facts.
6. Sync service records cursor and sync metadata.
7. Persona and rollup views consume the updated graph/facts.

### 5.2 Developer Check-In Flow

1. The tenant-wide schedule, the reconcile pass, or an admin starts a developer check-in.
2. Status Collector builds context from active issues, facts, and recent conversation.
3. Chat provider sends a direct message with a correlation ID.
4. Webhook receives developer reply.
5. Status Collector resolves the reply to the correct check-in.
6. LLM/parser evaluates whether the reply is status, sufficient, or needs clarification.
7. The system records check-in, conversation, parsed signals, and developer status.
8. Asks of other people become cross-person requests, and issue-state claims go to write-back when it is switched on.
9. Rollup services compute affected aggregate status.
10. Persona APIs and console screens display updated status.

### 5.3 Runtime Configuration Flow

1. Admin creates programs, projects, workstreams, pods, and members.
2. Admin links projects to programs, workstreams to projects, pods to projects and workstreams, and members to pods.
3. Admin optionally imports members from synced directory users.
4. Directory APIs expose configured entities and relationship IDs.
5. Console screens populate selectors and the Delivery navigator from directory APIs.
6. Persona views resolve selected pod/project/program to live rollup and status data.

### 5.4 Portfolio Rollup Flow

1. A viewer opens Today or Delivery, for today or a past viewing date.
2. Persona service loads the program tree for that date.
3. Stored node statuses are used when available.
4. A rollup that is not stored yet is computed for the response only; the rollup schedule stores history.
5. Program tree and heatmap are returned with RAG, source, confidence, and factors.
6. The console renders portfolio heat, the program panel, and the reasons behind each status for drill-oriented review.

## 6. Non-Functional Requirements

- The core domain and application layers shall remain provider-neutral.
- Vendor-specific details shall live in infrastructure adapters, tests, configuration, or documentation, not core business models.
- API DTOs shall be owned at the edge and kept in sync with generated frontend types.
- Authorization shall be centralized and default-deny.
- Raw direct-message content shall not be exposed through persona dashboards or public persona APIs.
- External mutations shall be idempotent where applicable.
- Facts shall be append-only evidence.
- Request handling shall preserve correlation IDs for observability.
- The system shall support fake providers for deterministic local tests.
- Secrets shall not be logged and shall be stored encrypted when persisted.

## 7. Current Phase Boundary

The implemented center of the app is Phase 0 foundation plus much of Phase 1 Sense:

- platform skeleton and hexagonal architecture;
- graph, facts, status, rollup, sync cursor, and conversation persistence seams;
- provider adapters and fakes;
- read-only Jira/GitHub/GitLab sync and live calendar availability checks;
- proactive chat check-ins on one tenant-wide schedule;
- reply parsing, clarification, non-response handling, nudges, escalation, and developer status recording;
- deterministic RAG rollups, stored on a schedule;
- signal-derived risk and drift findings;
- role-scoped persona APIs;
- the React console (Today, Delivery, Signals, Coordination, and the local built-in chat), including viewing a past date;
- narrative briefs, cross-person requests, and Ask the graph;
- admin runtime configuration UI and APIs, including data-source sync status.

Phase 1 does not write to VCS or calendars. Issue-tracker write-back is implemented but off by default: it runs only behind the tenant switch, the write capability, and the developer's own consent (see 4.24). Reconciliation goes as far as the risk and drift rules in 4.19; there is no richer confidence scoring beyond source tags and coarse confidence.

## 8. Roadmap and Planned Features

The product docs describe later phases that are not part of the current implemented core.

### 8.1 Reconcile

Planned requirements:

- Compare developer-stated status against Jira, Git, PR, and CI/CD facts.
- Produce richer confidence scores for human-confirmed versus inferred status.
- ~~Detect drift such as "said done, but no PR merged."~~ Implemented — see 4.19.
- ~~Detect watermelon risk where high-level green hides lower-level red.~~ Implemented — see 4.19.
- ~~Write approved transitions and comments back to Jira.~~ Implemented behind default-off gates — see 4.24.
- Write approved estimates back to Jira.
- ~~Keep every external write auditable and reversible.~~ Implemented for issue-tracker write-back — see 4.24.

### 8.2 Predict

Planned requirements:

- Detect repeated blockers and scope creep.
- ~~Detect stale work.~~ Implemented — see 4.19.
- Forecast sprint, milestone, or release slip probability.
- Detect cross-team dependency stalls.
- Apply smart escalation policies for aging blockers.
- Add advanced visualizations such as flow diagrams, blocker timelines, dependency graphs, and trend sparklines.

### 8.3 Coach

Planned requirements:

- Provide privacy-respecting sentiment and overload signals.
- Suggest load balancing or reassignment options.
- Generate standup summaries and retro inputs.
- Generate stakeholder-ready project updates on demand.
- ~~Support natural-language portfolio queries.~~ Implemented as Ask the graph — see 4.23.
- Maintain decision and blocker memory for future recommendations.

## 9. Explicit Current Out of Scope

- User/profile RBAC administration in the UI. Current member management configures developer graph nodes, while principal roles come from auth settings.
- A web form that replaces the chat check-in. Status collection is chat-based; the console only confirms or corrects a status the system already holds.
- A per-member check-in time. Check-ins go out at one tenant-wide time; a stored per-member time is not used.
- Writes to VCS or calendar providers, and issue-tracker write-back of anything beyond state transitions with a comment.
- Sending narrative briefs to chat.
- CI/CD ingestion as a first-class implemented sync service.
- Predictive delivery risk scoring.
- Burnout, sentiment, or people-health dashboards.
- Free-form answers from outside the graph. Ask the graph (4.23) answers only from the tools the asking role may read.
- Full SSO implementation. The app has an auth seam, a cookie-based OIDC backend-for-frontend, and dev-mode principal support.
