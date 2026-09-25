"""The demo tenant's people, hierarchy, and work — one editable source of truth.

Shared by ``seed_demo_history`` (which writes it to Postgres) and kept in step
with the mock chat/directory adapters, whose ``U10xx`` ids are the chat external
ids the demo logs in as. A developer node id IS the chat id here, which is what
lets the console act as any of these people without an identity-link step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Person:
    id: str
    name: str
    handle: str
    email: str
    title: str
    # App roles drive both the persona picker and what each screen will show.
    roles: tuple[str, ...]
    pod_ids: tuple[str, ...] = ()
    pod_role: str = "developer"
    timezone: str = "Europe/Berlin"
    # Rough weekday reply reliability, 0..1. Drives how often a seeded day is a
    # confirmed check-in versus a silent (unknown) one, so "silence is never
    # green" is visible in the rollups.
    reply_rate: float = 0.9
    # The most recent day is scripted rather than sampled, so the screen the
    # demo opens on is the same every time: most people confirmed, these two
    # silent, which is what makes the "unknown, not green" dot explainable.
    silent_today: bool = False


@dataclass(frozen=True, kw_only=True)
class Pod:
    id: str
    name: str
    project_id: str
    workstream_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Workstream:
    id: str
    name: str
    project_id: str
    repos: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Project:
    id: str
    name: str
    code: str
    jira_project_key: str
    repos: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class WorkItemSpec:
    id: str
    name: str
    workstream_id: str
    owner_id: str
    item_type: str = "feature"
    # Final state at the end of the seeded window.
    state: str = "in_progress"
    repo: str | None = None
    # Days before "today" the item entered its first active state.
    started_days_ago: int = 20
    # Days before "today" of the last state change; drives staleness risk.
    last_change_days_ago: int = 2
    pr_id: str | None = None
    pr_days_ago: int | None = None
    pr_merged: bool = False


@dataclass(frozen=True, kw_only=True)
class TaskSpec:
    """A per-developer task node — what the Today screen ranks as focus."""

    id: str
    name: str
    owner_id: str
    workstream_id: str
    # The work item this task implements. Every work item needs at least one,
    # otherwise it aggregates from nothing and reads unknown.
    work_item_id: str
    rag: str = "green"
    deadline_days_ahead: int | None = None
    confidence: float = 0.8


@dataclass(frozen=True, kw_only=True)
class BlockerSpec:
    """A blocker that lives across days, so blocker age is real in the demo."""

    developer_id: str
    description: str
    work_item_id: str | None = None
    pod_id: str | None = None
    first_day_offset: int = -18
    resolved_day_offset: int | None = None


PROGRAM_ID = "program-platform"
PROGRAM_NAME = "Digital Platform Program"

PROJECTS: tuple[Project, ...] = (
    Project(
        id="project-checkout",
        name="Checkout Revamp",
        code="CHK",
        jira_project_key="CHK",
        repos=("acme/checkout-api", "acme/storefront-web"),
    ),
    Project(
        id="project-identity",
        name="Identity Platform",
        code="IDP",
        jira_project_key="IDP",
        repos=("acme/identity-service", "acme/sso-gateway"),
    ),
    Project(
        id="project-insights",
        name="Customer Insights",
        code="INS",
        jira_project_key="INS",
        repos=("acme/insights-pipeline",),
    ),
)

WORKSTREAMS: tuple[Workstream, ...] = (
    Workstream(
        id="ws-payments",
        name="Payments API",
        project_id="project-checkout",
        repos=("acme/checkout-api",),
    ),
    Workstream(
        id="ws-cart",
        name="Cart & Pricing",
        project_id="project-checkout",
        repos=("acme/storefront-web",),
    ),
    Workstream(
        id="ws-login",
        name="Login Experience",
        project_id="project-identity",
        repos=("acme/identity-service",),
    ),
    Workstream(
        id="ws-sso",
        name="Enterprise SSO",
        project_id="project-identity",
        repos=("acme/sso-gateway",),
    ),
    Workstream(
        id="ws-datalake",
        name="Data Lake Ingest",
        project_id="project-insights",
        repos=("acme/insights-pipeline",),
    ),
)

PODS: tuple[Pod, ...] = (
    Pod(
        id="pod-payments",
        name="Payments Pod",
        project_id="project-checkout",
        workstream_ids=("ws-payments",),
    ),
    Pod(
        id="pod-storefront",
        name="Storefront Pod",
        project_id="project-checkout",
        workstream_ids=("ws-cart",),
    ),
    Pod(
        id="pod-identity",
        name="Identity Pod",
        project_id="project-identity",
        workstream_ids=("ws-login", "ws-sso"),
    ),
    Pod(
        id="pod-data",
        name="Data Pod",
        project_id="project-insights",
        workstream_ids=("ws-datalake",),
    ),
)

PEOPLE: tuple[Person, ...] = (
    Person(
        id="U1011",
        name="Elena Fischer",
        handle="elena",
        email="elena.fischer@example.com",
        title="Director of Engineering",
        roles=("exec",),
        reply_rate=0.5,
        silent_today=True,
    ),
    Person(
        id="U1001",
        name="Asha Rao",
        handle="asha",
        email="asha.rao@example.com",
        title="Engineering Manager",
        roles=("mgr", "admin"),
        pod_ids=("pod-payments", "pod-storefront"),
        pod_role="manager",
        reply_rate=0.8,
    ),
    Person(
        id="U1003",
        name="Mina Patel",
        handle="mina",
        email="mina.patel@example.com",
        title="Product Owner",
        roles=("po",),
        pod_ids=("pod-payments", "pod-storefront"),
        pod_role="product_owner",
        reply_rate=0.75,
    ),
    Person(
        id="U1013",
        name="Hana Kobayashi",
        handle="hana",
        email="hana.kobayashi@example.com",
        title="Product Owner",
        roles=("po",),
        pod_ids=("pod-identity", "pod-data"),
        pod_role="product_owner",
        timezone="Asia/Tokyo",
        reply_rate=0.8,
    ),
    Person(
        id="U1006",
        name="Ira Novak",
        handle="ira",
        email="ira.novak@example.com",
        title="Scrum Master",
        roles=("sm",),
        pod_ids=("pod-payments", "pod-identity"),
        pod_role="scrum_master",
        reply_rate=0.9,
    ),
    Person(
        id="U1014",
        name="Ben Sorensen",
        handle="ben",
        email="ben.sorensen@example.com",
        title="Scrum Master",
        roles=("sm",),
        pod_ids=("pod-storefront", "pod-data"),
        pod_role="scrum_master",
        reply_rate=0.85,
    ),
    Person(
        id="U1002",
        name="Liam Chen",
        handle="liam",
        email="liam.chen@example.com",
        title="Platform Engineer",
        roles=("dev",),
        pod_ids=("pod-payments",),
        reply_rate=0.95,
    ),
    Person(
        id="U1004",
        name="Noah Weber",
        handle="noah",
        email="noah.weber@example.com",
        title="Senior Backend Engineer",
        roles=("dev",),
        pod_ids=("pod-payments",),
        reply_rate=0.9,
    ),
    Person(
        id="U1007",
        name="Kai Thompson",
        handle="kai",
        email="kai.thompson@example.com",
        title="Backend Engineer",
        roles=("dev",),
        pod_ids=("pod-payments",),
        # The quiet one: drives the "silence is never green" story.
        reply_rate=0.45,
        silent_today=True,
    ),
    Person(
        id="U1005",
        name="Zoe Almeida",
        handle="zoe",
        email="zoe.almeida@example.com",
        title="Frontend Engineer",
        roles=("dev",),
        pod_ids=("pod-storefront",),
        reply_rate=0.92,
    ),
    Person(
        id="U1012",
        name="Tom Okafor",
        handle="tom",
        email="tom.okafor@example.com",
        title="Frontend Engineer",
        roles=("dev",),
        pod_ids=("pod-storefront",),
        reply_rate=0.85,
    ),
    Person(
        id="U1009",
        name="Sofia Bergmann",
        handle="sofia",
        email="sofia.bergmann@example.com",
        title="QA Engineer",
        roles=("dev",),
        pod_ids=("pod-identity",),
        reply_rate=0.88,
    ),
    Person(
        id="U1008",
        name="Omar Haddad",
        handle="omar",
        email="omar.haddad@example.com",
        title="SRE",
        roles=("dev",),
        pod_ids=("pod-identity",),
        reply_rate=0.7,
    ),
    Person(
        id="U1010",
        name="Raj Iyer",
        handle="raj",
        email="raj.iyer@example.com",
        title="Data Engineer",
        roles=("dev",),
        pod_ids=("pod-data",),
        reply_rate=0.8,
    ),
)

WORK_ITEMS: tuple[WorkItemSpec, ...] = (
    # --- Payments: the workstream that goes red ---------------------------
    WorkItemSpec(
        id="CHK-101",
        name="Payment intent API",
        workstream_id="ws-payments",
        owner_id="U1002",
        state="in_review",
        repo="acme/checkout-api",
        started_days_ago=26,
        last_change_days_ago=3,
        pr_id="418",
        pr_days_ago=11,
    ),
    WorkItemSpec(
        id="CHK-102",
        name="Refund edge cases",
        workstream_id="ws-payments",
        owner_id="U1004",
        state="in_progress",
        repo="acme/checkout-api",
        started_days_ago=24,
        # No state change in three weeks and no PR: stale + feature-no-PR risk.
        last_change_days_ago=19,
    ),
    WorkItemSpec(
        id="CHK-103",
        name="3-D Secure step-up",
        workstream_id="ws-payments",
        owner_id="U1007",
        state="in_progress",
        repo="acme/checkout-api",
        started_days_ago=17,
        last_change_days_ago=13,
    ),
    WorkItemSpec(
        id="CHK-104",
        name="Idempotent capture retries",
        workstream_id="ws-payments",
        owner_id="U1002",
        state="done",
        repo="acme/checkout-api",
        started_days_ago=28,
        last_change_days_ago=9,
        pr_id="402",
        pr_days_ago=12,
        pr_merged=True,
    ),
    # --- Storefront: mostly healthy --------------------------------------
    WorkItemSpec(
        id="CHK-201",
        name="Cart price breakdown",
        workstream_id="ws-cart",
        owner_id="U1005",
        state="done",
        repo="acme/storefront-web",
        started_days_ago=25,
        last_change_days_ago=6,
        pr_id="911",
        pr_days_ago=8,
        pr_merged=True,
    ),
    WorkItemSpec(
        id="CHK-202",
        name="Promo code validation",
        workstream_id="ws-cart",
        owner_id="U1012",
        state="in_review",
        repo="acme/storefront-web",
        started_days_ago=15,
        last_change_days_ago=0,
        pr_id="927",
        pr_days_ago=0,
    ),
    WorkItemSpec(
        id="CHK-203",
        name="Guest checkout banner",
        workstream_id="ws-cart",
        owner_id="U1005",
        state="in_progress",
        repo="acme/storefront-web",
        started_days_ago=7,
        last_change_days_ago=0,
    ),
    WorkItemSpec(
        id="CHK-204",
        name="Mini-cart accessibility pass",
        workstream_id="ws-cart",
        owner_id="U1012",
        item_type="chore",
        state="proposed",
        started_days_ago=4,
        last_change_days_ago=4,
    ),
    # --- Identity: an aging PR -------------------------------------------
    WorkItemSpec(
        id="IDP-301",
        name="Passkey enrolment",
        workstream_id="ws-login",
        owner_id="U1009",
        state="in_review",
        repo="acme/identity-service",
        started_days_ago=22,
        last_change_days_ago=5,
        # Open for three weeks: PR-age risk.
        pr_id="145",
        pr_days_ago=21,
    ),
    WorkItemSpec(
        id="IDP-302",
        name="Session revocation endpoint",
        workstream_id="ws-login",
        owner_id="U1008",
        state="done",
        repo="acme/identity-service",
        started_days_ago=20,
        last_change_days_ago=10,
        pr_id="138",
        pr_days_ago=13,
        pr_merged=True,
    ),
    WorkItemSpec(
        id="IDP-401",
        name="SAML metadata refresh",
        workstream_id="ws-sso",
        owner_id="U1008",
        state="in_progress",
        repo="acme/sso-gateway",
        started_days_ago=16,
        last_change_days_ago=11,
    ),
    WorkItemSpec(
        id="IDP-402",
        name="SCIM user deprovisioning",
        workstream_id="ws-sso",
        owner_id="U1009",
        item_type="feature",
        state="proposed",
        started_days_ago=5,
        last_change_days_ago=5,
    ),
    # --- Insights ---------------------------------------------------------
    WorkItemSpec(
        id="INS-501",
        name="Event ingest backfill",
        workstream_id="ws-datalake",
        owner_id="U1010",
        state="in_progress",
        repo="acme/insights-pipeline",
        started_days_ago=19,
        last_change_days_ago=1,
        pr_id="77",
        pr_days_ago=1,
    ),
    WorkItemSpec(
        id="INS-502",
        name="Attribution model v2",
        workstream_id="ws-datalake",
        owner_id="U1010",
        state="in_progress",
        repo="acme/insights-pipeline",
        started_days_ago=21,
        last_change_days_ago=16,
    ),
)

TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        id="task-chk-101",
        name="Ship payment intent API review fixes",
        owner_id="U1002",
        workstream_id="ws-payments",
        work_item_id="CHK-101",
        rag="amber",
        deadline_days_ahead=3,
        confidence=0.6,
    ),
    TaskSpec(
        id="task-chk-104",
        name="Capture retry runbook",
        owner_id="U1002",
        workstream_id="ws-payments",
        work_item_id="CHK-104",
        rag="green",
        confidence=0.9,
    ),
    TaskSpec(
        id="task-chk-102",
        name="Refund edge cases: settle scope with finance",
        owner_id="U1004",
        workstream_id="ws-payments",
        work_item_id="CHK-102",
        rag="red",
        deadline_days_ahead=1,
        confidence=0.35,
    ),
    TaskSpec(
        id="task-chk-103",
        name="3-D Secure step-up flow",
        owner_id="U1007",
        workstream_id="ws-payments",
        work_item_id="CHK-103",
        rag="amber",
        deadline_days_ahead=6,
        confidence=0.5,
    ),
    TaskSpec(
        id="task-chk-201",
        name="Cart price breakdown polish",
        owner_id="U1005",
        workstream_id="ws-cart",
        work_item_id="CHK-201",
        rag="green",
        confidence=0.9,
    ),
    TaskSpec(
        id="task-chk-203",
        name="Guest checkout banner",
        owner_id="U1005",
        workstream_id="ws-cart",
        work_item_id="CHK-203",
        rag="green",
        deadline_days_ahead=8,
        confidence=0.85,
    ),
    TaskSpec(
        id="task-chk-202",
        name="Promo code validation review",
        owner_id="U1012",
        workstream_id="ws-cart",
        work_item_id="CHK-202",
        rag="green",
        deadline_days_ahead=4,
        confidence=0.8,
    ),
    TaskSpec(
        id="task-idp-301",
        name="Passkey enrolment: land review",
        owner_id="U1009",
        workstream_id="ws-login",
        work_item_id="IDP-301",
        rag="amber",
        deadline_days_ahead=2,
        confidence=0.55,
    ),
    TaskSpec(
        id="task-idp-401",
        name="SAML metadata refresh",
        owner_id="U1008",
        workstream_id="ws-sso",
        work_item_id="IDP-401",
        rag="amber",
        deadline_days_ahead=5,
        confidence=0.6,
    ),
    TaskSpec(
        id="task-ins-501",
        name="Event ingest backfill",
        owner_id="U1010",
        workstream_id="ws-datalake",
        work_item_id="INS-501",
        rag="green",
        deadline_days_ahead=7,
        confidence=0.8,
    ),
    TaskSpec(
        id="task-ins-502",
        name="Attribution model v2 spike",
        owner_id="U1010",
        workstream_id="ws-datalake",
        work_item_id="INS-502",
        rag="amber",
        deadline_days_ahead=9,
        confidence=0.5,
    ),
    TaskSpec(
        id="task-chk-204",
        name="Mini-cart accessibility audit",
        owner_id="U1012",
        workstream_id="ws-cart",
        work_item_id="CHK-204",
        rag="green",
        confidence=0.75,
    ),
    TaskSpec(
        id="task-idp-302",
        name="Session revocation endpoint",
        owner_id="U1008",
        workstream_id="ws-login",
        work_item_id="IDP-302",
        rag="green",
        confidence=0.95,
    ),
    TaskSpec(
        id="task-idp-402",
        name="SCIM deprovisioning design",
        owner_id="U1009",
        workstream_id="ws-sso",
        work_item_id="IDP-402",
        rag="green",
        confidence=0.7,
    ),
)

BLOCKERS: tuple[BlockerSpec, ...] = (
    BlockerSpec(
        developer_id="U1004",
        description="Waiting on finance to confirm partial-refund rounding rules",
        work_item_id="CHK-102",
        pod_id="pod-payments",
        first_day_offset=-19,
    ),
    BlockerSpec(
        developer_id="U1007",
        description="3-D Secure sandbox credentials still not provisioned",
        work_item_id="CHK-103",
        pod_id="pod-payments",
        first_day_offset=-13,
    ),
    BlockerSpec(
        developer_id="U1002",
        description="Payment intent PR needs a second reviewer",
        work_item_id="CHK-101",
        pod_id="pod-payments",
        first_day_offset=-6,
    ),
    BlockerSpec(
        developer_id="U1009",
        description="Passkey review blocked on security sign-off",
        work_item_id="IDP-301",
        pod_id="pod-identity",
        first_day_offset=-9,
    ),
    BlockerSpec(
        developer_id="U1008",
        description="SSO staging tenant keeps losing its SAML certificate",
        work_item_id="IDP-401",
        pod_id="pod-identity",
        first_day_offset=-11,
    ),
    BlockerSpec(
        developer_id="U1010",
        description="Attribution spec ambiguous for multi-touch sessions",
        work_item_id="INS-502",
        pod_id="pod-data",
        first_day_offset=-16,
    ),
    # Resolved mid-window, so the demo shows blockers clearing too.
    BlockerSpec(
        developer_id="U1005",
        description="Design tokens missing for the price breakdown",
        work_item_id="CHK-201",
        pod_id="pod-storefront",
        first_day_offset=-22,
        resolved_day_offset=-12,
    ),
    BlockerSpec(
        developer_id="U1012",
        description="Promo service returning 500s in staging",
        work_item_id="CHK-202",
        pod_id="pod-storefront",
        first_day_offset=-14,
        resolved_day_offset=-4,
    ),
    BlockerSpec(
        developer_id="U1010",
        description="Ingest backfill throttled by warehouse quota",
        work_item_id="INS-501",
        pod_id="pod-data",
        first_day_offset=-20,
        resolved_day_offset=-7,
    ),
)


@dataclass(frozen=True, kw_only=True)
class CrossPersonSpec:
    id: str
    requester_id: str
    counterpart_id: str
    kind: str
    note: str
    status: str
    work_item_id: str | None = None
    created_day_offset: int = -5
    updated_day_offset: int | None = None


CROSS_PERSON_REQUESTS: tuple[CrossPersonSpec, ...] = (
    CrossPersonSpec(
        id="xpr-001",
        requester_id="U1002",
        counterpart_id="U1004",
        kind="review",
        note="second review on the payment intent PR",
        status="open",
        work_item_id="CHK-101",
        created_day_offset=-6,
    ),
    CrossPersonSpec(
        id="xpr-002",
        requester_id="U1004",
        counterpart_id="U1003",
        kind="input",
        note="confirm partial-refund rounding rules with finance",
        status="open",
        work_item_id="CHK-102",
        created_day_offset=-19,
    ),
    CrossPersonSpec(
        id="xpr-003",
        requester_id="U1007",
        counterpart_id="U1008",
        kind="dependency",
        note="provision 3-D Secure sandbox credentials",
        status="acknowledged",
        work_item_id="CHK-103",
        created_day_offset=-13,
        updated_day_offset=-10,
    ),
    CrossPersonSpec(
        id="xpr-004",
        requester_id="U1009",
        counterpart_id="U1006",
        kind="dependency",
        note="schedule security sign-off for passkey enrolment",
        status="open",
        work_item_id="IDP-301",
        created_day_offset=-9,
    ),
    CrossPersonSpec(
        id="xpr-005",
        requester_id="U1012",
        counterpart_id="U1008",
        kind="dependency",
        note="fix promo service 500s in staging",
        status="resolved",
        work_item_id="CHK-202",
        created_day_offset=-14,
        updated_day_offset=-4,
    ),
    CrossPersonSpec(
        id="xpr-006",
        requester_id="U1010",
        counterpart_id="U1013",
        kind="input",
        note="clarify multi-touch attribution rules",
        status="open",
        work_item_id="INS-502",
        created_day_offset=-16,
    ),
    CrossPersonSpec(
        id="xpr-007",
        requester_id="U1005",
        counterpart_id="U1003",
        kind="input",
        note="sign off guest checkout banner copy",
        status="acknowledged",
        work_item_id="CHK-203",
        created_day_offset=-3,
        updated_day_offset=-2,
    ),
)


# Progress phrases per work item, cycled through the window so each day's
# check-in reads like a different day of the same piece of work.
PROGRESS_NOTES: dict[str, tuple[str, ...]] = {
    "U1002": (
        "Payment intent API handlers done, wiring the capture path next",
        "Addressed review comments on the intent serializer",
        "Added idempotency key tests for capture retries",
        "Rebased the intent PR and re-ran the contract suite",
    ),
    "U1004": (
        "Mapped the refund edge cases, still short of a decision on rounding",
        "Wrote the partial-refund test matrix, cannot finish without finance",
        "Drafted the refund state machine doc for review",
    ),
    "U1007": (
        "Sketched the 3-D Secure step-up flow against the sandbox docs",
        "Stubbed the step-up challenge handler so tests can run offline",
        "Reviewed the PSD2 requirements with the payments pod",
    ),
    "U1005": (
        "Price breakdown component shipped, moving to the guest banner",
        "Guest checkout banner markup and copy slots in place",
        "Tightened the cart totals snapshot tests",
    ),
    "U1012": (
        "Promo validation edge cases covered, PR is up for review",
        "Fixed the promo staging regression and re-opened the PR",
        "Started the mini-cart accessibility audit",
    ),
    "U1009": (
        "Passkey enrolment happy path green, waiting on security review",
        "Added WebAuthn attestation tests for the enrolment flow",
        "Drafted the SCIM deprovisioning design note",
    ),
    "U1008": (
        "Session revocation endpoint merged, back on SAML metadata refresh",
        "Chased the staging SAML certificate again, still flaky",
        "Added alerting for SSO gateway certificate expiry",
    ),
    "U1010": (
        "Ingest backfill running at half rate, quota lifted this morning",
        "Attribution v2 spike: multi-touch rules still ambiguous",
        "Backfill completed for the first three event types",
    ),
    "U1001": (
        "Reviewed payments risk with the pod, escalating the refund decision",
        "Sat in on the storefront demo, cart work looks on track",
        "Prepped the program review pack",
    ),
    "U1003": (
        "Chasing finance on refund rounding, meeting booked",
        "Groomed the storefront backlog with the pod",
        "Signed off the guest checkout copy",
    ),
    "U1013": (
        "Reviewed the identity roadmap with the pod",
        "Drafted the attribution requirements clarification",
        "Prioritised SCIM deprovisioning for next sprint",
    ),
    "U1006": (
        "Ran payments standup, two blockers carried over",
        "Cleared the identity pod board, chasing security sign-off",
        "Facilitated the blocker triage with the manager",
    ),
    "U1014": (
        "Storefront board is clean, one review pending",
        "Data pod retro actions logged",
        "Chased the warehouse quota ticket to closure",
    ),
    "U1011": (
        "Reviewed the portfolio heatmap ahead of the board update",
        "Checkout remains the one programme I am watching",
    ),
}
