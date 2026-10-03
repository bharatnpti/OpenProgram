from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_sync_services_loads_without_structlog_or_rich() -> None:
    """The sync workflow definitions import sync_services, and Temporal loads
    them inside its workflow sandbox, which refuses structlog's import-time
    randomness (through rich). Checked in a fresh interpreter, because this test
    process has already imported both."""
    script = (
        "import sys\n"
        "import core.application.sync_services\n"
        "import infra.workflows.jira_sync\n"
        "import infra.workflows.git_sync\n"
        "import infra.workflows.calendar_sync\n"
        "import infra.workflows.cross_person_notify_retry\n"
        "import infra.workflows.nudge\n"
        "loaded = sorted(name for name in ('structlog', 'rich') if name in sys.modules)\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(BACKEND), "PATH": ""},
    )
    assert result.stdout.strip() == ""
