# OpenProgram Features and Business Requirements

## 1. Product Summary

OpenProgram is an agentic program-management system for continuously collecting, reconciling, rolling up, and presenting delivery status across software organizations.

The application replaces manual status chasing and meeting-driven reporting with:

- a graph-backed source of truth for programs, projects, pods, developers, and tasks;
- proactive developer check-ins through chat;
- read-only ingestion from delivery systems such as Jira, GitHub, and calendar providers;
- deterministic RAG rollups from developer/task level to pod, project, program, and portfolio level;
- role-specific dashboards for developers, scrum masters, product owners, managers, executives, and admins;
- runtime configuration screens for managing the organization hierarchy and check-in timing.

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
| Developer | Know current focus, blockers, tasks, and check-in expectations. | Dev dashboard, proactive chat check-ins, own check-in preference API, focus list. |
| Scrum Master | Track team check-in completeness and active blockers. | SM dashboard, pod selector, blocker board, pod check-in completeness, pod detail pages. |
| Product Owner | Track project progress, task health, and delivery risk indicators. | PO dashboard, project selector, project progress rollup, task breakdown, project detail pages. |
| Manager | Review team/program health across multiple delivery layers. | Program tree, aggregate rollups, portfolio heatmap capability. |
| Executive | View portfolio health at a glance without raw developer-message access. | Executive dashboard, portfolio page, RAG heatmap, hierarchy tree. |
| Admin | Configure hierarchy, members, links, assignments, check-ins, and workflow dispatch. | Admin config UI, config APIs, directory sync/import, workflow dispatch APIs. |

## 4. Current Implemented Feature Set

### 4.1 Graph of Truth

**Business requirement:** The system shall maintain a single canonical graph of delivery entities so status can be rolled up and drilled down consistently.

Functional requirements:

- The system shall model programs, projects, pods, developers, tasks, repos, and sprints as graph nodes.
- The system shall model hierarchy and assignment relationships with typed edges such as `CONTAINS`, `ASSIGNED_TO`, and `DEPENDS_ON`.
- The system shall support program -> project -> pod -> developer -> task drill paths.
- The system shall support matrixed relationships, including pods linked to multiple projects and developers linked through pod membership.
- The system shall store source facts as append-only evidence with timestamps, source identifiers, entity references, payloads, and correlation IDs.
- The system shall expose directory views for configured programs, projects, and pods, including relationship IDs and latest rollup status where available.

### 4.2 Runtime Configuration Management

**Business requirement:** Admins shall configure the business hierarchy at runtime instead of depending on hardcoded seed data.

Functional requirements:

- Admins shall create, view, update, and delete programs.
- Admins shall create, view, update, and delete projects.
- Admins shall create, view, update, and delete pods.
- Admins shall create, view, update, and delete members/developers.
- Admins shall link and unlink projects to programs.
- Admins shall link and unlink pods to projects.
- Admins shall link and unlink members to pods with a role-in-pod label.
- Admins shall assign and unassign tasks to members.
- Admins shall search synced directory users.
- Admins shall add selected directory users as configured members.
- Admins shall trigger directory sync and see synced/deactivated counts through API responses.
- The system shall reject invalid configuration mutations such as duplicate links, self-links, wrong node kinds, missing references, and conflicting IDs.

### 4.3 Check-In Preference Management

**Business requirement:** Check-ins shall respect member-level timing preferences.

Functional requirements:

- A developer shall retrieve and update their own check-in preference.
- Admins shall view check-in preferences for all configured members.
- Admins shall update a member's local check-in time.
- Admins shall update a member's timezone.
- Admins shall choose active weekdays for check-ins.
- Admins shall configure reply wait and final reply wait windows.
- The system shall fall back to tenant-level defaults when a member-specific preference does not exist.

### 4.4 Daily Status Collector

**Business requirement:** The system shall proactively collect status from developers through chat and convert free-text replies into structured delivery signals.

Functional requirements:

- The system shall dispatch developer check-ins through the workflow layer.
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

### 4.5 Non-Response and Nudge Handling

**Business requirement:** Missing replies shall be visible and shall not silently become green status.

Functional requirements:

- The system shall send a follow-up nudge for a pending check-in.
- The system shall refuse to nudge a check-in that already has a reply.
- The system shall record stale or unknown status when the developer does not respond within configured windows.
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

**Business requirement:** The system shall ingest hard delivery signals without writing back to source systems in the current phase.

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
- The system shall sync calendar events as developer facts for availability/PTO-style signals.
- The system shall store provider-neutral sync cursors for incremental syncs.
- The system shall record sync metadata such as last checked time and item count.

