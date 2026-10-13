"""The routes behind Today and Check-in, as each role calls them.

- ``/me/jira-changes``: the caller's own changes and their answer, never anyone else's.
- ``/me/conversation``: the caller's own turns, read without refreshing retention.
- ``/projects/{id}/asks``: who must act, with no names for an executive.
- ``/me/today-counts``: replies and warnings over the caller's part; none for a developer.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInSignals,
    DeveloperStatus,
    IssueClaim,
    StatusSource,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from core.ports.chat import ChatProvider
from core.ports.issue_tracker import IssueTracker
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_task_update import _DEV, _OTHER, _TENANT, _team

_TODAY = date.today()
_NOW = datetime.now(tz=UTC)
_CORRELATION = "checkin-kai-today"


class _Registry(ServiceRegistry):
    """Memory mode with a recording chat and a tracker holding Kai's CHK-4."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings.model_copy(
                update={
                    "tenant_id": _TENANT,
                    "chat_provider": "fake",
                    "llm_provider": "fake",
                    "issue_tracker_provider": "fake",
                    "calendar_provider": "fake",
                    "vcs_provider": "fake",
                    "jira_writeback_enabled": True,
                }
            )
        )
        self.chat = FakeChatProvider()
        self.tracker = FakeIssueTracker(
            issues={
                "CHK-4": Issue(
                    tenant_id=_TENANT,
                    key="CHK-4",
                    title="Work on CHK-4",
                    state=IssueState.IN_PROGRESS,
                    assignee=UserRef(tenant_id=_TENANT, external_id="jira-kai"),
                )
            }
        )

    def chat_provider(self) -> ChatProvider:
        return self.chat

    def issue_tracker(self) -> IssueTracker:
        return self.tracker

    @property
    def store(self) -> InMemoryGraphStore:
        return self._memory_graph_store()


def _app(settings: Settings, roles: str, subject: str = _DEV) -> tuple[FastAPI, _Registry]:
    configured = settings.model_copy(
        update={"dev_principal_roles": roles, "dev_principal_subject": subject}
    )
    registry = _Registry(configured)
    return create_app(settings=registry.settings, registry=registry), registry


async def _seed(store: InMemoryGraphStore) -> None:
    """Kai's team, his held check-in with a CHK-4 proposal, his turns, a blocker naming Liam."""
    await _team(store)
    await store.upsert_identity_link(
        IdentityLink(tenant_id=_TENANT, developer_id=_DEV, jira_account_id="jira-kai")
    )
    asked_at = _NOW - timedelta(minutes=30)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_DEV,
            correlation_id=_CORRELATION,
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=CheckInSignals(
                progress_note="CHK-4 is done.",
                issue_updates=(IssueClaim(issue_key="CHK-4", claimed_state="done"),),
            ),
            checkin_date=_TODAY,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            developer_id=_DEV,
            correlation_id=_CORRELATION,
            chat_user_ref=_DEV,
            chat_thread_ref="D-kai",
            outbound_message_id="out-1",
            asked_at=asked_at,
        )
    )
    await store.record(
        WriteBackAudit(
            id="proposal-chk4",
            tenant_id=_TENANT,
            developer_id=_DEV,
            issue_key="CHK-4",
            correlation_id=_CORRELATION,
            status=WriteBackStatus.PROPOSED,
            target_state="done",
            before_state="in_progress",
            after_state="done",
            source="checkin",
            created_at=asked_at,
        )
    )
    for person, role, text, minutes in (
        (_DEV, ConversationRole.AGENT, "Morning Kai, how is CHK-4 going?", 30),
        (_DEV, ConversationRole.USER, "CHK-4 is done.", 25),
        (_OTHER, ConversationRole.USER, "Liam's own words.", 20),
    ):
        await store.append_turn(
            ConversationTurn(
                tenant_id=_TENANT,
                developer_id=person,
                conversation_id=_CORRELATION,
                conversation_date=_TODAY,
                role=role,
                content=text,
                correlation_id=_CORRELATION,
                chat_message_id=f"m-{minutes}",
                observed_at=_NOW - timedelta(minutes=minutes),
            )
        )
    blocker = DeveloperBlocker(
        tenant_id=_TENANT,
        blocker_id="b-review",
        developer_id=_DEV,
        description="Waiting on Liam Chen's review of the refund flow",
        normalized_key=normalize_blocker_key("Waiting on Liam Chen's review of the refund flow"),
        work_item_id="CHK-4",
        source=BlockerSource.CHECKIN,
        first_seen_on=_TODAY - timedelta(days=3),
        last_seen_on=_TODAY,
    )
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_DEV,
            as_of=_TODAY,
            source=StatusSource.CONFIRMED,
            blockers=(blocker.description,),
            summary="CHK-4 is done.",
        ),
        (blocker,),
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_OTHER,
            as_of=_TODAY,
            source=StatusSource.INFERRED,
            blockers=(),
            summary="Inferred from Jira: CHK-9 in progress.",
        )
    )


