# OpenProgram Gap Register

This file tracks the product and adoption gaps identified during the need and adoption review.

## Summary

OpenProgram has a meaningful MVP foundation: graph-backed delivery state, chat check-ins, rollups, persona dashboards, admin configuration, read-only integrations, workflow dispatch, and local/demo tooling.

The remaining gaps are concentrated in the areas that would make the product clearly differentiated from Jira dashboards, Slack standup bots, manual status chasing, or incumbent AI features inside existing work-management suites.

## Strongest Differentiator Gaps

### 1. Hard-Signal Reconciliation

**Current gap:** OpenProgram does not yet fully reconcile developer-stated status against hard delivery evidence.

**Why it matters:** A basic status bot records what a developer said. A differentiated delivery-intelligence product should detect when human status and system evidence disagree.

Example:

- Developer says: "Task is basically done."
- Jira still says: `In Progress`.
- GitHub shows no pull request.
- CI has no passing build.
- Calendar shows the developer is out tomorrow.

The stronger OpenProgram behavior would be:

> Human status says nearly done, but delivery evidence is weak: no PR, no merged code, and Jira is unchanged. Confidence is low. Follow-up recommended.

**Adoption impact:** Without this, OpenProgram risks looking like another async standup/status dashboard. With it, OpenProgram becomes an evidence-backed delivery truth layer.

### 2. Issue-Tracker Write-Back

**Current gap:** Issue tracker write-back is not implemented in the current phase.

**Why it matters:** If a developer answers in chat but still has to update Jira manually, the product reduces some status-chasing pain but does not remove double entry.

Useful write-back behaviors would include:

- Transitioning an issue when the developer confirms progress.
- Adding a Jira comment from an approved check-in summary.
- Updating remaining estimate or ETA.
- Creating or linking blocker records.
- Creating follow-up work or dependency requests.

**Adoption impact:** Write-back is a major developer-value feature. Without it, OpenProgram mainly helps managers and scrum masters. With it, developers get direct time savings.

### 3. Predictive Delivery Risk

**Current gap:** Predictive delivery scoring is not fully implemented.

**Why it matters:** Reporting current status is useful, but managers and executives adopt new tools when they can act earlier than they could before.

Examples of valuable prediction:

- A release is likely to slip because critical-path work has no PR activity.
- A workstream has too many stale items.
- Blockers are aging faster than they are being resolved.
- Scope increased mid-sprint without matching capacity.
- A project is green at the rollup level but red at the task or developer level.

**Adoption impact:** Prediction is one of the strongest executive and manager hooks. Without it, the product is mostly a better reporting layer.

### 4. Coaching and Action Guidance

**Current gap:** Coaching is not complete as a product capability.

**Why it matters:** A high-value agent should help teams decide what to do next, not only display what happened.

Examples:

- Prompting a developer to clarify vague status.
- Nudging the right dependency owner.
- Suggesting escalation when a blocker crosses an age threshold.
- Generating standup and retro notes from evidence.
- Recommending load balancing when work is uneven.
- Highlighting the few actions a scrum master should take today.

**Adoption impact:** Coaching turns OpenProgram from a dashboard into an operating assistant. This is important because dashboards alone are easy for incumbents to copy.

## Current Listed Gaps

### No First-Class CI/CD Ingestion

CI/CD signals are essential for hard-signal reconciliation. Pull request, build, deployment, and test-failure evidence can prove or contradict reported status.

Until CI/CD ingestion is first-class, OpenProgram's confidence scoring and risk detection remain incomplete.

### No Burnout, Sentiment, or People-Health Capability

People-health features are listed as future capabilities, not current production-ready behavior.

This area should be handled carefully because it can create surveillance concerns. Any future implementation should focus on aggregate, privacy-respecting signals and avoid individual performance scoring.

### No Complete Natural-Language Query Product

The broader "ask anything" experience is not yet a complete product capability.

The useful version would let authorized users ask questions such as:

- Which projects are at risk because of a dependency?
- Which blockers are older than five days?
- Which workstreams have no recent PR activity?
- Why is this program amber?

The answer should include evidence, references, and authorization-safe detail.

### No Full SSO

The current product has an auth seam and role model, but full enterprise SSO is not implemented.

This is a serious adoption gap for real organizations because status, raw replies, role-scoped dashboards, and management rollups all depend on trusted identity and access control.

### Broader Backend Verification Pending

Focused tests have been run in prior implementation work, but broader backend verification and manual smoke coverage were still listed as pending.

Before production-style adoption, the project needs a clear verification baseline:

- Full backend test suite.
- Lint and type checks.
- OpenAPI/client drift check.
- Frontend typecheck and build.
- Container-backed integration checks where applicable.
- Manual smoke from empty configuration through admin setup and dashboard population.

### Manual Smoke Pending

The product needs a reliable end-to-end operator smoke:

1. Start an empty backend.
2. Configure programs, projects, pods, members, and check-in preferences through Admin.
3. Dispatch a check-in.
4. Submit or simulate a reply.
5. Confirm status, rollups, dashboards, risks, and portfolio views update as expected.

This matters because adoption depends on the complete workflow feeling coherent, not only on individual APIs passing tests.

## Trust and Adoption Gaps

### Privacy and Surveillance Risk

The risk is not that status collection is new. Manual standups, Jira comments, Slack pings, and PM spreadsheets existed before GenAI.

The risk is that automation changes the scale and behavior:

- It can ask everyone every day.
- It can persist raw replies and conversation history.
- It can infer blockers, confidence, mood, or risk.
- It can expose low-level signals to wider audiences.
- It can create a chilling effect if developers believe casual replies are being scored.

Mitigations needed:

- Redact logs and traces by default.
- Store structured status by default, not raw text forever.
- Use short retention for raw conversation turns.
- Make raw reply access highly restricted or unavailable.
- Let developers see and correct what was inferred.
- Avoid individual sentiment or burnout scoring.
- Position the product as reducing status toil, not monitoring developers.

### Incumbent Competition

The product competes against several categories:

- Jira and Atlassian AI features.
- Asana and other work-management AI.
- Microsoft Planner/Copilot project agents.
- Slack or Teams standup bots.
- Existing Jira dashboards, reports, and automation.
- Manual scrum-master or TPM workflows.

To win adoption, OpenProgram needs to avoid the generic "AI project status" lane and focus on a sharper wedge:

> Privacy-safe, evidence-backed engineering delivery truth across Jira, Git, CI, and chat, with explainable rollups and action guidance.

## Recommended Priority Order

1. Fix privacy foundations: redaction, retention, raw-message access, and developer-visible inference review.
2. Complete hard-signal reconciliation across Jira, Git, and CI/CD.
3. Add approved issue-tracker write-back to remove developer double entry.
4. Strengthen SSO/RBAC for real enterprise use.
5. Build a complete end-to-end smoke path from empty configuration to dashboards.
6. Add prediction and coaching only after the evidence layer is trusted.

