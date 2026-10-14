import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { podPath, workstreamPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Panel, RagDot } from "../../components/ui/Bits";
import { Fold } from "../../components/viz/Fold";
import { ragWords } from "../../lib/status";
import { PERSON_KEY_WORDS, plural } from "../../lib/words";
import { Facts, FactorsPanel } from "../delivery/NodeBits";
import { ProgressBlock } from "../delivery/ProgressBlock";
import { metadataFacts } from "../delivery/factors";
import type { WorkPod } from "./workPods";

/**
 * The work under the project, as Delivery's project panel had it: where it is
 * tracked (Jira project, repositories), its people (never for an executive,
 * who sees no names), its pods as links to their pages, workstreams only when
 * in use, the progress and tasks, and every reason behind today's health,
 * folded. The dates and the colour are said above, once. `namesOnly` is a
 * developer's form: the pods by name, their own one open, nothing else.
 */
export function ProjectWork({
  project,
  pods,
  workstreams,
  readable,
  namesOnly = false,
}: {
  project: DirectoryItemResponse | undefined;
  pods: WorkPod[];
  workstreams: DirectoryItemResponse[];
  /** The project's progress is the person's to read (`READ_PROJECT_PROGRESS`, or their own). */
  readable: boolean;
  namesOnly?: boolean;
}) {
  const { access } = useRole();
  // An executive reads every project but no pod: the one role with names kept out.
  const executive = access.pages.portfolio && !access.pages.pods;
  const progress = useQuery({
    queryKey: ["project", project?.id ?? "", "progress"],
    queryFn: () => apiClient.projectProgress(project?.id ?? ""),
    enabled: readable && !namesOnly && Boolean(project),
  });
  const p = progress.data;
  const facts: [string, string][] =
    project && !namesOnly
      ? [
          ...metadataFacts(project.metadata),
          ...(executive
            ? []
            : project.people.map((person): [string, string] => [
                PERSON_KEY_WORDS[person.key] ?? person.key,
                person.name ?? person.id,
              ])),
        ]
      : [];
  const inUse = namesOnly ? [] : workstreams.filter((item) => item.in_use !== false);

  return (
    <section id="work" aria-labelledby="work-h" className="scroll-mt-28">
      <SectionHeader
        id="work-h"
        title="Work"
        meta={
          namesOnly
            ? "The pods on this project"
            : "Where it is tracked, the pods on it, and how its work stands today"
        }
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        {facts.length > 0 ? <Facts facts={facts} /> : null}
        <Panel title="Pods" note={plural(pods.length, "pod", "pods")}>
          {pods.length === 0 ? (
            <p className="text-[14px] text-grey-secondary">No pod works on this project.</p>
          ) : (
            <ul className="grid gap-0.5">
              {pods.map((pod) => (
                <li key={pod.id}>
                  {pod.opens ? (
                    <Link
                      to={podPath(pod.id)}
                      className="flex min-h-10 items-center gap-2 rounded-xl px-2 py-1.5 text-[15px] font-bold text-ink no-underline hover:bg-grey-fill"
                    >
                      <RagDot rag={pod.rag} />
                      <span className="min-w-0 truncate">{pod.name}</span>
                      <span className="sr-only">, today {ragWords(pod.rag)}</span>
                    </Link>
                  ) : (
                    // A pod the person reads by name only: no colour, no link.
                    <span className="flex min-h-10 items-center gap-2 px-2 py-1.5 text-[15px] text-grey-secondary">
                      <span aria-hidden className="h-2.5 w-2.5 flex-none" />
                      <span className="min-w-0 truncate">{pod.name}</span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        {inUse.length > 0 && project ? (
          <Panel title="Workstreams" note={plural(inUse.length, "workstream", "workstreams")}>
            <ul className="flex flex-wrap gap-2">
              {inUse.map((item) => (
                <li key={item.id}>
                  <Link
                    to={workstreamPath(item.id, [project.id])}
                    className="inline-flex items-center rounded-full border border-grey-border px-3 py-1 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
                  >
                    {item.name}
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
        {readable && !namesOnly ? (
          <PanelState isLoading={progress.isLoading} error={progress.error}>
            {p ? (
              <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
                <ProgressBlock progress={p} />
                {/* An executive reads the colour, never the people behind its reasons. */}
                {!executive && p.factors.length > 0 ? (
                  <Fold summary="Every reason behind today's health">
                    <FactorsPanel factors={p.factors} names={p.source_names} always />
                  </Fold>
                ) : null}
              </div>
            ) : null}
          </PanelState>
        ) : null}
      </div>
    </section>
  );
}
