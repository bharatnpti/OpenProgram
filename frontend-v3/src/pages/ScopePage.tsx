import { useSearchParams } from "react-router-dom";

import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { BaselineSection } from "../features/overall/BaselineSection";
import { HygieneSection } from "../features/overall/HygieneSection";
import { useReadsReadiness } from "../features/overall/queries";
import { defaultProject } from "../features/project/projectChoice";
import { useProjectOptions } from "../features/project/useProjectOptions";

/**
 * Scope control (`/scope`): what joined after planning, and whether Jira is
 * tidy, one project at a time (`?project=`). For now Overall's planning
 * baseline and Jira hygiene sections as they were; lane P builds the table
 * across projects.
 */
export function ScopePage() {
  const { overall } = useRole().access;
  const [search, setSearch] = useSearchParams();
  const choice = useProjectOptions();
  const asked = search.get("project");
  const projectId =
    (asked && choice.options.some((option) => option.id === asked) ? asked : null) ??
    defaultProject(choice.options, choice.recent) ??
    "";
  // Jira hygiene follows the readiness board's rule: a scrum master their pods' projects.
  const hygiene = useReadsReadiness(projectId);
  return (
    <>
      <SectionHeader
        title="Scope control"
        meta="What joined after planning, and whether Jira is tidy."
      />
      <PanelState
        isLoading={choice.loading}
        error={choice.error}
        isEmpty={choice.options.length === 0}
        emptyText="No project is yours to read yet."
      >
        <div className="mb-6">
          <label htmlFor="scope-project" className="sr-only">
            Project
          </label>
          <select
            id="scope-project"
            className="h-10 max-w-full rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
            value={projectId}
            onChange={(event) =>
              setSearch(
                (current) => {
                  const next = new URLSearchParams(current);
                  next.set("project", event.target.value);
                  return next;
                },
                { replace: true },
              )
            }
          >
            {choice.options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
        {projectId ? (
          <div className="op-viz grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:gap-8">
            {overall.forecast ? <BaselineSection projectId={projectId} releaseId="" /> : null}
            {hygiene ? <HygieneSection projectId={projectId} /> : null}
          </div>
        ) : null}
      </PanelState>
    </>
  );
}
