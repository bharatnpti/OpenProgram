"""The issues with an open Jira hygiene finding, for the timeline's ring.

A requirement added after planning without the change label is ringed on the
timeline (and in delivery_impact's markers) while Jira hygiene has a finding
open on it. A finding attaches to an issue, so the issue key is all that is
read: never the rule's owner, never who is asked.
"""

from __future__ import annotations

from core.domain.forecast import CommitmentScope
from core.domain.hygiene import HygieneState
from core.ports.hygiene import HygieneRepository


class OpenFindingKeys:
    """``DeliveryImpactService``'s ``OpenFindings``, read from the hygiene findings."""

    def __init__(self, repository: HygieneRepository) -> None:
        self._repository = repository

    async def open_keys(self, tenant_id: str, scope: CommitmentScope) -> frozenset[str]:
        """The keys with a finding open in any scope of the scope's project.

        A finding is about its issue: a pod's rule that flags an issue of this
        project rings it on the project's row too. Findings of other projects
        are left out; a pod's own finding carries no project and is kept, since
        the caller matches keys against the row's own added items only.
        """
        findings = await self._repository.list_findings(tenant_id, states={HygieneState.OPEN})
        return frozenset(
            finding.issue_key
            for finding in findings
            if finding.project_id is None or finding.project_id == scope.project_id
        )


__all__ = ["OpenFindingKeys"]
