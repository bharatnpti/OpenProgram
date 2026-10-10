"""People are named in what OpenProgram writes for a reader, never shown by their chat id.

On the QA tenant an inferred status read "... risks flagged on U..." (a raw chat
member id), and it showed on Today's check-ins, on Signals > Risks and wherever
the status was quoted. Every builder below names the person instead, or says
"a team member" when nothing names them; an issue key such as CHK-12 stays.
Text stored before that is cleaned when it is read (``without_member_ids``).

The ids here are made up in the chat provider's shape: U, then ten capitals
and digits.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.dtos import DriftFindingResponse, MyStatusResponse, PodCheckinsResponse
from api.main import create_app
from config.settings import Settings
from core.application import status_collector as collector
from core.application.checkin_drift import (
    checkin_drift_signals,
    eta_stated_fact,
    issue_eta,
    review_without_merge_request_fact,
)
from core.application.cross_person_service import CrossPersonRequestService
from core.application.forecast_service import _target_reasons
from core.application.person_names import (
    CHAT_ID,
    UNKNOWN_PERSON,
    member_names,
    without_member_ids,
)
from core.application.persona_views import CheckinDeveloperView, PodCheckinsView
from core.application.risk_service import RISK_FACT_SOURCE
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.escalation import EscalationTarget
from core.domain.forecast import (
    Commitment,
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    HistoryForecast,
)
from core.domain.graph import Developer, EntityRef, FactEvent, NodeKind, Task
from core.domain.risk import DriftFinding, DriftFindingKind
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus, IssueClaim, StatusSource
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider
from tests.fixtures.demo_graph import populate_demo_graph

TENANT = "demo"
NOOR = "U0000TEST01"
RAMI = "U0000TEST02"
STRANGER = "U0000TEST99"
NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
DAY = date(2026, 10, 9)
MEMBERS = [
    Developer(tenant_id=TENANT, id=NOOR, name="Noor Test"),
    # A member whose node id is not the chat id: both name them.
    Developer(
        tenant_id=TENANT, id="dev-rami", name="Rami Test", metadata={"chat_external_id": RAMI}
    ),
    # Imported with no name of their own: the id is not a name.
    Developer(tenant_id=TENANT, id="U0000TEST03", name="U0000TEST03"),
]


def _no_chat_id(text: str) -> None:
    assert CHAT_ID.search(text) is None, text


# ---- The read-time cleanup ---------------------------------------------------------------


def test_member_names_name_a_member_by_node_id_and_chat_id_but_never_by_an_id() -> None:
    assert member_names(MEMBERS) == {
        NOOR: "Noor Test",
        "dev-rami": "Rami Test",
        RAMI: "Rami Test",
    }


@pytest.mark.parametrize(
    ("stored", "read"),
    [
        (
            f"Inferred from 1 active issue: CHK-12 Cart; risks flagged on {NOOR} and CHK-12.",
            "Inferred from 1 active issue: CHK-12 Cart; risks flagged on Noor Test and CHK-12.",
        ),
        (f"Waits on {RAMI} and dev-rami.", "Waits on Rami Test and Rami Test."),
        # Already named beside its id: the name once.
        (f"Noor Test ({NOOR}) said CHK-4 is in review.", "Noor Test said CHK-4 is in review."),
        # Nobody names them: a person, never the id.
        (f"risks flagged on {STRANGER}", "risks flagged on a team member"),
        # Not people: an issue key, a word in capitals, a path with an id in it.
        ("CHK-12 UNDERSTOOD acme/U0000TEST01", "CHK-12 UNDERSTOOD acme/U0000TEST01"),
    ],
)
def test_stored_text_reads_with_names(stored: str, read: str) -> None:
    assert without_member_ids(stored, member_names(MEMBERS)) == read


def test_a_member_whose_id_is_a_word_never_rewrites_the_word() -> None:
    names = {"liam": "Liam Chen"}

    assert without_member_ids("liam is away", names) == "liam is away"


def test_todays_check_ins_and_the_persons_own_status_clean_a_stored_summary() -> None:
    stored = f"Inferred from 1 active issue: CHK-12 Cart; risks flagged on {NOOR}."
    names = member_names(MEMBERS)
    view = PodCheckinsView(
        pod_id="pod-1",
        pod_name="Pod",
        as_of=DAY,
        confirmed=0,
        partial=0,
        stale=0,
        missing=1,
        developers=(
            CheckinDeveloperView(
                developer_id=NOOR,
                developer_name="Noor Test",
                state="missing",
                source=StatusSource.INFERRED,
                status_as_of=DAY,
                summary=stored,
            ),
        ),
    )
    status = DeveloperStatus(
        tenant_id=TENANT,
        developer_id=NOOR,
        as_of=DAY,
        source=StatusSource.INFERRED,
        blockers=("no confirmed reply",),
        summary=stored,
    )

    checkins = PodCheckinsResponse.from_view(view, names)
    mine = MyStatusResponse.from_domain(status, names=names)

    assert checkins.developers[0].summary.endswith("risks flagged on Noor Test.")
    assert mine.summary.endswith("risks flagged on Noor Test.")
    # With no names at all, a chat id still never shows.
    assert (
        PodCheckinsResponse.from_view(view)
        .developers[0]
        .summary.endswith(f"risks flagged on {UNKNOWN_PERSON}.")
    )


# ---- The builders ----------------------------------------------------------------------


def _risk_fact(entity_kind: NodeKind, entity_id: str) -> FactEvent:
    return FactEvent(
        tenant_id=TENANT,
        source=RISK_FACT_SOURCE,
        entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=NOOR),
        payload={"entity_kind": entity_kind.value, "entity_id": entity_id, "rule_id": "pr_age"},
        observed_at=NOW,
        correlation_id=f"risk-{entity_id}",
    )


def test_the_close_out_summary_names_the_person_a_risk_is_filed_on() -> None:
    facts = [
        _risk_fact(NodeKind.DEVELOPER, NOOR),
        _risk_fact(NodeKind.TASK, "CHK-12"),
        _risk_fact(NodeKind.DEVELOPER, STRANGER),
    ]

    named = collector._inferred_summary([], facts, NOW, people={NOOR: "Noor Test"})
    unnamed = collector._inferred_summary([], facts, NOW)

    assert "risks flagged on Noor Test, CHK-12 and a team member" in named
    assert "risks flagged on a team member and CHK-12" in unnamed
    for text in (named, unnamed):
        _no_chat_id(text)


def test_a_check_in_or_nudge_dm_greets_by_name_or_says_there() -> None:
    for purpose in ("checkin", "nudge"):
        for name in (None, NOOR, ""):
            text = collector._fallback_outbound_checkin_text(
                developer_id=NOOR, developer_name=name, context="", purpose=purpose
            )
            assert text.startswith("Hi there, "), text
            _no_chat_id(text)
        named = collector._fallback_outbound_checkin_text(
            developer_id=NOOR, developer_name="Noor Test", context="", purpose=purpose
        )
        assert named.startswith("Hi Noor Test, ")


def test_a_merge_request_in_the_dm_context_names_its_author() -> None:
    fact = FactEvent(
        tenant_id=TENANT,
        source="vcs_pull_request",
        entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=NOOR),
        payload={"repo": "acme/storefront-web", "id": "11", "state": "opened"},
        observed_at=NOW,
        correlation_id="mr-11",
    )

    (line,) = collector._fact_context_lines([fact], "Noor Test")

    assert line.startswith("Recent Git activity for Noor Test: source=vcs_pull_request")
    _no_chat_id(line)


def test_an_escalation_notice_names_the_person_or_a_team_member() -> None:
    for target in (EscalationTarget.SCRUM_MASTER, EscalationTarget.MANAGER):
        text = collector._compose_escalation_notice(target=target, developer_name=None)
        assert text.startswith("Heads up: a team member hasn't completed")
        _no_chat_id(text)


def _drift_facts(*, name: str | None) -> list[FactEvent]:
    stated = datetime(2026, 10, 9, 9, tzinfo=UTC)
    return [
        review_without_merge_request_fact(
            tenant_id=TENANT,
            issue_key="CHK-4",
            developer_id=NOOR,
            # An older fact recorded the id as the name when none was known.
            developer_name=name,
            as_of=DAY,
            status_source=StatusSource.CONFIRMED,
            observed_at=stated,
            correlation_id="corr-1",
        ),
        eta_stated_fact(
            tenant_id=TENANT,
            issue_key="CHK-5",
            eta=issue_eta(IssueClaim(issue_key="CHK-5", note="ETA Friday"), DAY),
            developer_id=NOOR,
            developer_name=name,
            as_of=DAY,
            observed_at=stated,
            correlation_id="corr-1",
        ),
        eta_stated_fact(
            tenant_id=TENANT,
            issue_key="CHK-5",
            eta=issue_eta(IssueClaim(issue_key="CHK-5", note="ETA next Wednesday"), DAY),
            developer_id="dev-rami",
            developer_name=None,
            as_of=DAY,
            observed_at=stated,
            correlation_id="corr-2",
        ),
    ]


@pytest.mark.parametrize("recorded", [NOOR, None])
def test_drift_reasons_name_who_said_it(recorded: str | None) -> None:
    def reasons(names: dict[str, str] | None) -> list[str]:
        return [
            signal.reason
            for signal in checkin_drift_signals(
                _drift_facts(name=recorded),
                issue_keys={"CHK-4", "CHK-5"},
                as_of=DAY,
                merge_request_facts=[],
                owners={"CHK-5": "dev-rami"},
                names=names,
            )
        ]

    named = reasons(member_names(MEMBERS))
    unnamed = reasons(None)

    assert named == [
        "Noor Test said CHK-4 is in review, but no open merge request names it.",
        "ETAs disagree for CHK-5: Rami Test (owner) said next Wednesday, Oct 14; "
        "Noor Test said Friday, Oct 9. The owner's ETA is the one used.",
    ]
    assert (
        unnamed[0] == "A team member said CHK-4 is in review, but no open merge request names it."
    )
    for text in (*named, *unnamed):
        _no_chat_id(text)


def test_who_committed_a_date_is_named_or_a_team_member() -> None:
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")
    commitment = Commitment(
        scope=scope,
        target_date=date(2026, 11, 20),
        original_date=date(2026, 11, 20),
        changes=(
            DateChange(
                tenant_id=TENANT,
                scope=scope,
                target_date=date(2026, 11, 20),
                changed_at=NOW,
                changed_by=STRANGER,
            ),
        ),
    )
    history = HistoryForecast(
        p50=None, p85=None, remaining=1, unit="requirements", sample_days=0, completed_in_sample=0
    )

    (named,) = _target_reasons(
        commitment, date(2026, 11, 20), "committed", history, {STRANGER: "Ira"}
    )
    (unnamed,) = _target_reasons(commitment, date(2026, 11, 20), "committed", history, {})

    assert named == "Committed for Fri 20 Nov 2026 by Ira."
    assert unnamed == "Committed for Fri 20 Nov 2026 by a team member."


def _request(display_name: str | None) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id="req-1",
        requester_id=NOOR,
        requester_chat_ref=NOOR,
        counterpart_id=RAMI,
        kind=CrossPersonRequestKind.REVIEW,
        note="API schema review",
        source_correlation_id="corr-1",
        status=CrossPersonRequestStatus.ACKNOWLEDGED,
        created_at=NOW,
        updated_at=NOW,
        counterpart_display_name=display_name,
    )


@pytest.mark.parametrize(("in_directory", "named"), [(False, "A team member"), (True, "Rami Test")])
async def test_a_dm_to_the_requester_names_the_person_asked(in_directory: bool, named: str) -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    if in_directory:
        await directory.upsert_users(
            [DirectoryUser(tenant_id=TENANT, external_id=RAMI, display_name="Rami Test")]
        )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store, chat_provider=chat, directory_repository=directory
    )

    await service._notify_requester_acknowledged(_request(None), "tomorrow")
    await service._notify_requester_resolved(_request(None))
    await service._notify_requester_resolved(_request(None), merged_labels=("checkout-api !1",))

    acknowledged, resolved, merged = (message.text for message in chat.sent)
    assert acknowledged.startswith(f"{named} acknowledged your review request")
    assert resolved.startswith(f"{named} marked your review request resolved")
    assert f"request to {named[:1].lower() + named[1:] if not in_directory else named}" in merged
    for text in (acknowledged, resolved, merged):
        _no_chat_id(text)


def test_a_drift_response_cleans_a_reason_built_from_an_older_fact() -> None:
    finding = DriftFinding(
        tenant_id=TENANT,
        kind=DriftFindingKind.SAID_IN_REVIEW_NO_MR,
        severity=Rag.AMBER,
        entity_ref=Task(tenant_id=TENANT, id="CHK-4", name="CHK-4").ref,
        workstream_id=None,
        reason=f"{NOOR} said CHK-4 is in review, but no open merge request names it.",
        detected_at=NOW,
    )

    response = DriftFindingResponse.from_domain(finding, member_names(MEMBERS))

    assert (
        response.reason == "Noor Test said CHK-4 is in review, but no open merge request names it."
    )


# ---- Guard: the main builders never interpolate a person's raw id ---------------------

BACKEND = Path(__file__).resolve().parents[2]
BUILDERS = (
    "core/application/status_collector.py",
    "core/application/status_summaries.py",
    "core/application/checkin_drift.py",
    "core/application/risk_service.py",
    "core/application/day_report_asks.py",
    "core/application/day_report_builder.py",
    "core/application/forecast_service.py",
    "core/application/cross_person_service.py",
    "core/application/narrative_brief_service.py",
    "core/application/brief_facts.py",
    "core/application/portfolio_feed_service.py",
    "core/application/persona_views.py",
    "core/application/attention.py",
)
# What holds a person's id: a variable or a field of that name, or a principal's
# subject; also as the fallback of an "or" or the default of a .get().
PERSON_ID_NAMES = frozenset(
    {
        "developer_id",
        "chat_external_id",
        "owner_id",
        "member_id",
        "person_id",
        "requester_id",
        "requester_chat_ref",
        "counterpart_id",
        "assignee_id",
        "changed_by",
        "actor",
    }
)
# Not text a person reads: keys, correlation ids and log fields.
ALLOWED: set[tuple[str, str]] = set()


def _person_id(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name) and node.id in PERSON_ID_NAMES:
        return node.id
    if isinstance(node, ast.Attribute):
        if node.attr in PERSON_ID_NAMES or node.attr == "subject":
            return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        key = node.slice.value
        return key if isinstance(key, str) and key in PERSON_ID_NAMES else None
    if (
        isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and (found := next((_person_id(value) for value in node.values[1:]), None))
    ):
        # "name or developer_id": the id is the fallback.
        return found
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        # names.get(actor, actor): the id is the default.
        if node.func.attr == "get" and len(node.args) == 2:
            return _person_id(node.args[1])
    return None


@pytest.mark.parametrize("module", BUILDERS)
def test_no_builder_writes_a_persons_raw_id_into_text(module: str) -> None:
    tree = ast.parse((BACKEND / module).read_text())
    found = [
        f"{module}:{node.lineno}: {ast.unparse(node)[:100]}"
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for value in node.values
        if isinstance(value, ast.FormattedValue)
        and (name := _person_id(value.value)) is not None
        and (module, name) not in ALLOWED
    ]

    assert found == []


# ---- Stored text, read through the API -------------------------------------------------


def test_a_summary_stored_with_a_chat_id_reads_with_the_name_on_every_screen(
    settings: Settings,
) -> None:
    """Today's check-ins, the person's own status and their focus read the stored row."""
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "sm,dev", "dev_principal_subject": "dev-liam"}
        )
    )
    stored = (
        "Inferred from 1 active issue: CHK-12 Cart; "
        f"risks flagged on {NOOR} and {STRANGER}; recent Git activity: 1 pull request."
    )
    read = (
        "Inferred from 1 active issue: CHK-12 Cart; "
        "risks flagged on Liam and a team member; recent Git activity: 1 pull request."
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            populate_demo_graph(
                registry.graph_repository(), registry.time_series_repository(), settings.tenant_id
            )
        )
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(
                    tenant_id=settings.tenant_id,
                    id="dev-liam",
                    name="Liam",
                    metadata={"chat_external_id": NOOR},
                )
            )
        )
        asyncio.run(
            registry.status_repository().record_developer_status(
                DeveloperStatus(
                    tenant_id=settings.tenant_id,
                    developer_id="dev-liam",
                    as_of=date(2026, 6, 15),
                    source=StatusSource.INFERRED,
                    blockers=("no confirmed reply",),
                    summary=stored,
                )
            )
        )
        checkins = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")
        mine = client.get("/me/status?as_of=2026-06-15")
        focus = client.get("/me/focus?as_of=2026-06-15")

    liam = next(dev for dev in checkins.json()["developers"] if dev["developer_id"] == "dev-liam")
    assert liam["summary"] == read
    assert mine.json()["summary"] == read
    assert focus.json()["summary"] == read
