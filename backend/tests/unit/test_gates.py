"""Gates: finding items and questions in Jira text, confirming them, and signing them off."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.delivery_service import DeliveryService
from core.application.gate_extraction import ModelItemFinder, extract_from_issue, questions_in
from core.application.gate_service import GateService
from core.domain.auth import Role
from core.domain.delivery import DeliveryStage
from core.domain.errors import AuthorizationDenied
from core.domain.gates import (
    GateError,
    GateItem,
    GateState,
    GateTemplate,
    ItemKind,
    ItemSource,
    ItemStatus,
    QuestionStatus,
    default_templates,
    evaluate_gate,
    validated_template,
)
from core.domain.graph import Developer, EdgeKind, GraphEdge, Project, Task
from core.domain.integrations import IssueComment, IssueState, IssueText, UserRef
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from infra.adapters.integrations.fake import FakeIssueTracker
from infra.persistence.in_memory_delivery import (
    InMemoryDeliverySettingsRepository,
    InMemoryRequirementsSnapshotRepository,
)
from infra.persistence.in_memory_gates import (
    InMemoryGateItemRepository,
    InMemoryGateTemplateRepository,
    InMemoryIssueScanRepository,
    InMemoryQuestionRepository,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
TEMPLATES = default_templates(TENANT)
ACCEPTANCE, ENGINEERING = TEMPLATES


def _user(account: str, name: str) -> UserRef:
    return UserRef(tenant_id=TENANT, external_id=account, display_name=name)


def _issue(
    description: str, *comments: IssueComment, state: IssueState = IssueState.IN_PROGRESS
) -> IssueText:
    return IssueText(
        tenant_id=TENANT,
        key="CHK-1",
        state=state,
        description=description,
        comments=comments,
        updated_at=NOW,
    )


def _texts(description: str) -> list[tuple[str, str]]:
    extraction = extract_from_issue(_issue(description), TEMPLATES)
    return [(item.kind, item.text) for item in extraction.items]


# --- Reading items from text ---------------------------------------------------------------


def test_a_heading_with_a_list_gives_one_item_per_entry() -> None:
    description = "\n".join(
        [
            "Some context about the change.",
            "",
            "## Acceptance criteria",
            "- The payment page loads within 1 second",
            "- [ ] The receipt is emailed once",
            "* A long criterion that",
            "  goes on to a second line",
            "",
            "Unrelated closing paragraph.",
        ]
    )

    assert _texts(description) == [
        ("acceptance", "The payment page loads within 1 second"),
        ("acceptance", "The receipt is emailed once"),
        ("acceptance", "A long criterion that goes on to a second line"),
    ]


def test_a_numbered_list_as_the_jira_adapter_writes_it() -> None:
    # Data Center's "h3." and "#" arrive as "###" and "1.", as Cloud's ADF does.
    description = "### Acceptance criteria\n1. Support refunds part of an order\n  1. Twice at most"

    assert _texts(description) == [
        ("acceptance", "Support refunds part of an order"),
        ("acceptance", "Twice at most"),
    ]


@pytest.mark.parametrize(
    "line",
    ["h3. Acceptance Criteria", "*Acceptance criteria:*", "**AC**", "Acceptance criteria:"],
)
def test_headings_are_read_in_markdown_wiki_and_bold(line: str) -> None:
    assert _texts(f"{line}\n1. Works offline") == [("acceptance", "Works offline")]


def test_a_heading_with_its_item_on_the_same_line() -> None:
    assert _texts("AC: refunds post within one day") == [
        ("acceptance", "refunds post within one day")
    ]


def test_a_sentence_that_starts_like_a_heading_is_not_one() -> None:
    assert _texts("Acceptance criteria are agreed in the workshop next week.") == []


def test_a_short_paragraph_under_a_heading_is_one_item() -> None:
    assert _texts("Test plan\nRun the regression suite against staging.\n\nOther notes.") == [
        ("test_case", "Run the regression suite against staging.")
    ]


def test_gherkin_scenarios_become_test_cases() -> None:
    description = "\n".join(
        [
            "Scenario: card declined twice",
            "Given the shopper pays with a saved card",
            "When the card is declined twice",
            "Then checkout offers another payment method",
            "",
            "Scenario: card accepted",
            "Given the shopper pays with a valid card",
            "Then the order is confirmed",
        ]
    )

    assert _texts(description) == [
        (
            "test_case",
            "card declined twice: Given the shopper pays with a saved card "
            "When the card is declined twice Then checkout offers another payment method",
        ),
        (
            "test_case",
            "card accepted: Given the shopper pays with a valid card Then the order is confirmed",
        ),
    ]


def test_items_in_comments_point_at_their_comment() -> None:
    comment = IssueComment(
        id="10042",
        author=_user("acc-po", "Maria"),
        created_at=NOW,
        body="Test cases:\n- Silence for 10 seconds hangs up",
    )

    (item,) = extract_from_issue(_issue("", comment), TEMPLATES).items

    assert (item.source, item.source_ref) == (ItemSource.COMMENT, "10042")


# --- Questions ---------------------------------------------------------------------------


def _comment(
    comment_id: str, author: UserRef, body: str, minutes: int, *mentions: UserRef
) -> IssueComment:
    return IssueComment(
        id=comment_id,
        author=author,
        created_at=NOW + timedelta(minutes=minutes),
        body=body,
        mentions=mentions,
    )


QA = _user("acc-qa", "Kim")
PO = _user("acc-po", "Maria")


def test_a_question_to_a_mentioned_person_is_answered_when_they_reply() -> None:
    issue = _issue(
        "",
        _comment("1", QA, "@Maria Which coupon codes count as test orders? Thanks.", 0, PO),
        _comment("2", PO, "The ones starting with QA-.", 30),
    )

    (question,) = questions_in(issue)

    assert question.asked_to == "acc-po" and question.asked_to_name == "Maria"
    assert question.summary == "Which coupon codes count as test orders?"
    assert question.status is QuestionStatus.ANSWERED
    assert question.answered_ref == "2"


def test_an_unanswered_question_on_a_closed_issue_is_closed_unanswered() -> None:
    issue = _issue(
        "",
        _comment("1", QA, "@Maria refund, replace, or keep?", 0, PO),
        state=IssueState.DONE,
    )

    (question,) = questions_in(issue)
    assert question.status is QuestionStatus.CLOSED_UNANSWERED
    assert question.summary == "Refund, replace, or keep?"


def test_a_comment_without_a_question_or_a_mention_is_not_a_question() -> None:
    issue = _issue(
        "",
        _comment("1", QA, "Retested on dev, still failing.", 0),
        _comment("2", QA, "Is this still planned?", 1),
        _comment("3", QA, "Question: who sends the receipt, the shop?", 2),
    )

    questions = questions_in(issue)

    assert [question.comment_ref for question in questions] == ["3"]
    assert questions[0].summary == "Who sends the receipt, the shop?"
    assert questions[0].asked_to == ""


# --- The model reads text without headings, and is held to quotes -----------------------


class _ScriptedModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=self.text,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0, latency_ms=1
            ),
            trace_id="t",
        )


async def test_the_model_finds_written_criteria_and_its_inventions_are_dropped() -> None:
    model = _ScriptedModel(
        '{"items": ['
        '{"kind": "acceptance", "quote": "the shopper sees the order summary within 1 second"},'
        '{"kind": "acceptance", "quote": "the shopper is delighted by the design"},'
        '{"kind": "unknown", "quote": "order summary"}]}'
    )
    finder = ModelItemFinder(model, model="test-model")
    issue = _issue(
        "After paying, the shopper sees the order summary within 1 second, "
        "measured on the dev system. Today it takes about six seconds."
    )

    items = await finder.find(TENANT, issue, [ACCEPTANCE], already=())

    assert [item.text for item in items] == ["the shopper sees the order summary within 1 second"]
    assert model.requests[0].json_mode is True


async def test_the_model_is_not_asked_when_the_text_already_has_its_items() -> None:
    model = _ScriptedModel('{"items": []}')
    finder = ModelItemFinder(model, model="m")
    issue = _issue("Acceptance criteria:\n- One thing that is long enough to count here")
    found = extract_from_issue(issue, [ACCEPTANCE]).items

    assert await finder.find(TENANT, issue, [ACCEPTANCE], already=found) == []
    assert model.requests == []


# --- Evaluating a gate -----------------------------------------------------------------------


def _item(kind: str, status: ItemStatus, template: GateTemplate = ACCEPTANCE) -> GateItem:
    return GateItem(
        tenant_id=TENANT,
        item_id=f"{kind}-{status}",
        issue_key="CHK-1",
        template_id=template.template_id,
        kind=kind,
        text=f"{kind} {status}",
        status=status,
        source=ItemSource.MANUAL,
        source_ref="",
        created_at=NOW,
        updated_at=NOW,
        created_by="a",
    )


def test_a_gate_passes_when_every_confirmed_item_is_met_or_waived() -> None:
    assert evaluate_gate(ACCEPTANCE, []).state is GateState.MISSING
    assert evaluate_gate(ACCEPTANCE, [_item("acceptance", ItemStatus.SUGGESTED)]).state is (
        GateState.MISSING
    )
    open_gate = evaluate_gate(
        ACCEPTANCE, [_item("acceptance", ItemStatus.MET), _item("acceptance", ItemStatus.PENDING)]
    )
    assert (open_gate.state, open_gate.met, open_gate.total) == (GateState.OPEN, 1, 2)
    assert (
        evaluate_gate(
            ACCEPTANCE,
            [_item("acceptance", ItemStatus.MET), _item("acceptance", ItemStatus.WAIVED)],
        ).state
        is GateState.PASSED
    )
    assert evaluate_gate(ACCEPTANCE, [_item("acceptance", ItemStatus.FAILED)]).state is (
        GateState.FAILED
    )


def test_a_template_is_tidied_and_refused_without_sign_off_roles() -> None:
    template = validated_template(
        GateTemplate(
            tenant_id=TENANT,
            template_id="security",
            name="  Security  review ",
            guards_stage=DeliveryStage.PRODUCTION,
            kinds=(
                ItemKind(
                    key="Pen Test",
                    label=" Pen test ",
                    sign_off_roles=(Role.MGR, Role.MGR),
                    headings=("Security", "security", " "),
                ),
            ),
        )
    )
    assert template.name == "Security review"
    assert template.kinds[0].key == "pen_test"
    assert template.kinds[0].sign_off_roles == (Role.MGR,)
    assert template.kinds[0].headings == ("Security",)

    with pytest.raises(GateError, match="Say who signs off K"):
        validated_template(
            GateTemplate(
                tenant_id=TENANT,
                template_id="x",
                name="X",
                guards_stage=DeliveryStage.PRODUCTION,
                kinds=(ItemKind(key="k", label="K", sign_off_roles=()),),
            )
        )


# --- The service --------------------------------------------------------------------------


async def _service(texts: dict[str, IssueText]) -> GateService:
    store = InMemoryGraphStore()
    await store.upsert_node(Project(tenant_id=TENANT, id="checkout", name="Checkout"))
    await store.upsert_node(Developer(tenant_id=TENANT, id="po", name="Pat Owner"))
    for key, status in (("CHK-1", "In Progress"), ("CHK-2", "Done")):
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=f"{key} work",
                metadata={"key": key, "status": status, "updated_at": NOW.isoformat()},
            )
        )
        await store.add_edge(
            GraphEdge(
                tenant_id=TENANT, from_node_id="checkout", to_node_id=key, kind=EdgeKind.CONTAINS
            )
        )
    tracker = FakeIssueTracker(tenant_id=TENANT)
    tracker.issues = []
    from core.domain.integrations import Issue

    for key in ("CHK-1", "CHK-2"):
        tracker.issues.append(Issue(tenant_id=TENANT, key=key, title=key, state=IssueState.TODO))
    tracker.texts = texts
    delivery = DeliveryService(
        graph_repository=store,
        settings_repository=InMemoryDeliverySettingsRepository(),
        snapshot_repository=InMemoryRequirementsSnapshotRepository(),
        clock=lambda: NOW,
        today=lambda: NOW.date(),
    )
    counter = iter(range(1000))
    return GateService(
        template_repository=InMemoryGateTemplateRepository(),
        item_repository=InMemoryGateItemRepository(),
        question_repository=InMemoryQuestionRepository(),
        scan_repository=InMemoryIssueScanRepository(),
        delivery_service=delivery,
        issue_tracker=tracker,
        graph_repository=store,
        clock=lambda: NOW,
        new_id=lambda: f"id-{next(counter)}",
    )


def _text_for(key: str, description: str, *comments: IssueComment) -> IssueText:
    return IssueText(
        tenant_id=TENANT,
        key=key,
        state=IssueState.IN_PROGRESS,
        description=description,
        comments=comments,
        updated_at=NOW,
    )


async def test_a_scan_suggests_once_and_a_dismissed_item_stays_dismissed() -> None:
    service = await _service(
        {
            "CHK-1": _text_for("CHK-1", "Acceptance criteria:\n- Works with saved cards"),
            "CHK-2": _text_for(
                "CHK-2",
                "Test cases:\n- Pay with a saved card",
                _comment("9", QA, "@Maria is 3-D Secure in scope?", 0, PO),
            ),
        }
    )

    first = await service.scan_scope(TENANT, "checkout", NOW.date())
    again = await service.scan_scope(TENANT, "checkout", NOW.date())
    board = await service.board(TENANT, "checkout", NOW.date())

    assert (first.read, first.suggested_items, first.questions) == (2, 2, 1)
    assert (again.read, again.unchanged) == (0, 2)
    by_key = {issue.key: issue for issue in board.issues}
    (suggestion,) = by_key["CHK-1"].items
    assert suggestion.status is ItemStatus.SUGGESTED
    await service.dismiss_item(TENANT, suggestion.item_id, actor="po")
    forced = await service.scan_scope(TENANT, "checkout", NOW.date(), force=True)
    assert forced.suggested_items == 0
    assert board.questions[0].confirmed is False


async def test_done_without_a_passed_gate_is_flagged() -> None:
    service = await _service({})

    board = await service.board(TENANT, "checkout", NOW.date())

    by_key = {issue.key: issue for issue in board.issues}
    assert by_key["CHK-2"].stage is DeliveryStage.PRODUCTION
    assert by_key["CHK-2"].passed_without == ("Business acceptance", "Engineering delivery")
    assert by_key["CHK-1"].passed_without == ()


async def test_only_the_named_roles_sign_off_and_tests_need_evidence() -> None:
    service = await _service({})
    acceptance = await service.add_item(
        TENANT,
        "CHK-1",
        template_id="business-acceptance",
        kind="acceptance",
        text="Pays",
        actor="dev",
    )
    test = await service.add_item(
        TENANT,
        "CHK-1",
        template_id="engineering-delivery",
        kind="test_case",
        text="Pay test",
        actor="dev",
    )

    with pytest.raises(AuthorizationDenied, match="Only a product owner or manager signs off"):
        await service.sign_off(
            TENANT, acceptance.item_id, ItemStatus.MET, actor="dev", roles=frozenset({Role.DEV})
        )
    met = await service.sign_off(
        TENANT, acceptance.item_id, ItemStatus.MET, actor="po", roles=frozenset({Role.PO})
    )
    assert met.signed_by == "po" and met.signed_at == NOW
    with pytest.raises(GateError, match="A test case is met only with a link to its evidence"):
        await service.sign_off(
            TENANT, test.item_id, ItemStatus.MET, actor="dev", roles=frozenset({Role.DEV})
        )
    passed = await service.sign_off(
        TENANT,
        test.item_id,
        ItemStatus.MET,
        actor="dev",
        roles=frozenset({Role.DEV}),
        evidence_url="https://ci.example.com/run/7",
    )
    assert passed.evidence_url == "https://ci.example.com/run/7"
    board = await service.board(TENANT, "checkout", NOW.date())
    assert board.actor_names == {"po": "Pat Owner"}


async def test_a_status_a_person_set_is_kept_through_later_scans() -> None:
    service = await _service(
        {"CHK-1": _text_for("CHK-1", "", _comment("1", QA, "@Maria then what?", 0, PO))}
    )
    await service.scan_scope(TENANT, "checkout", NOW.date())
    (question,) = (await service.board(TENANT, "checkout", NOW.date())).questions

    await service.update_question(
        TENANT, question.question_id, actor="qa", confirmed=True, status=QuestionStatus.PARTLY
    )
    await service.scan_scope(TENANT, "checkout", NOW.date(), force=True)

    (after,) = (await service.board(TENANT, "checkout", NOW.date())).questions
    assert after.status is QuestionStatus.PARTLY and after.confirmed is True


async def test_editing_a_default_gate_keeps_the_other_default() -> None:
    service = await _service({})
    templates, is_default = await service.templates(TENANT)
    assert is_default and len(templates) == 2

    await service.save_template(
        GateTemplate(
            tenant_id=TENANT,
            template_id="business-acceptance",
            name="Business sign-off",
            guards_stage=DeliveryStage.PRODUCTION,
            kinds=ACCEPTANCE.kinds,
        ),
        actor="admin",
    )

    templates, is_default = await service.templates(TENANT)
    assert not is_default
    assert sorted(template.name for template in templates) == [
        "Business sign-off",
        "Engineering delivery",
    ]


# --- API -------------------------------------------------------------------------------------


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


def test_api_gates_from_template_to_sign_off(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    with TestClient(app) as client:
        assert (
            client.post("/config/projects", json={"id": "checkout", "name": "Checkout"}).status_code
            == 201
        )
        templates = client.get("/config/gates")
        board = client.get("/projects/checkout/gates", headers=_as("exec"))
        added = client.post(
            "/issues/CHK-1/gate-items",
            json={"template_id": "business-acceptance", "kind": "acceptance", "text": "Pays"},
            headers=_as("dev"),
        )
        item_id = added.json()["item_id"]
        dev_sign = client.put(
            f"/gate-items/{item_id}/sign-off", json={"status": "met"}, headers=_as("dev")
        )
        po_sign = client.put(
            f"/gate-items/{item_id}/sign-off", json={"status": "met"}, headers=_as("po")
        )
        exec_add = client.post(
            "/issues/CHK-1/gate-items",
            json={"template_id": "business-acceptance", "kind": "acceptance", "text": "x"},
            headers=_as("exec"),
        )
        scan = client.post("/projects/checkout/gates/scan", headers=_as("sm"))
        question = client.post(
            "/issues/CHK-1/questions",
            json={"asked_to": "Product", "summary": "Which code table applies?"},
            headers=_as("sm"),
        )
        updated = client.put(
            f"/questions/{question.json()['question_id']}",
            json={"status": "partly"},
            headers=_as("sm"),
        )
        bad_template = client.put(
            "/config/gates",
            json={"name": "X", "guards_stage": "production", "kinds": []},
        )

    assert templates.json()["is_default"] is True
    assert board.status_code == 200
    assert added.status_code == 201
    assert dev_sign.status_code == 403
    assert po_sign.json()["status"] == "met"
    assert exec_add.status_code == 403
    assert scan.json() == {
        "read": 0,
        "unchanged": 0,
        "failed": 0,
        "suggested_items": 0,
        "questions": 0,
    }
    assert updated.json()["status"] == "partly" and updated.json()["status_set_by_person"] is True
    assert bad_template.status_code == 422


def test_api_a_developer_and_a_scrum_master_work_the_gates_but_read_no_progress(
    settings: Settings,
) -> None:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    with TestClient(app) as client:
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        boards = {
            role: client.get("/projects/checkout/gates", headers=_as(role))
            for role in ("dev", "sm", "po", "mgr", "exec")
        }
        added = client.post(
            "/issues/CHK-1/gate-items",
            json={"template_id": "engineering-delivery", "kind": "test_case", "text": "Refunds"},
            headers=_as("dev"),
        )
        item_id = added.json()["item_id"]
        confirmed = client.post(f"/gate-items/{item_id}/confirm", headers=_as("sm"))
        dev_sign = client.put(
            f"/gate-items/{item_id}/sign-off",
            json={"status": "met", "evidence_url": "https://ci.example.com/run/7"},
            headers=_as("dev"),
        )
        sm_sign = client.put(
            f"/gate-items/{item_id}/sign-off",
            json={"status": "failed", "note": "Second refund double-books"},
            headers=_as("sm"),
        )
        # The rest of the project stays with the roles that read its progress.
        progress_reads = [
            client.get(path, headers=_as(role))
            for role in ("dev", "sm")
            for path in (
                "/projects/checkout/progress",
                "/projects/checkout/delivery",
                "/projects/checkout/requirements",
                "/projects/checkout/releases",
            )
        ]

    assert {role: response.status_code for role, response in boards.items()} == {
        "dev": 200,
        "sm": 200,
        "po": 200,
        "mgr": 200,
        "exec": 200,
    }
    assert set(boards["dev"].json()) == {
        "project_id",
        "release_id",
        "templates",
        "issues",
        "questions",
        "actor_names",
    }
    assert added.status_code == 201 and confirmed.json()["status"] == "pending"
    assert dev_sign.status_code == 200 and dev_sign.json()["status"] == "met"
    assert sm_sign.status_code == 200 and sm_sign.json()["status"] == "failed"
    assert [response.status_code for response in progress_reads] == [403] * 8


def test_api_a_board_for_an_unknown_project_is_not_found(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/projects/nope/gates")

    assert response.status_code == 404