### 4.8 Rollup Engine

**Business requirement:** The system shall compute explainable health status across the delivery hierarchy.

Functional requirements:

- The system shall compute RAG values: green, amber, red, and unknown.
- The system shall calculate developer status from confirmed, stale, inferred, unknown, and blocker signals.
- The system shall escalate status when blockers exist.
- The system shall escalate a single blocker on a critical-path task.
- The system shall escalate multiple blockers to red.
- The system shall keep stale or inferred status out of green.
- The system shall aggregate child statuses into pod, project, and program status.
- The system shall record rollup factors explaining why a node has a given RAG value.
- The system shall persist computed node statuses when a view needs rollups that are not already stored.
- The system shall keep task nodes as source items rather than rollup nodes.

### 4.9 Persona Dashboards

**Business requirement:** Each user role shall see a focused dashboard for the decisions they own.

Functional requirements:

- The application shall provide a role switcher in the frontend shell for demo/local role-based navigation.
- The application shall persist the active UI role in local storage.
- The application shall show health, environment, tenant, as-of date, and refresh controls on persona dashboards.
- The application shall support as-of date filtering for status views.
- The application shall dynamically populate pod, project, and program selectors from runtime directory data.
- The developer dashboard shall show developer name, summary, active focus items, blockers, assigned tasks, task RAG, status source, and confidence where present.
- The scrum master dashboard shall show pod check-in counts, confirmed/stale/missing developers, and an open blocker board with owner, source, and age.
- The product owner dashboard shall show project progress percentage, task counts by RAG, total tasks, task list, source, and confidence.
- The manager/executive dashboard shall show a program hierarchy tree and portfolio heatmap.
- Dashboard requests shall show appropriate loading, unavailable, and unauthorized states.

### 4.10 Pods and Projects Directory

**Business requirement:** Users shall browse configured delivery structures and drill into detail pages.

Functional requirements:

- The Pods page shall list configured pods with ID, name, description, RAG badge, member count, and linked project count.
- The Pod detail page shall show pod metadata, member/project/blocker stats, related projects, check-in completeness, developer summaries, and open blockers.
- The Projects page shall list configured projects with ID/code, name, description, RAG badge, linked pod count, and linked program count.
- The Project detail page shall show progress, done/at-risk/blocked counts, associated pods, rollup source, total task count, and task breakdown.
- Detail pages shall support as-of date filtering and manual refresh.

### 4.11 Portfolio View

**Business requirement:** Executives and managers shall review program-level health from a portfolio view.

Functional requirements:

- The Portfolio page shall list configured programs in a selector.
- The Portfolio page shall support as-of date filtering and manual refresh.
- The Portfolio page shall render a program tree for the selected program.
- The Portfolio page shall render a heatmap of rollup cells by entity kind and entity ID.
- The portfolio heatmap shall use an explicit program root when selected.
- The portfolio heatmap shall fall back to the first configured program when no root is supplied.
- The portfolio view shall return an empty state when no programs are configured.

### 4.12 Role-Based Authorization

**Business requirement:** Access to capabilities and sensitive fields shall be explicit and default-deny.

Functional requirements:

- The system shall define roles for developer, product owner, scrum master, manager, executive, and admin.
- The system shall check capabilities centrally in the authorization policy.
- Developers shall read their own work and directory data.
- Scrum masters shall read directory data, team aggregates, pod blockers, pod check-ins, and raw DM content where allowed.
- Product owners shall read directory data, team aggregates, and project progress.
- Managers shall read directory data, team aggregates, executive aggregates, project progress, program rollups, and portfolio heatmaps.
- Executives shall read directory data, executive aggregates, program rollups, and portfolio heatmaps.
- Admins shall manage configuration and dispatch workflows.
- Admin role shall short-circuit to allowed for capabilities.
- Raw DM content and budget-like sensitive fields shall be protected by field-level checks.
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

- The system shall provide a chat provider port and fake/Slack implementations.
- The system shall provide a chat webhook mapper and fake/Slack implementations.
- The system shall provide an issue tracker port and fake/Jira implementations.
- The system shall provide a VCS port and fake/GitHub implementations.
- The system shall provide a calendar provider port and fake/Google Calendar implementations.
- The system shall provide a directory provider and fake/Slack directory implementations.
- The system shall provide an LLM provider and fake/LiteLLM implementations.
- The system shall provide workflow scheduler/worker adapters for fake, DBOS, and Temporal modes.
- The system shall use Redis for rate limiting and chat conversation caching when not in memory mode.
- The system shall use encrypted secret storage for connector credentials.

