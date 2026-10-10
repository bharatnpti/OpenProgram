import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { readState } from "../../lib/readState";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { Facts, FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { ProjectDateStrip } from "./DeliveryStrips";
import { ProgressBlock } from "./ProgressBlock";
import { metadataFacts, reasonLine, type Finder } from "./factors";

/**
 * A project: its colour and why, where its work is tracked (Jira project,
 * repositories), related nodes, progress and tasks, and its reports.
 */
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
        reason={p ? reasonLine(p.factors, p.source_names, p.rag) : undefined}
        read={readState(progress)}
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
      <Facts
        facts={[
          ...metadataFacts(project.metadata),
          ...project.people.map((person): [string, string] => [
            PERSON_KEY_WORDS[person.key] ?? person.key,
            person.name ?? person.id,
          ]),
        ]}
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
      <div className="mb-5">
        <ProjectDateStrip projectId={project.id} />
      </div>
      <PanelState isLoading={progress.isLoading} error={progress.error}>
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
