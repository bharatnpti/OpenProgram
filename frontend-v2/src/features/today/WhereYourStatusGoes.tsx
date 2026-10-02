import type { ReactNode } from "react";

import { Card } from "../../components/ui/Card";
import { RagChip } from "../../components/ui/RagChip";
import { toneForRag } from "../../lib/status";
import type { StatusRollups } from "./statusRollups";

const KIND_LABEL: Record<string, string> = {
  pod: "Pod",
  project: "Project",
  program: "Program",
};

/**
 * The pods, projects and programs this developer's check-in feeds, each with
 * its current colour.
 *
 * It replaces a fixed "Why this matters" paragraph that read the same every
 * day. This one changes with the developer's own pods and with the rollups
 * themselves, and says plainly when there is no pod for a check-in to reach.
 */
export function WhereYourStatusGoes({
  rollups,
  animateDelay,
}: {
  rollups: StatusRollups;
  animateDelay?: number;
}) {
  // A refused read leaves nothing true to show, and a permission error is no
  // use on the screen a developer opens every morning.
  if (rollups.forbidden) return null;

  const items = [...rollups.pods, ...rollups.projects, ...rollups.programs];

  return (
    <Card padding="p-0" animateDelay={animateDelay}>
      <div className="px-5 pt-5 pb-2">
        <h2 className="text-[18px] font-bold">Where your status goes</h2>
      </div>
      {rollups.isError ? (
        <Note>
          Your pod and project could not be loaded:{" "}
          {rollups.error instanceof Error ? rollups.error.message : "unknown error"}
        </Note>
      ) : rollups.isPending ? (
        <Note>Loading…</Note>
      ) : items.length === 0 ? (
        <Note>
          You're not in a pod yet, so your check-in doesn't roll up anywhere. An admin can add you
          to one.
        </Note>
      ) : (
        items.map((item) => (
          <div
            key={`${item.kind}:${item.id}`}
            className="flex items-center gap-3.5 border-t border-grey-fill px-5 py-3.5"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-bold">{item.name}</div>
              <div className="mt-0.5 text-[13px] text-grey-secondary">
                {KIND_LABEL[item.kind] ?? item.kind}
                {item.source ? ` · ${item.source}` : ""}
              </div>
            </div>
            <RagChip tone={toneForRag(item.rag)}>{item.rag ?? "no rollup yet"}</RagChip>
          </div>
        ))
      )}
    </Card>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <div className="px-5 py-6 text-sm text-grey-secondary">{children}</div>;
}
