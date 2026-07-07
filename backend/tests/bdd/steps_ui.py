"""Playwright-backed step definitions for the pure frontend/browser scenarios.

These cover the ``MS-E2E`` rows that cannot be exercised through the backend
API alone (client-side nav gating, route guards, disabled-button state,
polling/auto-select UI behavior, and timeline grouping). They drive a real
Vite dev server + backend through Playwright and only run when
``OPENPROGRAM_RUN_UI_BDD=1`` is set (see ``tests.bdd.fixtures.ui_stack``); they
skip gracefully otherwise, matching the existing docker-skip convention used
by ``backend/tests/integration``.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import httpx
from pytest_bdd import given, parsers, then, when

from tests.bdd.fixtures import UiStack

_CHECKIN_DATE_SEQUENCE = itertools.count()
_FIRST_FUTURE_MONDAY = date(2099, 1, 5)


def _fresh_weekday_iso() -> str:
    # ui_stack is session-scoped (one shared backend across all UI scenarios),
    # so each dispatch needs a distinct, never-before-used weekday to avoid
    # colliding with the daily_checkin same-date idempotency guard from an
    # earlier scenario.
    candidate = _FIRST_FUTURE_MONDAY + timedelta(days=next(_CHECKIN_DATE_SEQUENCE) * 7)
    return candidate.isoformat()


ROLE_LABELS = {
    "dev": "Developer",
    "sm": "Scrum Master",
    "po": "Product Owner",
    "mgr": "Manager",
    "exec": "Executive",
    "admin": "Admin",
}


@given("the Mock Slack frontend stack is running", target_fixture="stack")
def _given_frontend_stack(clean_ui_stack: UiStack) -> UiStack:
    return clean_ui_stack


@given(parsers.parse('a configured member "{member_id}" named "{name}" exists'))
def _given_ui_configured_member(stack: UiStack, member_id: str, name: str) -> None:
    response = httpx.post(
        f"{stack.backend_url}/config/members",
        json={"id": member_id, "name": name},
        timeout=5.0,
    )
    assert response.status_code in (201, 409), response.text


@given(parsers.parse('member "{member_id}" has a dispatched bot check-in'))
def _given_ui_member_has_dispatch(stack: UiStack, member_id: str) -> None:
    response = httpx.post(
        f"{stack.backend_url}/admin/workflows/checkin/dispatch",
        json={
            "tenant_id": "demo",
            "developer_id": member_id,
            "developer_name": member_id,
            "chat_external_id": member_id,
            "checkin_date": _fresh_weekday_iso(),
        },
        timeout=5.0,
    )
    assert response.status_code == 200, response.text


@when(parsers.parse('member "{member_id}" has a dispatched bot check-in'))
def _when_ui_member_has_dispatch(stack: UiStack, member_id: str) -> None:
    _given_ui_member_has_dispatch(stack, member_id)


def _select_role(page: object, role_code: str) -> None:
    label = ROLE_LABELS.get(role_code.lower(), role_code)
    page.wait_for_selector("#role-switcher")  # type: ignore[attr-defined]
    page.select_option("#role-switcher", label=label)  # type: ignore[attr-defined]
    page.wait_for_timeout(150)  # type: ignore[attr-defined]


@when(parsers.parse('I open "{path}" as role "{role}"'))
def _when_open_path_as_role(browser_page: object, path: str, role: str) -> None:
    page = browser_page
    page.goto("/me")  # type: ignore[attr-defined]
    page.wait_for_load_state("networkidle")  # type: ignore[attr-defined]
    _select_role(page, role)
    if path != "/me":
        page.goto(path)  # type: ignore[attr-defined]
    page.wait_for_load_state("networkidle")  # type: ignore[attr-defined]


@when(parsers.parse('I switch the active role to "{label}"'))
def _when_switch_role(browser_page: object, label: str) -> None:
    page = browser_page
    page.select_option("#role-switcher", label=label)  # type: ignore[attr-defined]
    page.wait_for_timeout(250)  # type: ignore[attr-defined]


@then(parsers.parse('the navigation should show a "{label}" link'))
def _then_nav_shows_link(browser_page: object, label: str) -> None:
    page = browser_page
    nav = page.get_by_role("navigation", name="Primary")  # type: ignore[attr-defined]
    assert nav.get_by_role("link", name=label).count() > 0


@then(parsers.parse('the navigation should not show a "{label}" link'))
def _then_nav_hides_link(browser_page: object, label: str) -> None:
    page = browser_page
    nav = page.get_by_role("navigation", name="Primary")  # type: ignore[attr-defined]
    assert nav.get_by_role("link", name=label).count() == 0


@then(parsers.parse('the browser URL path should be "{path}"'))
def _then_browser_url_path(browser_page: object, path: str) -> None:
    page = browser_page
    from urllib.parse import urlparse

    assert urlparse(page.url).path == path  # type: ignore[attr-defined]


@then(parsers.parse('the "{label}" button should be disabled'))
def _then_button_disabled(browser_page: object, label: str) -> None:
    page = browser_page
    assert page.get_by_role("button", name=label).is_disabled()  # type: ignore[attr-defined]


@then(parsers.parse('the "{label}" button should be enabled'))
def _then_button_enabled(browser_page: object, label: str) -> None:
    page = browser_page
    assert not page.get_by_role("button", name=label).is_disabled()  # type: ignore[attr-defined]


@when("I enter whitespace-only text into the reply message field")
def _when_enter_whitespace_reply(browser_page: object) -> None:
    page = browser_page
    page.wait_for_selector("#sim-reply-text")  # type: ignore[attr-defined]
    page.fill("#sim-reply-text", "   \n   ")  # type: ignore[attr-defined]


@when("I reset the simulator through the UI")
def _when_reset_through_ui(browser_page: object) -> None:
    page = browser_page
    page.get_by_role("main").get_by_role("button", name="Reset").click()  # type: ignore[attr-defined]
    dialog = page.get_by_role("alertdialog")  # type: ignore[attr-defined]
    dialog.wait_for()
    dialog.get_by_role("button", name="Reset").click()
    page.wait_for_timeout(250)  # type: ignore[attr-defined]


@when("I click the Refresh button")
def _when_click_refresh(browser_page: object) -> None:
    page = browser_page
    page.get_by_role("button", name="Refresh").click()  # type: ignore[attr-defined]
    page.wait_for_timeout(250)  # type: ignore[attr-defined]


@when("I wait for the message timeline to refresh")
@when("I wait for the message timeline to refresh without clicking Refresh")
def _when_wait_for_timeline_refresh(browser_page: object) -> None:
    page = browser_page
    page.wait_for_timeout(5500)  # type: ignore[attr-defined]


@then(parsers.parse('the reply message selector should show "{placeholder}"'))
def _then_reply_selector_shows_placeholder(browser_page: object, placeholder: str) -> None:
    page = browser_page
    value = page.locator("#sim-reply-message").input_value()  # type: ignore[attr-defined]
    assert value == "", value


def _selected_reply_option_text(page: object) -> str:
    select = page.locator("#sim-reply-message")  # type: ignore[attr-defined]
    value = select.input_value()
    if not value:
        return ""
    return select.locator(f"option[value='{value}']").inner_text()


@then(parsers.parse('the reply message selector should show a message for user "{user_id}"'))
def _then_reply_selector_shows_user(browser_page: object, user_id: str) -> None:
    page = browser_page
    for _ in range(20):
        if user_id in _selected_reply_option_text(page):
            return
        page.wait_for_timeout(500)  # type: ignore[attr-defined]
    assert user_id in _selected_reply_option_text(page)


@then(parsers.parse('the reply message selector should still show a message for user "{user_id}"'))
def _then_reply_selector_still_shows_user(browser_page: object, user_id: str) -> None:
    assert user_id in _selected_reply_option_text(browser_page)


@then('the reply message selector should no longer show "Select message"')
def _then_reply_selector_has_value(browser_page: object) -> None:
    page = browser_page
    locator = page.locator("#sim-reply-message")  # type: ignore[attr-defined]
    for _ in range(20):
        if locator.input_value() != "":
            return
        page.wait_for_timeout(500)  # type: ignore[attr-defined]
    assert locator.input_value() != ""


def _kpi_value(page: object, label: str) -> str:
    label_node = page.get_by_text(label, exact=True).first  # type: ignore[attr-defined]
    section = label_node.locator("xpath=ancestor::section[1]")
    return section.locator("div.text-2xl").inner_text()


@then(parsers.parse('the Messages KPI should read "{value}"'))
def _then_messages_kpi(browser_page: object, value: str) -> None:
    page = browser_page
    for _ in range(20):
        if _kpi_value(page, "Messages") == value:
            return
        page.wait_for_timeout(500)  # type: ignore[attr-defined]
    assert _kpi_value(page, "Messages") == value


@then(parsers.parse('the Members KPI should read "{value}"'))
def _then_members_kpi(browser_page: object, value: str) -> None:
    assert _kpi_value(browser_page, "Members") == value


@then(
    parsers.parse('the timeline should show one group for user "{user_id}" with {count:d} message')
)
@then(
    parsers.parse('the timeline should show one group for user "{user_id}" with {count:d} messages')
)
def _then_timeline_group(browser_page: object, user_id: str, count: int) -> None:
    page = browser_page
    label = page.get_by_text(user_id, exact=True).first  # type: ignore[attr-defined]
    label.wait_for(timeout=10000)
    group = label.locator("xpath=ancestor::div[contains(concat(' ', @class, ' '), ' px-4 ')][1]")
    text = group.inner_text()
    assert str(count) in text, text
