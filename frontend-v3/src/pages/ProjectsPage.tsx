import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import { PanelState, SectionHeader } from "../components/PanelState";
import { RagChip } from "../components/ui/RagChip";
import { ragSeverity, toneForRag } from "../lib/status";

/** Every project, worst first, each opening on its Daily report. */
export function ProjectsPage() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: apiClient.projects });
  const reports = useQuery({
    queryKey: ["day-reports", "all"],
    queryFn: () => apiClient.dayReports(),
  });

  const sorted = [...(projects.data ?? [])].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  const reportCount = (projectId: string) =>
    (reports.data ?? []).filter((report) => report.project_id === projectId).length;

  return (
    <>
      <SectionHeader
        title="Projects"
        meta="Pick a project to read today's report or its overall state."
      />
      <PanelState
        needs="anyone with a member record"
        isLoading={projects.isLoading}
        error={projects.error}
        onRetry={() => void projects.refetch()}
        isEmpty={sorted.length === 0}
        emptyText="No projects are configured yet. An admin adds them in the console under Admin → Entities."
      >
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {sorted.map((project) => {
            const count = reportCount(project.id);
            return (
              <li key={project.id}>
                <Link
                  to={`/projects/${project.id}/daily`}
                  className="flex h-full flex-col gap-3 rounded-3xl border border-grey-border p-5 text-ink no-underline hover:shadow-op-hover"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-[17px] font-extrabold">{project.name}</span>
                    <RagChip tone={toneForRag(project.rag)} dot>
                      {project.rag ?? "unknown"}
                    </RagChip>
                  </div>
                  <span className="text-[13px] text-grey-secondary">
                    {count === 0
                      ? "No day report set up"
                      : count === 1
                        ? "1 day report"
                        : `${count} day reports`}
                    {project.pod_ids.length > 0
                      ? ` · ${project.pod_ids.length} ${project.pod_ids.length === 1 ? "pod" : "pods"}`
                      : ""}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </PanelState>
    </>
  );
}
