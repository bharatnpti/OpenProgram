import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { PodTaskDto } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TaskRow } from "./TaskRow";

/**
 * The pod's work, blocked first: the tasks its members are assigned within
 * the workstreams it serves. The server decides what counts as the pod's, so
 * a member who also works in another pod does not bring that pod's tasks here.
 */
export function PodTasks({ podId, asOf }: { podId: string; asOf: string }) {
  const tasks = useQuery({
    queryKey: ["persona", "pod-tasks", podId, asOf],
    queryFn: () => apiClient.podTasks(podId, asOf),
  });
  const list = tasks.data?.tasks ?? [];
  const blocked = list.filter((task) => task.blocked).length;

  return (
    <Card padding="p-0">
      <div className="flex items-baseline justify-between gap-4 px-6 pt-6 pb-2">
        <h3 className="text-[18px] font-bold">Tasks</h3>
        {list.length > 0 ? (
          <span className="text-[13px] font-bold text-grey-secondary">
            {list.length === 1 ? "1 task" : `${list.length} tasks`}
            {blocked > 0 ? ` · ${blocked} blocked` : ""}
          </span>
        ) : null}
      </div>
      {tasks.isError && !tasks.data ? (
        <p className="px-6 pb-6 text-[14px] text-grey-secondary">
          The pod&apos;s tasks could not be loaded.
        </p>
      ) : !tasks.data ? (
        <p className="px-6 pb-6 text-[14px] text-grey-secondary">Loading the pod&apos;s tasks…</p>
      ) : list.length === 0 ? (
        <p className="px-6 pb-6 text-[14px] text-grey-secondary">
          No tasks assigned to this pod&apos;s members.
        </p>
      ) : (
        list.map((task) => (
          <TaskRow key={task.id} task={task} owner={ownerNames(task)}>
            {task.open_blockers.map((blocker) => (
              <div
                key={blocker.blocker_id}
                className="mt-1.5 flex items-start gap-2 text-[13px] font-bold text-rag-red"
              >
                <span className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full bg-rag-red" />
                <span>
                  Blocker · open {blocker.age_days}d · {blocker.description}
                </span>
              </div>
            ))}
          </TaskRow>
        ))
      )}
    </Card>
  );
}

function ownerNames(task: PodTaskDto): string {
  const names = task.owners.map((owner) => owner.name);
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}
