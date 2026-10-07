"""Screenshots of frontend-v3 for docs/screenshots, taken from the mock API.

Mock data only. `npm run mock` serves the built app with scripts/mock-api.mjs, which
answers every endpoint with made-up demo data. The pictures are committed to a public
repository, so this script refuses a server whose people are not the demo roster. Never
point it at a real tenant.

    cd frontend-v3 && npm run mock                      # http://127.0.0.1:5175
    uv run python frontend-v3/scripts/screenshots.py    # from the repository root
    uv run python frontend-v3/scripts/screenshots.py --only 17 19 26

It needs Playwright's Chromium once: `uv run playwright install chromium`.

Each shot in screenshots.json names a path, who is acting (the localStorage keys the
console reads, set before it loads), a width (1440 px, or 390 for a phone) and, when a
picture needs it, steps that open the palette or a dialog first. The browser's clock is
fixed to the mock's day and its zone to UTC, and the one answer that carries the machine's own
clock (when the data sources were read) is restamped, so every run shows the same day and times.
"""

import argparse
import json
import re
import struct
import urllib.request
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Locator, Page, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "screenshots.json"
OUT_DIR = HERE.parent / "docs" / "screenshots"
BASE = "http://127.0.0.1:5175"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# The mock's "today" is 2026-10-06 (scripts/mock-api.mjs). Fixing the browser's clock to an
# afternoon of that day keeps every "today", greeting and "N days ago" the same on any run.
MOCK_NOW = "2026-10-06T16:30:00Z"
DESKTOP_WIDTH = 1440
HEIGHT = 900
# The demo roster's ids (scripts/mock-console.mjs, scripts/demo_roster.py): U1001 to U1014.
DEMO_PERSON = re.compile(r"U10\d\d")


class Step(TypedDict, total=False):
    press: str
    type: str
    wait: int
    click: dict[str, str]


class Shot(TypedDict):
    name: str
    path: str
    storage: dict[str, str]
    width: NotRequired[int]
    wait: NotRequired[int]
    full_page: NotRequired[bool]
    steps: NotRequired[list[Step]]


def load_shots(only: list[str]) -> list[Shot]:
    """The manifest's shots, or just those whose number ("17") or file name was asked for."""
    shots: list[Shot] = json.loads(MANIFEST.read_text())
    if not only:
        return shots
    return [shot for shot in shots if {shot["name"], shot["name"].split("-")[0]} & set(only)]


def check_mock(base: str) -> None:
    """Stop unless the server is local and its people are the mock's demo roster."""
    if urlparse(base).hostname not in LOCAL_HOSTS:
        raise SystemExit(f"{base} is not a local address. Only the local mock is shot.")
    request = urllib.request.Request(
        f"{base}/api/v1/auth/dev-users", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            people = json.load(response)["items"]
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(
            f"Nothing usable answers at {base} ({error}). Start `npm run mock` in frontend-v3."
        ) from error
    ids = [str(person.get("id", "")) for person in people]
    if not ids or not all(DEMO_PERSON.fullmatch(person_id) for person_id in ids):
        raise SystemExit(
            f"The people at {base} are not the mock's demo roster (U1001 to U1014). These "
            "pictures go into a public repository, so only the mock is shot."
        )


def pin_server_clock(context: BrowserContext) -> None:
    """Say the mock read the data sources at its own "now", not at the machine's.

    The data sources screen counts each next run from the time the server says it read them,
    and the mock puts the machine's real clock there: the picture would change with the day.
    """

    def restamp(route: Route) -> None:
        response = route.fetch()
        if response.ok:
            route.fulfill(response=response, json={**response.json(), "generated_at": MOCK_NOW})
        else:
            route.fulfill(response=response)

    context.route("**/admin/ops/sync-status", restamp)


def new_context(browser: Browser, shot: Shot) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": shot.get("width", DESKTOP_WIDTH), "height": HEIGHT},
        locale="en-US",
        timezone_id="UTC",
    )
    context.clock.set_fixed_time(MOCK_NOW)
    pin_server_clock(context)
    # Runs before the app does, on every load: who is acting, and under which lens.
    context.add_init_script(
        "".join(
            f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)});"
            for key, value in shot["storage"].items()
        )
    )
    return context


def settle(page: Page, extra: int) -> None:
    """Wait for the page's requests to go quiet, then a little longer for charts and tables."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass  # A screen that polls never goes quiet; the extra wait below is enough.
    page.wait_for_timeout(extra)


def locate(page: Page, target: dict[str, str]) -> Locator:
    """A button by its role and name, or an element by CSS and the text it holds."""
    if "role" in target:
        return page.get_by_role(target["role"], name=target.get("name")).first
    return page.locator(target["css"], has_text=target.get("text")).first


def run_step(page: Page, step: Step) -> None:
    if "press" in step:
        page.keyboard.press(step["press"])
    elif "type" in step:
        page.keyboard.type(step["type"])
    elif "click" in step:
        element = locate(page, step["click"])
        element.scroll_into_view_if_needed()
        element.click()
    elif "wait" in step:
        page.wait_for_timeout(step["wait"])
    else:
        raise SystemExit(f"Unknown step in {MANIFEST.name}: {step}")
    page.wait_for_timeout(300)  # Let a palette or a dialog finish opening.


def png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def take(browser: Browser, base: str, out: Path, shot: Shot) -> list[str]:
    """One picture. Returns what the browser complained about, which should be nothing."""
    context = new_context(browser, shot)
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
    page.on(
        "console",
        lambda message: (
            problems.append(f"console: {message.text}") if message.type == "error" else None
        ),
    )
    try:
        page.goto(base + shot["path"])
        settle(page, shot.get("wait", 1200))
        for step in shot.get("steps", []):
            run_step(page, step)
        page.screenshot(
            path=out / shot["name"],
            full_page=shot.get("full_page", True),
            animations="disabled",
        )
    finally:
        context.close()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Screenshots of frontend-v3 from the mock API.")
    parser.add_argument("--base", default=BASE, help=f"the running mock (default {BASE})")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where the PNGs go")
    parser.add_argument(
        "--only", nargs="*", default=[], help="shot numbers or file names, e.g. 17 26"
    )
    args = parser.parse_args()

    shots = load_shots(args.only)
    if not shots:
        raise SystemExit(f"No shot in {MANIFEST.name} matches {args.only}.")
    check_mock(args.base)
    args.out.mkdir(parents=True, exist_ok=True)

    failed = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for shot in shots:
            try:
                problems = take(browser, args.base, args.out, shot)
            except PlaywrightError as error:
                failed += 1
                print(f"FAILED {shot['name']}: {str(error).splitlines()[0]}")
                continue
            width, height = png_size(args.out / shot["name"])
            print(f"saved  {shot['name']}  {width} x {height}")
            for problem in problems:
                print(f"       {problem}")
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
