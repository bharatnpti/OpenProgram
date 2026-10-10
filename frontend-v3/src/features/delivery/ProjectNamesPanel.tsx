import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Panel, RagDot } from "../../components/ui/Bits";
import { ragWords } from "../../lib/status";
import { plural } from "../../lib/words";
import { NodeHeader, ReportLinks } from "./NodeBits";
import type { OwnNode } from "./ownTree";

/**
 * A project of the person's whose own data is not theirs to read (a
 * developer's): its name, its reports, and every pod under it by name. The
 * pods that open are links with their colour; the others are names, muted,
 * with nothing to open and no colour.
 */
export function ProjectNamesPanel({
  project,
  pods,
  trail,
}: {
  project: { id: string; name: string };
  pods: OwnNode[];
  trail?: ReactNode;
}) {
  return (
    <>
      <NodeHeader
        kind="Project"
        name={project.name}
        rag={null}
        status={false}
        above={trail}
        actions={<ReportLinks projectId={project.id} />}
      />
      <Panel title="Pods" note={plural(pods.length, "pod", "pods")}>
        {pods.length === 0 ? (
          <p className="text-[14px] text-grey-secondary">No pod works on this project.</p>
        ) : (
          <ul className="grid gap-0.5">
            {pods.map((pod) => (
              <li key={pod.id}>
                {pod.opens ? (
                  <Link
                    to={`/delivery/pod/${encodeURIComponent(pod.id)}`}
                    className="flex items-center gap-2 rounded-xl px-2 py-1.5 text-[15px] font-bold text-ink no-underline hover:bg-grey-fill"
                  >
                    <RagDot rag={pod.rag} />
                    <span className="min-w-0 truncate">{pod.name}</span>
                    <span className="sr-only">, {ragWords(pod.rag)}</span>
                  </Link>
                ) : (
                  <span className="flex items-center gap-2 px-2 py-1.5 text-[15px] text-grey-secondary">
                    <span aria-hidden className="h-2.5 w-2.5 flex-none" />
                    <span className="min-w-0 truncate">{pod.name}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
