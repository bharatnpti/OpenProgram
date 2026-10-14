import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { BaselineSection } from "../features/overall/BaselineSection";
import { HygieneSection } from "../features/overall/HygieneSection";
import { useReadsReadiness } from "../features/overall/queries";
import { BaselineTable, HygieneTable } from "../features/scope/ScopeTables";
import { planningWords } from "../features/scope/scopeWords";

/**
 * Scope control (`/scope`): what joined each project after planning, and whether
 * Jira is tidy, in one read across projects (`GET /portfolio/scope`). A
 * project picked (`?project=`) opens its own planning baseline and Jira hygiene
 * below, where they are acted on, as each reader's project board allows.
 */
export function ScopePage() {
  const { overall } = useRole().access;
  const [search, setSearch] = useSearchParams();
  const scope = useQuery({
    queryKey: ["portfolio", "scope"],
    queryFn: () => apiClient.portfolioScope(),
  });
  const asked = search.get("project");
  const picked =
    asked && scope.data?.projects.some((project) => project.id === asked) ? asked : null;
  const project = scope.data?.projects.find((item) => item.id === picked) ?? null;
  // Jira hygiene follows the readiness board's rule: a scrum master their pods' projects.
  const hygiene = useReadsReadiness(picked ?? "");

  const pick = (projectId: string) =>
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (next.get("project") === projectId) next.delete("project");
        else next.set("project", projectId);
        return next;
      },
      { replace: true },
    );

  return (
    <div className="op-viz">
      <SectionHeader
        title="Scope control"
        meta="What joined each project after planning, and whether Jira is tidy."
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
            <BaselineTable scope={scope.data} picked={picked} onPick={pick} />
            <HygieneTable scope={scope.data} />
            {project ? (
              <section aria-labelledby="scope-project" className="grid gap-6">
                <div>
                  <h3 id="scope-project" className="text-[18px] font-extrabold">
                    {project.name}
                  </h3>
                  {planningWords(project) ? (
                    <p className="mt-1 text-[13px] text-grey-secondary">{planningWords(project)}</p>
                  ) : null}
                </div>
                {overall.forecast ? <BaselineSection projectId={project.id} releaseId="" /> : null}
                {hygiene ? <HygieneSection projectId={project.id} /> : null}
              </section>
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