def _seeded(app: FastAPI, registry: _Registry) -> InMemoryGraphStore:
    asyncio.run(_seed(registry.store))
    return registry.store


# ---- /me/jira-changes ----------------------------------------------------------------


def test_the_developer_lists_and_answers_his_own_change_and_the_dm_hears_it(
    settings: Settings,
) -> None:
    app, registry = _app(settings, "dev")
    with TestClient(app) as client:
        _seeded(app, registry)
        listed = client.get("/me/jira-changes")
        item = listed.json()["items"][0]
        answered = client.post(f"/me/jira-changes/{item['id']}/answer", json={"answer": "yes"})
        again = client.get("/me/jira-changes")
        bad = client.post(f"/me/jira-changes/{item['id']}/answer", json={"answer": "maybe"})

    assert listed.status_code == 200, listed.text
    assert listed.json()["tracker"] == "the issue tracker"
    assert item["kind"] == "proposal"
    assert (item["issue_key"], item["target_label"], item["answerable"]) == ("CHK-4", "Done", True)
    assert item["gate"] == {
        "state": "open",
        "yes_allowed": True,
        "tracker_status": None,
        "merge_requests": [],
    }
    assert item["holds_checkin"] is True
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert (body["outcome"], body["checkin_finished"], body["line_sent"]) == (
        "applied",
        True,
        True,
    )
    assert body["detail"] == "Moved CHK-4 to Done in the issue tracker."
    assert registry.tracker.transitions == [(_TENANT, "CHK-4", "done")]
    lines = [m.text for m in registry.chat.sent if m.metadata.get("purpose") != "status_ack"]
    assert lines[0] == "Answered in the console: CHK-4 moved to Done in the issue tracker."
    assert again.json()["items"] == []
    assert bad.status_code == 422


def test_nobody_else_answers_the_developers_change(settings: Settings) -> None:
    app, registry = _app(settings, "admin,mgr", subject=_OTHER)
    with TestClient(app) as client:
        _seeded(app, registry)
        listed = client.get("/me/jira-changes")
        answered = client.post(
            "/me/jira-changes/proposal:proposal-chk4/answer", json={"answer": "yes"}
        )
        due = client.post("/me/jira-changes/due:CHK-4/answer", json={"answer": "yes"})

    assert listed.json()["items"] == []
    assert answered.status_code == 404
    assert due.status_code == 422
    assert "Change the due date in Jira" in due.json()["detail"]
    assert registry.tracker.transitions == []


# ---- /me/conversation ----------------------------------------------------------------


def test_the_own_conversation_is_read_without_touching_its_retention(settings: Settings) -> None:
    app, registry = _app(settings, "dev")
    with TestClient(app) as client:
        store = _seeded(app, registry)
        before = [turn.last_accessed_at for turn in store._conversation_turns]
        today = client.get(f"/me/conversation?day={_TODAY.isoformat()}")
        old = client.get(f"/me/conversation?day={(_TODAY - timedelta(days=31)).isoformat()}")
        after = [turn.last_accessed_at for turn in store._conversation_turns]

    assert today.status_code == 200, today.text
    body = today.json()
    assert (body["kept"], body["retention_days"], body["note"]) == (True, 30, None)
    assert [(turn["role"], turn["text"]) for turn in body["turns"]] == [
        ("agent", "Morning Kai, how is CHK-4 going?"),
        ("user", "CHK-4 is done."),
    ]
    assert after == before  # the read moved no purge date
    assert old.json() == {
        "day": (_TODAY - timedelta(days=31)).isoformat(),
        "kept": False,
        "retention_days": 30,
        "note": "Not kept after 30 days.",
        "turns": [],
    }


