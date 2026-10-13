import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { PanelState, SectionHeader } from "../components/PanelState";
import { useProjectOptions } from "../features/project/useProjectOptions";
import { WarningGroup } from "../features/warnings/WarningGroups";
import { countLine, shownGroups } from "../features/warnings/warningWords";
import { formatDay } from "../lib/format";

/**
 * Early warnings (`/warnings`): what Jira and Git say, whatever anyone reports,
 * grouped by kind with a count each, in one read across the projects the person
 * reads (`GET /portfolio/warnings`, D90). Every warning has an owner; findings
 * attach to work items and nobody is ranked. `?project=` narrows it to one.
 */
export function RisksPage() {
  const [search, setSearch] = useSearchParams();
  const projectId = search.get("project");
  const choice = useProjectOptions();
  const warnings = useQuery({
    queryKey: ["portfolio", "warnings", projectId ?? ""],
    queryFn: () => apiClient.portfolioWarnings({ projectId }),
  });
  const view = warnings.data;
  const seenSince = view?.seen_since;
  return (
    <div className="op-viz">
      <SectionHeader
        title="Early warnings"
        meta={
          <>
            What Jira and Git say, whatever anyone reports. Every warning has an owner; findings
            attach to work items, and nobody is ranked.{view ? ` ${countLine(view)}` : ""}
            {seenSince
              ? ` Ages of disagreements count from ${formatDay(seenSince)}, when they were first recorded.`
              : ""}
          </>
        }
      />
      {choice.options.length > 1 ? (
        <div className="mb-5">
          <label htmlFor="warnings-project" className="mb-1 block text-[12px] font-bold">
            Project
          </label>
          <select
            id="warnings-project"
            value={projectId ?? ""}
            onChange={(event) =>
              setSearch(
                (current) => {
                  const next = new URLSearchParams(current);
                  if (event.target.value) next.set("project", event.target.value);
                  else next.delete("project");
                  return next;
                },
                { replace: true },
              )
            }
            className="h-10 max-w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] font-bold text-ink"
          >
            <option value="">Every project</option>
            {choice.options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <PanelState
        isLoading={warnings.isLoading}
        error={warnings.error}
        onRetry={() => void warnings.refetch()}
      >
        {view ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            {shownGroups(view).map((group) => (
              <WarningGroup key={group.kind} group={group} seenSince={seenSince} />
            ))}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
