import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { projectTabPath } from "../../app/links";
import { PanelState } from "../../components/PanelState";
import { TabIntro } from "../../features/hub/TabIntro";
import { BaselineTable, HygieneTable } from "../../features/scope/ScopeTables";

/**
 * All projects › Scope control (`/portfolio/scope`): what joined each project
 * after planning, and whether Jira is tidy, in one read across projects
 * (`GET /portfolio/scope`). A project's row opens its own Scope control, where
 * its planning baseline and Jira hygiene are acted on.
 */
export function PortfolioScope() {
  const navigate = useNavigate();
  const scope = useQuery({
    queryKey: ["portfolio", "scope"],
    queryFn: () => apiClient.portfolioScope(),
  });
  return (
    <div>
      <TabIntro
        tab="scope"
        meta="What joined each project after planning, and whether Jira is tidy. A project opens its own."
      />
      <PanelState
        isLoading={scope.isLoading}
        error={scope.error}
        onRetry={() => void scope.refetch()}
        isEmpty={scope.data?.projects.length === 0}
        emptyText="No project is yours to read yet."
      >
        {scope.data ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
            <BaselineTable
              scope={scope.data}
              picked={null}
              onPick={(projectId) => navigate(projectTabPath(projectId, "scope"))}
            />
            <HygieneTable scope={scope.data} />
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
