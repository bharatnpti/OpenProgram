import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { formatDate } from "../../lib/format";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { ProgressBlock } from "./ProgressBlock";
import { reasonLine, type Finder } from "./factors";

/**
 * A workstream: type, phase, target date and the people who run it (an id that
 * matches no member is shown as the id, never as a name), then its progress.
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
  const m = workstream.metadata;
  const text = (v: unknown) => (typeof v === "string" && v ? v : null);
  const facts = [
    ["Type", text(m.type)],
    ["Phase", text(m.phase)],
    ["Target date", text(m.target_date) ? formatDate(text(m.target_date)) : null],
    ...workstream.people.map((person) => [
      PERSON_KEY_WORDS[person.key] ?? person.key,
      person.name ?? person.id,
    ]),
  ].filter((pair): pair is [string, string] => Boolean(pair[1]));

  return (
    <>
      <NodeHeader
        kind="Workstream"
        name={workstream.name}
        rag={p?.rag ?? workstream.rag}
        reason={p ? reasonLine(p.factors, p.source_names) : undefined}
      />
      {facts.length > 0 ? (
        <dl className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {facts.map(([label, value]) => (
            <div key={label} className="rounded-2xl bg-grey-fill px-3 py-2">
              <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                {label}
              </dt>
              <dd className="mt-0.5 text-[14px] font-bold">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
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
