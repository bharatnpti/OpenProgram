from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

from infra.adapters.workflows import temporal as temporal_workflows

BACKEND = Path(__file__).resolve().parents[2]


def test_workflow_definitions_load_without_structlog_or_rich() -> None:
    """Temporal loads the workflow definitions, and every module they import,
    inside its workflow sandbox, which refuses structlog's import-time
    randomness (through rich). The sync workflows import sync_services; the
    drift scan reaches writeback_service through risk_service and checkin_drift.
    Checked in a fresh interpreter, because this test process has already
    imported both."""
    script = (
        "import sys\n"
        "import core.application.sync_services\n"
        "import core.application.writeback_service\n"
        "import infra.workflows.jira_sync\n"
        "import infra.workflows.git_sync\n"
        "import infra.workflows.calendar_sync\n"
        "import infra.workflows.cross_person_notify_retry\n"
        "import infra.workflows.nudge\n"
        "import infra.workflows.drift_scan\n"
        "import infra.adapters.workflows.temporal\n"
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


async def test_temporal_workflows_pass_the_worker_sandbox_validation() -> None:
    """The check the Temporal worker makes for each workflow before it polls,
    where the integration job's heartbeat test failed: the workflow's module is
    imported again inside the sandbox, which refuses non-deterministic calls
    at import (structlog's randomness through rich, for one). It needs no
    server, only the running event loop the worker also has."""
    definitions = [
        _Definition.must_from_class(value)
        for value in vars(temporal_workflows).values()
        if isinstance(value, type) and _Definition.from_class(value) is not None
    ]
    assert definitions
    # The sandbox imports a workflow's module afresh for every workflow it
    # validates, so one workflow per module covers that module's import-time code.
    one_per_module = {definition.cls.__module__: definition for definition in definitions}
    runner = SandboxedWorkflowRunner()
    for definition in one_per_module.values():
        runner.prepare_workflow(definition)
