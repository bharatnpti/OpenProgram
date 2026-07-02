from __future__ import annotations

import pytest

from tests.bdd import (
    fixtures,
    steps_common,
    steps_cross_person_requests,
    steps_redis_persistence,
    steps_reply_parsing,
    steps_schedule,
    steps_ui,
)

# `pytest_plugins` is restricted to the repo's top-level conftest.py in
# modern pytest, so fixtures/step definitions are split across modules and
# merged into this conftest's namespace instead: pytest discovers fixtures
# (and pytest-bdd's given/when/then, which are fixtures under the hood) by
# scanning a conftest module's namespace, regardless of whether a name was
# defined locally or imported. `globals().update` (unlike `import *`) also
# picks up the leading-underscore step function names used throughout.
for _module in (
    fixtures,
    steps_common,
    steps_cross_person_requests,
    steps_reply_parsing,
    steps_redis_persistence,
    steps_schedule,
    steps_ui,
):
    globals().update(
        {name: value for name, value in vars(_module).items() if not name.startswith("__")}
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``@ms_e2e_XXX`` Gherkin tags (see the feature files under
    ``features/``) as pytest marks so they can filter runs, e.g.
    ``pytest backend/tests/bdd -m ms_e2e_005``, without unknown-mark warnings.
    """
    for index in range(1, 46):
        config.addinivalue_line(
            "markers", f"ms_e2e_{index:03d}: Mock Slack E2E matrix row {index:03d}"
        )
    config.addinivalue_line("markers", "ui_bdd_scenario: playwright-backed frontend BDD scenario")
