"""The QA org's people, hierarchy and Jira work: one editable source of truth.

Unlike ``scripts/demo_roster.py`` nothing here is mocked: every person is a real
Slack member, Jira user and GitLab account. Emails are plus-addresses on one of
two mailboxes (``OPENPROGRAM_QA_MAIL_BASE`` and ``OPENPROGRAM_QA_MAIL_BASE_2``),
some on the googlemail.com alias of the same inbox, so no address lands in the repo.

The shape is deliberately enterprise-messy, because that is what the mocks hid:

* one developer split across two projects (Noah: Payments + Identity)
* one developer in two pods of the same project (Zoe: Payments + Storefront)
* a shared SRE in three pods (Omar: Platform, Identity, Data)
* QA spanning projects (Sofia: Identity + Storefront)
* a pod serving two projects (Platform, via the ``platform`` label in CHK and IDP),
  so some issues sit in two pods at once
* two pods carved out of one Jira project by component (Payments / Storefront)
* a pod with no manager (Data)
* an exec with no pod (Elena)
* Git usernames that do not match Slack handles
* unassigned issues and a backlog that is not in any sprint
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.qa_org import env

# The OpenProgram tenant the org lives in. "qa" belonged to the first Slack
# workspace; its members are keyed by that workspace's user ids, so the second
# workspace starts a fresh tenant instead of deleting the first one's data.
QA_TENANT = "qa2"
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
    gitlab_username: str | None = None
    mailbox: int = 1  # which OPENPROGRAM_QA_MAIL_BASE[_<n>] the plus-address is on
    googlemail: bool = False  # the googlemail.com alias: same inbox, a distinct address
    scenario: str = ""

    @property
    def email(self) -> str:
        """The one address this person uses in Slack, Jira and GitLab."""
        return mail(self.tag, mailbox=self.mailbox, googlemail=self.googlemail)

    @property
    def slack_email(self) -> str:
        return self.email

    @property
    def jira_email(self) -> str | None:
        return self.email if self.in_jira else None


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


@dataclass(frozen=True, kw_only=True)
class GitWork:
    """Commits one person pushed for one issue, and what became of them."""

    repo: str  # repository name inside GITLAB_GROUP
    author: str  # Person.tag
    issue: str  # Issue.summary; its Jira key names the branch and the MR
    commits: tuple[str, ...]  # one commit (adding one file) per message
    mr: str | None = "open"  # open | draft | merged | None: pushed, never opened
    to_main: bool = False  # committed straight to the default branch, no branch at all


def git_branch(work: GitWork, jira_key: str) -> str:
    """The branch a piece of work lives on: the Jira key, then a slug of the issue."""
    if work.to_main:
        return "main"
    slug = re.sub(r"[^a-z0-9]+", "-", work.issue.lower()).strip("-")[:32].rstrip("-")
    return f"{jira_key}-{slug}"


def mail(tag: str, *, mailbox: int = 1, googlemail: bool = False) -> str:
    key = "OPENPROGRAM_QA_MAIL_BASE" if mailbox == 1 else f"OPENPROGRAM_QA_MAIL_BASE_{mailbox}"
    base = env.require(key)[key]
    local, _, domain = base.partition("@")
    if googlemail and domain == "gmail.com":
        domain = "googlemail.com"
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
        gitlab_username="efischer",
        scenario="exec with no pod",
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
        gitlab_username="arao",
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
        gitlab_username="mpatel",
        googlemail=True,
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
        gitlab_username="hkobayashi",
        googlemail=True,
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
        gitlab_username="inovak",
        mailbox=2,
        scenario="scrum master across two projects, owns no tickets",
    ),
    Person(
        tag="liam",
        name="Liam Chen",
        title="Backend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-payments"),),
        gitlab_username="lchen",
        mailbox=2,
        scenario="single-pod baseline",
    ),
    Person(
        tag="noah",
        name="Noah Weber",
        title="Senior Backend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-payments"), Membership(pod_id="pod-identity")),
        gitlab_username="noah.weber",
        mailbox=2,
        scenario="split across two projects; heaviest ticket load",
    ),
    Person(
        tag="zoe",
        name="Zoe Almeida",
        title="Frontend Engineer",
        roles=("dev",),
        pods=(Membership(pod_id="pod-storefront"), Membership(pod_id="pod-payments")),
        gitlab_username="zalmeida",
        mailbox=2,
        googlemail=True,
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
        mailbox=2,
        googlemail=True,
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
        gitlab_username="riyer",
        mailbox=2,
        googlemail=True,
        scenario="data engineer in a pod with no manager",
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


# Git activity, shaped so each kind of signal has a case. Branches and MR titles
# carry the Jira key, the convention the work-item linking relies on.
GIT_WORK: tuple[GitWork, ...] = (
    GitWork(
        repo="checkout-api",
        author="liam",
        issue="Payment intent API",
        commits=("Add payment intent model", "Expose POST /payment-intents"),
    ),
    GitWork(
        repo="checkout-api",
        author="liam",
        issue="Payment provider sandbox credentials",
        commits=("Read sandbox credentials from the secret store",),
        mr="merged",
    ),
    GitWork(
        repo="checkout-api",
        author="noah",
        issue="Refund edge cases",
        commits=("Round partial refunds half-even",),
        mr="draft",
    ),
    # Payments-pod work in the Storefront pod's repository.
    GitWork(
        repo="storefront-web",
        author="zoe",
        issue="Payment form validation UI",
        commits=("Validate card fields inline",),
    ),
    GitWork(
        repo="storefront-web",
        author="zoe",
        issue="Cart price breakdown",
        commits=("Show tax and shipping lines", "Fix rounding in cart totals"),
    ),
    GitWork(
        repo="storefront-web",
        author="zoe",
        issue="Storefront performance budget",
        commits=("Add a bundle size budget",),
        mr="merged",
    ),
    GitWork(
        repo="storefront-web",
        author="sofia",
        issue="Test plan: guest checkout",
        commits=("Add guest checkout end-to-end scenarios",),
    ),
    GitWork(
        repo="identity-service",
        author="noah",
        issue="Passkey enrolment",
        commits=("Add WebAuthn registration endpoint",),
    ),
    # Merged while Jira still says In Progress: the record and the code disagree.
    GitWork(
        repo="identity-service",
        author="noah",
        issue="Login loop on expired refresh token",
        commits=("Clear the stale refresh cookie on 401",),
        mr="merged",
    ),
    # In progress in Jira with commits but no merge request.
    GitWork(
        repo="sso-gateway",
        author="omar",
        issue="SAML metadata refresh",
        commits=("Schedule IdP metadata refresh", "Cache IdP metadata by ETag"),
        mr=None,
    ),
    GitWork(
        repo="platform-libs",
        author="omar",
        issue="Upgrade shared HTTP client library",
        commits=("Bump the shared HTTP client to 3.x",),
    ),
    GitWork(
        repo="insights-pipeline",
        author="raj",
        issue="Event ingest backfill",
        commits=("Add the Q3 event backfill job",),
    ),
    GitWork(
        repo="insights-pipeline",
        author="raj",
        issue="Duplicate events in hourly rollup",
        commits=("Deduplicate by event id before the hourly rollup",),
        mr="merged",
    ),
    # An SRE with maintainer rights pushing straight to the default branch.
    GitWork(
        repo="insights-pipeline",
        author="omar",
        issue="Data lake retention policy",
        commits=("Apply 400-day retention to the raw zone",),
        mr=None,
        to_main=True,
    ),
)


def person(tag: str) -> Person:
    for candidate in PEOPLE:
        if candidate.tag == tag:
            return candidate
    raise KeyError(tag)
