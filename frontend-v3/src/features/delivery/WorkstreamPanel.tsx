import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { readState } from "../../lib/readState";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { Facts, FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { ProgressBlock } from "./ProgressBlock";
import { metadataFacts, reasonLine, type Finder } from "./factors";

/**
 * A workstream: what its metadata says (type, phase, target date, its
 * repositories), the people who run it (an id that matches no member is shown
 * as the id, never as a name), then its progress.
 */
export function WorkstreamPanel({
  workstream,
  find,
}: {
  workstream: DirectoryItemResponse;
  find: Finder;
}) {
  const { canReadProjectProgress } = useRole();
  const progress = useQuery({
    queryKey: ["workstream", workstream.id, "progress"],
    queryFn: () => apiClient.workstreamProgress(workstream.id),
    enabled: canReadProjectProgress,
  });
  const p = progress.data;
  const facts: [string, string][] = [
    ...metadataFacts(workstream.metadata),
    ...workstream.people.map((person): [string, string] => [
      PERSON_KEY_WORDS[person.key] ?? person.key,
      person.name ?? person.id,
    ]),
  ];

  return (
    <>
      <NodeHeader
        kind="Workstream"
        name={workstream.name}
        rag={p?.rag ?? workstream.rag}
        reason={
          canReadProjectProgress
            ? p
              ? reasonLine(p.factors, p.source_names, p.rag)
              : undefined
            : "The reasons behind a workstream's status open for a product owner, manager, executive or admin."
        }
        read={readState(progress)}
      />
      <Facts facts={facts} />
      <div className="mb-5 grid gap-2">
        <Related
          label="Project"
          kind="project"
          items={workstream.project_ids.map((id) => find("project", id))}
        />
        <Related label="Pods" kind="pod" items={workstream.pod_ids.map((id) => find("pod", id))} />
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