def test_an_admin_reads_only_their_own_conversation_here(settings: Settings) -> None:
    app, registry = _app(settings, "admin", subject=_OTHER)
    with TestClient(app) as client:
        _seeded(app, registry)
        mine = client.get(f"/me/conversation?day={_TODAY.isoformat()}&developer_id={_DEV}")

    assert [turn["text"] for turn in mine.json()["turns"]] == ["Liam's own words."]


# ---- /projects/{id}/asks -------------------------------------------------------------


def test_who_must_act_names_people_for_a_manager_and_teams_for_an_executive(
    settings: Settings,
) -> None:
    manager_app, manager_registry = _app(settings, "mgr", subject="U-mgr")
    with TestClient(manager_app) as client:
        _seeded(manager_app, manager_registry)
        manager = client.get(f"/projects/checkout/asks?as_of={_TODAY.isoformat()}")
    exec_app, exec_registry = _app(settings, "exec", subject="U-exec")
    with TestClient(exec_app) as client:
        _seeded(exec_app, exec_registry)
        executive = client.get(f"/projects/checkout/asks?as_of={_TODAY.isoformat()}")
    dev_app, dev_registry = _app(settings, "dev")
    with TestClient(dev_app) as client:
        _seeded(dev_app, dev_registry)
        developer = client.get(f"/projects/checkout/asks?as_of={_TODAY.isoformat()}")
        missing = client.get("/projects/nope/asks")

    assert manager.status_code == 200, manager.text
    [ask] = [row for row in manager.json()["asks"] if row["issue_key"] == "CHK-4"]
    assert ask["what"] == "CHK-4: Waiting on Liam Chen's review of the refund flow"
    assert ask["detail"] == "reported by Kai Thompson"
    assert (ask["need"], ask["need_label"], ask["waited_days"]) == ("fix", "Fix", 3)
    assert manager.json()["names_hidden"] is False

    assert executive.status_code == 200, executive.text
    body = executive.json()
    assert body["names_hidden"] is True
    text = executive.text
    for name in ("Kai", "Liam", "Thompson", "Chen"):
        assert name not in text
    [row] = [row for row in body["asks"] if row["issue_key"] == "CHK-4"]
    assert row["who"] == "Payments"
    assert row["what"] == "CHK-4: Waiting on a team member review of the refund flow"
    assert (row["detail"], row["escalated_to"]) == (None, None)

    assert developer.status_code == 403
    assert missing.status_code in (403, 404)


# ---- /me/today-counts ----------------------------------------------------------------


def test_replies_today_count_the_callers_part_and_a_developer_reads_none(
    settings: Settings,
) -> None:
    sm_app, sm_registry = _app(settings, "sm", subject=_OTHER)
    with TestClient(sm_app) as client:
        _seeded(sm_app, sm_registry)
        scrum_master = client.get(f"/me/today-counts?as_of={_TODAY.isoformat()}")
    dev_app, dev_registry = _app(settings, "dev")
    with TestClient(dev_app) as client:
        _seeded(dev_app, dev_registry)
        developer = client.get("/me/today-counts")
    mgr_app, mgr_registry = _app(settings, "mgr", subject="U-mgr")
    with TestClient(mgr_app) as client:
        _seeded(mgr_app, mgr_registry)
        manager = client.get(f"/me/today-counts?as_of={_TODAY.isoformat()}")

    assert scrum_master.status_code == 200, scrum_master.text
    assert scrum_master.json()["scope"] == "own"
    assert scrum_master.json()["replies"] == {
        "people": 2,
        "replied": 1,
        "inferred": 1,
        "waiting": 0,
    }
    assert scrum_master.json()["project_count"] == 1
    assert developer.status_code == 403
    assert manager.status_code == 200, manager.text
    assert manager.json()["scope"] == "portfolio"
    assert manager.json()["replies"]["people"] == 2
    assert isinstance(manager.json()["open_warnings"], int)
