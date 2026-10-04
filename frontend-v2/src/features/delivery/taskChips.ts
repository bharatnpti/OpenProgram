import type { Rag } from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/** One chip at the end of a task row. */
export type TaskChip =
  { kind: "tracker"; label: string; title: string } | { kind: "rag"; label: string; rag: Rag };

/**
 * The chips a task row ends with: the issue tracker's own status, when the
 * task came from one, then the task's colour.
 *
 * The colour says how the work is going, and RAG has no "in progress", so an
 * In Progress ticket reads unknown. Without the tracker's status a manager
 * moving a ticket in Jira changed nothing a row showed. The server sends it
 * only for tracker tasks; a seeded task's status is already its colour.
 */
export function taskChips(task: { rag: Rag; tracker_status?: string | null }): TaskChip[] {
  const status = task.tracker_status?.trim();
  const rag: TaskChip = { kind: "rag", label: task.rag, rag: task.rag };
  if (!status) return [rag];
  return [{ kind: "tracker", label: status, title: `Status in the issue tracker: ${status}` }, rag];
}
