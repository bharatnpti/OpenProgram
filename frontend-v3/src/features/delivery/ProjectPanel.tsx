import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { readState } from "../../lib/readState";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { Facts, FactorsPanel, NodeHeader, Related, ReportLinks, type OwnPanel } from "./NodeBits";
import { ProjectDateStrip } from "./DeliveryStrips";
import { ProgressBlock } from "./ProgressBlock";
import { metadataFacts, reasonLine, type Finder } from "./factors";

/**
 * A project: its colour and why, where its work is tracked (Jira project,
 * repositories), related nodes, progress and tasks, and its reports. Opened
 * from a person's own part of the tree (`own`), it reads the progress that
 * part gives them (a scrum master's own project too) and links only what their
 * Delivery lists: the pods that open, and no program or workstream panel.
 */
export function ProjectPanel({
  project,
  find,
  own,
}: {
  project: DirectoryItemResponse;
  find: Finder;
  own?: OwnPanel;
}) {
  const { canReadProjectProgress } = useRole();
  const readable = own ? own.full : canReadProjectProgress;
  const progress = useQuery({
    queryKey: ["project", project.id, "progress"],
    queryFn: () => apiClient.projectProgress(project.id),
    enabled: readable,
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
        above={own?.trail}
        actions={<ReportLinks projectId={project.id} />}
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
        {own ? (
          <Related label="Pods" kind="pod" items={own.related} />
        ) : (
          <>
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
          </>
        )}
      </div>
      <div className="mb-5">
        <ProjectDateStrip projectId={project.id} readable={own ? own.full : undefined} />
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
