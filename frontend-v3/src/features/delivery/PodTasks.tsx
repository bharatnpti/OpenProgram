import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { PodTaskDto } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { BlockerChip, DueDate, Panel, RagBadge } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { plural } from "../../lib/words";
import { etaAfterDue, startState, statementWords, viaWords } from "../today/taskUpdate";

/**
 * The tasks a pod's people hold within its remit, blocked first, each with its
 * owners, its open blockers, what an owner last said about it, its due date and
 * the owners' own ETA. On the pod's Delivery panel for a manager or admin, and
 * on a scrum master's Today for the pod picked there; it shares its query with
 * both.
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
          <table className="w-full min-w-[640px] border-collapse sm:min-w-[860px]">
            <thead>
              <tr>
                <th className={th}>Task</th>
                <th className={th}>Status</th>
                <th className={th}>Owners</th>
                <th className={th}>Blockers</th>
                <th className={th}>Last said</th>
                <th className={th}>Due</th>
                <th className={th}>ETA</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((task) => (
                <tr
                  key={task.id}
                  // Blocked work is the bad news: a red bar on its left edge, and first.
                  className={
                    task.blocked
                      ? "[&>td:first-child]:border-l-4 [&>td:first-child]:border-l-rag-red"
                      : undefined
                  }
                >
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
                      <RagBadge rag={task.rag} quiet />
                    )}
                  </td>
                  <td className={td}>{task.owners.map((o) => o.name).join(", ") || "—"}</td>
                  <td className={td}>
                    {task.open_blockers.length === 0 ? (
                      "—"
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {task.open_blockers.map((b) => (
                          <BlockerChip
                            key={b.blocker_id}
                            description={b.description}
                            ageDays={b.age_days}
                          />
                        ))}
                      </div>
                    )}
                  </td>
                  <td className={td}>
                    <LastSaid task={task} />
                  </td>
                  <td className={`${td} whitespace-nowrap`}>
                    <DueDate deadline={task.deadline} trackerStatus={task.tracker_status} />
                  </td>
                  <td className={`${td} whitespace-nowrap`}>
                    {task.eta ? (
                      <span className="inline-flex flex-wrap items-center gap-1.5">
                        <span className="font-bold text-ink">{formatDay(task.eta)}</span>
                        {/* Late only while the work is open: a done task's ETA is history. */}
                        {startState({
                          last_update: task.last_update ?? null,
                          tracker_status: task.tracker_status ?? null,
                        }) !== "done" &&
                        etaAfterDue({ my_eta: task.eta, deadline: task.deadline }) ? (
                          <RagChip tone="warning" className="h-5 px-2 text-[11px]">
                            after due
                          </RagChip>
                        ) : null}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
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

/** "In review · MR !3 open", and under it who said it, when and where; "—" when nobody has. */
function LastSaid({ task }: { task: PodTaskDto }) {
  const words = statementWords(task.last_update);
  if (!task.last_update || !words) return <>—</>;
  const who = task.last_update_by ? `${task.last_update_by} · ` : "";
  return (
    <div className="max-w-[16rem]">
      <span className="text-ink">{words.charAt(0).toUpperCase() + words.slice(1)}</span>
      <span className="block text-[12px] text-grey-secondary">
        {who}
        {formatDay(task.last_update.at)} {viaWords(task.last_update.via)}
      </span>
    </div>
  );
}
