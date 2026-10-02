import type { ReactNode } from "react";

import type { Rag, StatusSource } from "../../api/schema";
import { RagChip } from "../../components/ui/RagChip";
import { toneForRag } from "../../lib/status";

/** What a task row shows. Project, workstream and pod tasks all carry it. */
export type TaskRowTask = {
  id: string;
  name: string;
  rag: Rag;
  source: StatusSource;
  confidence: number | null;
};

/**
 * One task in a Delivery panel: its name, id, owner when known, where its
 * status came from and how sure that is. `children` adds lines below, such as
 * the blockers attributed to it.
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
      <RagChip tone={toneForRag(task.rag)}>{task.rag}</RagChip>
    </div>
  );
}