### 4.16 Observability, Health, and Readiness

**Business requirement:** Operators shall be able to verify health and trace requests across the stack.

Functional requirements:

- The API shall expose `/health` with status, environment, tenant ID, and correlation ID.
- The API shall expose `/ready` with dependency readiness checks.
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

**Business requirement:** Users shall access dashboards and admin workflows through a typed React frontend.

Functional requirements:

- The frontend shall use a typed API client generated from OpenAPI.
- The frontend shall send a unique correlation ID header with API requests.
- The frontend shall provide sidebar navigation for Dev, SM, PO, Exec dashboards, Pods, Projects, Portfolio, and Admin Config.
- The frontend shall hide Portfolio navigation unless the active UI role is executive or admin.
- The frontend shall hide Admin Config navigation unless the active UI role is admin.
- The frontend shall provide reusable UI primitives for buttons, badges, dialogs, fields, inputs, selects, sliders, textareas, and role-aware layouts.

## 5. Key Business Data Flows

### 5.1 Read Sync Flow

1. Admin or scheduler dispatches a sync workflow.
2. Workflow invokes the relevant read sync service.
3. Adapter fetches provider-neutral DTOs from Jira, GitHub, or calendar provider.
4. Sync service upserts graph nodes and edges.
5. Sync service appends immutable facts.
6. Sync service records cursor and sync metadata.
7. Persona and rollup views consume the updated graph/facts.

### 5.2 Developer Check-In Flow

1. Workflow dispatch starts a developer check-in.
2. Status Collector builds context from active issues, facts, and recent conversation.
3. Chat provider sends a direct message with a correlation ID.
4. Webhook receives developer reply.
5. Status Collector resolves the reply to the correct check-in.
6. LLM/parser evaluates whether the reply is status, sufficient, or needs clarification.
7. The system records check-in, conversation, parsed signals, and developer status.
8. Rollup services compute affected aggregate status.
9. Persona APIs and dashboards display updated status.

### 5.3 Runtime Configuration Flow

1. Admin creates programs, projects, pods, and members.
2. Admin links projects to programs, pods to projects, and members to pods.
3. Admin optionally imports members from synced directory users.
4. Directory APIs expose configured entities and relationship IDs.
5. Frontend dashboards populate selectors from directory APIs.
6. Persona views resolve selected pod/project/program to live rollup and status data.

### 5.4 Portfolio Rollup Flow

1. User selects a program and as-of date.
2. Persona service loads the program tree.
3. Existing node statuses are used when available.
4. Missing rollups are computed and recorded.
5. Program tree and heatmap are returned with RAG, source, confidence, and factors.
6. UI renders hierarchy and heatmap cells for drill-oriented review.

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
- read-only Jira/GitHub/calendar sync;
- proactive chat check-ins;
- reply parsing, clarification, non-response handling, nudges, and developer status recording;
- deterministic RAG rollups;
- role-scoped persona APIs;
- React dashboards and directory pages;
- admin runtime configuration UI and APIs.

Phase 1 explicitly does not write back to issue trackers, VCS, or calendars. It also does not perform full hard-signal reconciliation beyond source tags and coarse confidence/source information.

## 8. Roadmap and Planned Features

The product docs describe later phases that are not part of the current implemented core.

### 8.1 Reconcile

Planned requirements:

- Compare developer-stated status against Jira, Git, PR, and CI/CD facts.
- Produce richer confidence scores for human-confirmed versus inferred status.
- Detect drift such as "said done, but no PR merged."
- Detect watermelon risk where high-level green hides lower-level red.
- Write approved comments, transitions, or estimates back to Jira.
- Keep every external write auditable and reversible.

### 8.2 Predict

Planned requirements:

- Detect repeated blockers, stale work, and scope creep.
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
- Support natural-language portfolio queries.
- Maintain decision and blocker memory for future recommendations.

## 9. Explicit Current Out of Scope

- User/profile RBAC administration in the UI. Current member management configures developer graph nodes, while principal roles come from auth settings.
- Web form based daily check-in submission. Current status collection is chat-based.
- Issue tracker write-back.
- CI/CD ingestion as a first-class implemented sync service.
- Predictive delivery risk scoring.
- Burnout, sentiment, or people-health dashboards.
- Ask-anything natural-language query.
- Full SSO implementation. The app has an auth seam and dev-mode principal support.
