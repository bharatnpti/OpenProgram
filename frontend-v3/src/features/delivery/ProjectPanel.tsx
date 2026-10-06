import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { ProgressBlock } from "./ProgressBlock";
import { reasonLine, type Finder } from "./factors";

/** A project: its colour and why, related nodes, progress and tasks, and its reports. */
export function ProjectPanel({ project, find }: { project: DirectoryItemResponse; find: Finder }) {
  const { canReadProjectProgress } = useRole();
  const progress = useQuery({
    queryKey: ["project", project.id, "progress"],
    queryFn: () => apiClient.projectProgress(project.id),
    enabled: canReadProjectProgress,
  });
  const p = progress.data;

  return (
    <>
      <NodeHeader
        kind="Project"
        name={project.name}
        rag={p?.rag ?? project.rag}
        reason={p ? reasonLine(p.factors, p.source_names) : undefined}
        actions={
          <>
            <Link
              to={`/reports/${project.id}/daily`}
              className="inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-bold text-white no-underline"
            >
              Daily report
            </Link>
            <Link
              to={`/reports/${project.id}/overall`}
              className="inline-flex h-9 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline"
            >
              Overall
            </Link>
          </>
        }
      />
      <div className="mb-5 grid gap-2">
        <Related
          label="Program"
          kind="program"
          items={project.program_ids.map((id) => find("program", id))}
        />
        <Related
          label="Workstreams"
          kind="workstream"
          items={project.workstream_ids.map((id) => find("workstream", id))}
        />
        <Related label="Pods" kind="pod" items={project.pod_ids.map((id) => find("pod", id))} />
      </div>
      <PanelState
        locked={!canReadProjectProgress}
        needs="a product owner, manager, executive or admin"
        isLoading={progress.isLoading}
        error={progress.error}
      >
        {p ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <ProgressBlock progress={p} />
            <FactorsPanel factors={p.factors} names={p.source_names} />
          </div>
        ) : null}
      </PanelState>
    </>
  );
}
