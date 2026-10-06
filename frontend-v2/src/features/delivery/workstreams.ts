import type { DirectoryItemResponse } from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/*
 * Workstreams are optional. Pods are the one grouping a tenant must set up;
 * a workstream splits a project's scope only where several teams share it.
 * The directory lists only the workstreams in use -- those holding a task or
 * work item on the viewed day -- so the navigator, heat rows, palette and
 * link chips never show an empty one. Only a direct link reads one, marked
 * `in_use: false`, and its panel says why there is nothing in it.
 */

/** The navigator's heading: the workstream level only when one is in use. */
export function hierarchyLabel(workstreams: Pick<DirectoryItemResponse, "id">[]): string {
  return workstreams.length > 0
    ? "Hierarchy · program → project → workstream"
    : "Hierarchy · program → project";
}

/** A workstream read straight from a link that holds no work on the day. */
export function isEmptyWorkstream(
  kind: string,
  item: Pick<DirectoryItemResponse, "in_use"> | undefined,
): boolean {
  // An older backend sends no `in_use`; everything it lists is shown.
  return kind === "workstream" && item?.in_use === false;
}

/** What an empty workstream's panel says in place of a status and a 0% ring. */
export const EMPTY_WORKSTREAM_LEAD = "No work is in this workstream yet.";
export const EMPTY_WORKSTREAM_DETAIL =
  "Workstreams are optional; pods are enough unless several teams share one piece of scope. " +
  "This one shows in Delivery and Today once an admin links a task or work item to it, " +
  "under Admin › Links.";

/** "2 projects · 3 pods roll up into this program.", naming workstreams only when there are any. */
export function rollupCountsLine(counts: {
  projects: number;
  workstreams: number;
  pods: number;
}): string {
  const parts = [
    plural(counts.projects, "project"),
    ...(counts.workstreams > 0 ? [plural(counts.workstreams, "workstream")] : []),
    plural(counts.pods, "pod"),
  ];
  return `${parts.join(" · ")} roll up into this program.`;
}

function plural(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`}`;
}
