import type { ReactNode } from "react";

import type { Rag, StatusSource } from "../../api/schema";
import { RagChip } from "../../components/ui/RagChip";
import { toneForRag } from "../../lib/status";
import { taskChips } from "./taskChips";

/** What a task row shows. Project, workstream and pod tasks all carry it. */
export type TaskRowTask = {
  id: string;
  name: string;
  rag: Rag;
  source: StatusSource;
  confidence: number | null;
  /** The issue tracker's own status, e.g. "In Progress"; absent from older servers. */
  tracker_status?: string | null;
};

/**
 * One task in a Delivery panel: its name, id, owner when known, where its
 * status came from and how sure that is, and -- for a tracker's task -- the
 * tracker's own status beside its colour. `children` adds lines below, such
 * as the blockers attributed to it.
 */
export function TaskRow({
  task,
  owner,
  children,
}: {
  task: TaskRowTask;
  owner?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-t border-grey-fill px-6 py-4 first:border-t-0">
      <div className="min-w-0">
        <div className="truncate text-[15px] font-bold">{task.name}</div>
        <div className="mt-0.5 text-[13px] text-grey-secondary">
          {task.id}
          {owner ? ` · ${owner}` : ""} · {task.source}
          {task.confidence != null ? ` · ${Math.round(task.confidence * 100)}%` : ""}
        </div>
        {children}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {taskChips(task).map((chip) =>
          chip.kind === "tracker" ? (
            <span
              key="tracker"
              title={chip.title}
              className="inline-flex h-7 items-center whitespace-nowrap rounded-full border border-grey-border px-3 text-[13px] font-bold text-grey-body"
            >
              {chip.label}
            </span>
          ) : (
            <RagChip key="rag" tone={toneForRag(chip.rag)}>
              {chip.label}
            </RagChip>
          ),
        )}
      </div>
    </div>
  );
}
