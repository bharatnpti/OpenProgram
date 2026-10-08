import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel, RagBadge } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { plural } from "../../lib/words";

/**
 * The tasks a pod's people hold within its remit, blocked first, each with its
 * owners, its open blockers and its due date. On the pod's Delivery panel for a
 * manager or admin, and on a scrum master's Today for the pod picked there; it
 * shares its query with both.
 */
export function PodTasksPanel({ podId }: { podId: string }) {
  const tasks = useQuery({
    queryKey: ["pod", podId, "tasks"],
    queryFn: () => apiClient.podTasks(podId),
  });
  const sorted = [...(tasks.data?.tasks ?? [])].sort(
    (a, b) => Number(b.blocked) - Number(a.blocked) || ragSeverity(b.rag) - ragSeverity(a.rag),
  );

  return (
    <Panel
      title="Tasks held by the pod's people"
      note={tasks.data ? plural(sorted.length, "task", "tasks") : undefined}
    >
      <PanelState
        isLoading={tasks.isLoading}
        error={tasks.error}
        onRetry={() => void tasks.refetch()}
        isEmpty={sorted.length === 0}
        emptyText="No tasks are assigned to this pod's members within its remit."
      >
        <TableBox>
          <table className="w-full min-w-[500px] border-collapse sm:min-w-[680px]">
            <thead>
              <tr>
                <th className={th}>Task</th>
                <th className={th}>Status</th>
                <th className={th}>Owners</th>
                <th className={th}>Blockers</th>
                <th className={th}>Due</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((task) => (
                <tr key={task.id}>
                  <td className={td}>
                    {/* Capped on a phone, so the status beside it is in the first screenful. */}
                    <div className="max-w-[12rem] sm:max-w-none">
                      <span className="font-bold">{task.name}</span>
                      <span className="block text-[12px] text-grey-secondary">
                        {task.id}
                        {task.tracker_status ? ` · ${task.tracker_status}` : ""}
                      </span>
                    </div>
                  </td>
                  <td className={td}>
                    {task.blocked ? (
                      <RagChip tone="danger" className="h-6 px-2.5 text-[12px]">
                        blocked
                      </RagChip>
                    ) : (
                      <RagBadge rag={task.rag} />
                    )}
                  </td>
                  <td className={td}>{task.owners.map((o) => o.name).join(", ") || "—"}</td>
                  <td className={td}>
                    {task.open_blockers.length === 0
                      ? "—"
                      : task.open_blockers
                          .map((b) => `${b.description} (${b.age_days}d)`)
                          .join("; ")}
                  </td>
                  <td className={`${td} whitespace-nowrap`}>{formatDay(task.deadline)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableBox>
        <p className="mt-2 text-[12px] text-grey-secondary">
          Counted by who holds each task. The open count under the pod&apos;s dates counts the
          requirements linked to the pod, so the two can differ.
        </p>
      </PanelState>
    </Panel>
  );
}
