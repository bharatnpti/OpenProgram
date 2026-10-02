"""The QA org's people, hierarchy and Jira work: one editable source of truth.

Unlike ``scripts/demo_roster.py`` nothing here is mocked: every person is a real
Slack member, most are real Jira users, and developers get real GitLab accounts.
Emails are plus-addresses on one mailbox (``OPENPROGRAM_QA_MAIL_BASE``), so the
address itself never lands in the repo.

The shape is deliberately enterprise-messy, because that is what the mocks hid:

* one developer split across two projects (Noah: Payments + Identity)
* one developer in two pods of the same project (Zoe: Payments + Storefront)
* a shared SRE in three pods (Omar: Platform, Identity, Data)
* QA spanning projects (Sofia: Identity + Storefront)
* a pod serving two projects (Platform, via the ``platform`` label in CHK and IDP),
  so some issues sit in two pods at once
* two pods carved out of one Jira project by component (Payments / Storefront)
* a pod with no manager (Data)
* an exec with no pod and no Jira seat (Elena)
* one person whose Jira email differs from their Slack email (Raj)
* Git usernames that do not match Slack handles
* unassigned issues and a backlog that is not in any sprint
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts.qa_org import env

PROGRAM_ID = "program-platform"
PROGRAM_NAME = "Digital Platform Program"
GITLAB_GROUP = "acme"


@dataclass(frozen=True, kw_only=True)
class Membership:
    pod_id: str
    role: str = "developer"  # developer | manager | product_owner | scrum_master


@dataclass(frozen=True, kw_only=True)
class Person:
    tag: str  # plus-address tag and stable key
    name: str
    title: str
    roles: tuple[str, ...]  # OpenProgram app roles: exec, mgr, admin, po, sm, dev
    pods: tuple[Membership, ...] = ()
    timezone: str = "Europe/Berlin"
    in_jira: bool = True
    jira_tag: str | None = None  # set only when the Jira email differs from Slack
    gitlab_username: str | None = None
    scenario: str = ""

    @property
    def slack_email(self) -> str:
        return mail(self.tag)

    @property
    def jira_email(self) -> str | None:
        if not self.in_jira:
            return None
        return mail(self.jira_tag or self.tag)


@dataclass(frozen=True, kw_only=True)
class Project:
    id: str
    name: str
    jira_key: str
    repos: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Workstream:
    id: str
    name: str
    project_id: str
    epic: str  # summary of the Jira epic that carries this workstream
    repos: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Pod:
    id: str
    name: str
    project_ids: tuple[str, ...]
    workstream_ids: tuple[str, ...]
    jira_filter_jql: str | None
    repos: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Issue:
    project: str  # Jira key
    summary: str
    type: str = "Story"  # Story | Task | Bug | Spike is filed as Task with a label
    assignee: str | None = None  # Person.tag
    status: str = "To Do"  # To Do | In Progress | Done
    component: str | None = None
    labels: tuple[str, ...] = ()
    epic: str | None = None  # Workstream.epic
    priority: str | None = None
    in_sprint: bool = True


def mail(tag: str) -> str:
    base = env.require("OPENPROGRAM_QA_MAIL_BASE")["OPENPROGRAM_QA_MAIL_BASE"]
    local, _, domain = base.partition("@")
    return f"{local}+{tag}@{domain}"


def repo(name: str) -> str:
    return f"{GITLAB_GROUP}/{name}"


PROJECTS: tuple[Project, ...] = (
    Project(
        id="project-checkout",
        name="Checkout Revamp",
        jira_key="CHK",
        repos=(repo("checkout-api"), repo("storefront-web"), repo("platform-libs")),
    ),
    Project(
        id="project-identity",
        name="Identity Platform",
        jira_key="IDP",
        repos=(repo("identity-service"), repo("sso-gateway"), repo("platform-libs")),
    ),
    Project(
        id="project-insights",
        name="Customer Insights",
        jira_key="INS",
        repos=(repo("insights-pipeline"),),
    ),
)

WORKSTREAMS: tuple[Workstream, ...] = (
    Workstream(
        id="ws-payments",
        name="Payments API",
        project_id="project-checkout",
        epic="Payments API",
        repos=(repo("checkout-api"),),
    ),
    Workstream(
        id="ws-cart",
        name="Cart & Pricing",
        project_id="project-checkout",
        epic="Cart & Pricing",
        repos=(repo("storefront-web"),),
    ),
    Workstream(
        id="ws-login",
        name="Login Experience",
        project_id="project-identity",
        epic="Login Experience",
        repos=(repo("identity-service"),),
    ),
    Workstream(
        id="ws-sso",
        name="Enterprise SSO",
        project_id="project-identity",
        epic="Enterprise SSO",
        repos=(repo("sso-gateway"),),
    ),
    Workstream(
        id="ws-datalake",
        name="Data Lake Ingest",
        project_id="project-insights",
        epic="Data Lake Ingest",
        repos=(repo("insights-pipeline"),),
    ),
)

PODS: tuple[Pod, ...] = (
    Pod(
        id="pod-payments",
        name="Payments Pod",
        project_ids=("project-checkout",),
        workstream_ids=("ws-payments",),
        jira_filter_jql="component = Payments",
        repos=(repo("checkout-api"),),
    ),
    Pod(
        id="pod-storefront",
        name="Storefront Pod",
        project_ids=("project-checkout",),
        workstream_ids=("ws-cart",),
        jira_filter_jql="component = Storefront",
        repos=(repo("storefront-web"),),
    ),
    Pod(
        id="pod-identity",
        name="Identity Pod",
        project_ids=("project-identity",),
        workstream_ids=("ws-login", "ws-sso"),
        jira_filter_jql=None,
        repos=(repo("identity-service"), repo("sso-gateway")),
    ),
    Pod(
        id="pod-data",
        name="Data Pod",
        project_ids=("project-insights",),
        workstream_ids=("ws-datalake",),
        jira_filter_jql=None,
        repos=(repo("insights-pipeline"),),
    ),
    Pod(
        id="pod-platform",
        name="Platform Pod",
        project_ids=("project-checkout", "project-identity"),
        workstream_ids=(),
        jira_filter_jql="labels = platform",
        repos=(repo("platform-libs"),),
    ),
)

PEOPLE: tuple[Person, ...] = (
    Person(
        tag="elena",
        name="Elena Fischer",
        title="Director of Engineering",
        roles=("exec",),
        in_jira=False,
        scenario="exec with no pod and no Jira seat",
    ),
    Person(
        tag="asha",
        name="Asha Rao",
        title="Engineering Manager",
        roles=("mgr", "admin"),
        pods=(
            Membership(pod_id="pod-payments", role="manager"),
            Membership(pod_id="pod-storefront", role="manager"),
            Membership(pod_id="pod-identity", role="manager"),
            Membership(pod_id="pod-platform", role="manager"),
        ),
        scenario="owns the Slack workspace and Jira site; manages four pods across two projects",
    ),
    Person(
        tag="mina",
        name="Mina Patel",
        title="Product Owner",
        roles=("po",),
        pods=(
            Membership(pod_id="pod-payments", role="product_owner"),
            Membership(pod_id="pod-storefront", role="product_owner"),
        ),
        scenario="PO over two pods carved out of one Jira project",
    ),
    Person(
        tag="hana",
        name="Hana Kobayashi",
        title="Product Owner",
        roles=("po",),
        pods=(
            Membership(pod_id="pod-identity", role="product_owner"),
            Membership(pod_id="pod-data", role="product_owner"),
        ),
        timezone="Asia/Tokyo",
        scenario="PO in another timezone, across two projects",
    ),
    Person(
        tag="ira",
        name="Ira Novak",
        title="Scrum Master",
        roles=("sm",),
        pods=(
            Membership(pod_id="pod-payments", role="scrum_master"),
            Membership(pod_id="pod-identity", role="scrum_master"),
        ),
        scenario="scrum master across two projects, owns no tickets",
    ),
    Person(
        tag="liam",
        name="Liam Chen",
        title="Backend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-payments"),),
        gitlab_username="lchen",
        scenario="single-pod baseline",
    ),
    Person(
        tag="noah",
        name="Noah Weber",
        title="Senior Backend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-payments"), Membership(pod_id="pod-identity")),
        gitlab_username="noah.weber",
        scenario="split across two projects; heaviest ticket load",
    ),
    Person(
        tag="zoe",
        name="Zoe Almeida",
        title="Frontend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-storefront"), Membership(pod_id="pod-payments")),
        gitlab_username="zalmeida",
        scenario="two pods inside the same project, in progress in both",
    ),
    Person(
        tag="omar",
        name="Omar Haddad",
        title="Site Reliability Engineer",
        roles=("dev",),
        pods=(
            Membership(pod_id="pod-platform"),
            Membership(pod_id="pod-identity"),
            Membership(pod_id="pod-data"),
        ),
        timezone="Asia/Kolkata",
        gitlab_username="ohaddad",
        scenario="shared SRE in three pods across all three projects",
    ),
    Person(
        tag="sofia",
        name="Sofia Bergmann",
        title="QA Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-identity"), Membership(pod_id="pod-storefront")),
        gitlab_username="sbergmann",
        scenario="QA across two projects",
    ),
    Person(
        tag="raj",
        name="Raj Iyer",
        title="Data Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-data"),),
        jira_tag="riyer",
        gitlab_username="riyer",
        scenario="Jira email differs from Slack email, so matching by email fails",
    ),
)

ISSUES: tuple[Issue, ...] = (
    # --- CHK / Payments component -------------------------------------------
    Issue(
        project="CHK",
        summary="Payment intent API",
        assignee="liam",
        status="In Progress",
        component="Payments",
        epic="Payments API",
    ),
    Issue(
        project="CHK",
        summary="3-D Secure step-up flow",
        assignee="liam",
        component="Payments",
        epic="Payments API",
    ),
    Issue(
        project="CHK",
        summary="Payment provider sandbox credentials",
        type="Task",
        assignee="liam",
        status="Done",
        component="Payments",
        epic="Payments API",
    ),
    Issue(
        project="CHK",
        summary="Refund edge cases",
        assignee="noah",
        status="In Progress",
        component="Payments",
        epic="Payments API",
    ),
    Issue(
        project="CHK",
        summary="Duplicate capture on retry timeout",
        type="Bug",
        assignee="noah",
        component="Payments",
        epic="Payments API",
        priority="High",
    ),
    Issue(
        project="CHK",
        summary="Payment form validation UI",
        assignee="zoe",
        status="In Progress",
        component="Payments",
        epic="Payments API",
    ),
    Issue(
        project="CHK",
        summary="Capture retry runbook",
        type="Task",
        component="Payments",
        epic="Payments API",
        in_sprint=False,
    ),
    Issue(
        project="CHK",
        summary="Define refund policy acceptance criteria",
        type="Task",
        assignee="mina",
        status="In Progress",
        component="Payments",
    ),
    # --- CHK / Storefront component -----------------------------------------
    Issue(
        project="CHK",
        summary="Cart price breakdown",
        assignee="zoe",
        status="In Progress",
        component="Storefront",
        epic="Cart & Pricing",
    ),
    Issue(
        project="CHK",
        summary="Promo code validation",
        assignee="zoe",
        component="Storefront",
        epic="Cart & Pricing",
    ),
    Issue(
        project="CHK",
        summary="Storefront performance budget",
        type="Task",
        assignee="zoe",
        status="Done",
        component="Storefront",
        epic="Cart & Pricing",
    ),
    Issue(
        project="CHK",
        summary="Test plan: guest checkout",
        type="Task",
        assignee="sofia",
        status="In Progress",
        component="Storefront",
        epic="Cart & Pricing",
    ),
    Issue(
        project="CHK",
        summary="Mini-cart focus trap breaks keyboard navigation",
        type="Bug",
        assignee="sofia",
        component="Storefront",
        epic="Cart & Pricing",
    ),
    Issue(
        project="CHK",
        summary="Q4 checkout roadmap review",
        type="Task",
        assignee="asha",
    ),
    # --- CHK / platform label (Platform Pod; one also sits in Payments Pod) --
    Issue(
        project="CHK",
        summary="Upgrade shared HTTP client library",
        type="Task",
        assignee="omar",
        status="In Progress",
        labels=("platform",),
    ),
    Issue(
        project="CHK",
        summary="Payments service SLO dashboards",
        type="Task",
        assignee="omar",
        component="Payments",
        labels=("platform",),
    ),
    # --- IDP -----------------------------------------------------------------
    Issue(
        project="IDP",
        summary="Passkey enrolment",
        assignee="noah",
        status="In Progress",
        epic="Login Experience",
    ),
    Issue(
        project="IDP",
        summary="Session revocation endpoint",
        assignee="noah",
        epic="Login Experience",
    ),
    Issue(
        project="IDP",
        summary="Login loop on expired refresh token",
        type="Bug",
        assignee="noah",
        status="In Progress",
        epic="Login Experience",
        priority="High",
    ),
    Issue(
        project="IDP",
        summary="SAML metadata refresh",
        assignee="omar",
        status="In Progress",
        epic="Enterprise SSO",
    ),
    Issue(
        project="IDP",
        summary="SCIM deprovisioning test suite",
        type="Task",
        assignee="sofia",
        epic="Enterprise SSO",
    ),
    Issue(
        project="IDP",
        summary="Rotate IdP signing certificates",
        type="Task",
        assignee="omar",
        labels=("platform",),
        epic="Enterprise SSO",
    ),
    Issue(
        project="IDP",
        summary="Enterprise SSO onboarding guide",
        type="Task",
        assignee="hana",
        epic="Enterprise SSO",
    ),
    # --- INS -----------------------------------------------------------------
    Issue(
        project="INS",
        summary="Event ingest backfill",
        assignee="raj",
        status="In Progress",
        epic="Data Lake Ingest",
    ),
    Issue(
        project="INS",
        summary="Duplicate events in hourly rollup",
        type="Bug",
        assignee="raj",
        status="In Progress",
        epic="Data Lake Ingest",
        priority="High",
    ),
    Issue(
        project="INS",
        summary="Attribution model v2",
        assignee="raj",
        epic="Data Lake Ingest",
    ),
    Issue(
        project="INS",
        summary="Data lake retention policy",
        type="Task",
        assignee="omar",
        status="In Progress",
        epic="Data Lake Ingest",
    ),
    Issue(
        project="INS",
        summary="Spike: evaluate streaming CDC",
        type="Task",
        labels=("spike",),
        in_sprint=False,
    ),
)


def person(tag: str) -> Person:
    for candidate in PEOPLE:
        if candidate.tag == tag:
            return candidate
    raise KeyError(tag)
