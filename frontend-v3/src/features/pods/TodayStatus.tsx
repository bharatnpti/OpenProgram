import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { PodTaskDto } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { BlockerChip, DueDate, Panel, RagBadge } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { jiraTodayLine } from "../../lib/observedWords";
import { ragSeverity } from "../../lib/status";
import { cn } from "../../lib/utils";
import { plural } from "../../lib/words";
import { CellLabel, StackedTable, stackCell, stackRow } from "../coordination/StackedTable";
import { etaAfterDue, startState, statementWords, viaWords } from "../today/taskUpdate";

/**
 * Today's status: each task the pod's people hold within its remit, blocked
 * first, as Ticket · Who · Jira today · What they said · ETA or blocker. What
 * Jira says is never anyone's word; a colour worked out from Jira and Git is
 * marked inferred, and is never green on its own. The Delivery pod panel's
 * task table, in the prototype's columns.
 */
export function TodayStatus({ podId, day }: { podId: string; day: string }) {
  const tasks = useQuery({
    queryKey: ["pod", podId, "tasks"],
    queryFn: () => apiClient.podTasks(podId),
  });
  const sorted = [...(tasks.data?.tasks ?? [])].sort(
    (a, b) => Number(b.blocked) - Number(a.blocked) || ragSeverity(b.rag) - ragSeverity(a.rag),
  );
  return (
    <Panel
      title={`Today's status`}
      note={tasks.data ? `${plural(sorted.length, "task", "tasks")} ${day}` : undefined}
    >
      <PanelState
        isLoading={tasks.isLoading}
        error={tasks.error}
        onRetry={() => void tasks.refetch()}
        isEmpty={sorted.length === 0}
        emptyText="No tasks are assigned to this pod's people within its remit."
      >
        <StackedTable
          caption="Today's status of the pod's tasks"
          columns={["Ticket", "Who", "Jira today", "What they said", "ETA or blocker"]}
        >
          {sorted.map((task) => (
            <tr
              key={task.id}
              className={cn(
                stackRow,
                // Blocked work is the bad news: a red bar on its left edge, and first.
                task.blocked &&
                  "max-md:border-l-4 max-md:border-l-rag-red md:[&>td:first-child]:border-l-4 md:[&>td:first-child]:border-l-rag-red",
              )}
            >
              <td className={cn(stackCell, "md:max-w-[16rem]")}>
                <span className="block font-bold text-ink">{task.name}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px] text-grey-secondary">
                  {task.id}
                  <TaskState task={task} />
                </span>
              </td>
              <td className={stackCell}>
                <CellLabel>Who</CellLabel>
                {task.owners.map((owner) => owner.name).join(", ") || "—"}
              </td>
              <td className={cn(stackCell, "md:max-w-[14rem]")}>
                <CellLabel>Jira today</CellLabel>
                <JiraToday task={task} today={tasks.data?.as_of ?? ""} />
              </td>
              <td className={cn(stackCell, "md:max-w-[16rem]")}>
                <CellLabel>What they said</CellLabel>
                <Said task={task} />
              </td>
              <td className={stackCell}>
                <CellLabel>ETA or blocker</CellLabel>
                <EtaOrBlocker task={task} />
              </td>
            </tr>
          ))}
        </StackedTable>
      </PanelState>
    </Panel>
  );
}

/** Blocked, or the task's colour; "inferred" when Jira and Git gave it, not a person. */
function TaskState({ task }: { task: PodTaskDto }) {
  if (task.blocked) {
    return (
      <RagChip tone="danger" className="h-5 px-2 text-[11px]">
        {task.source === "inferred" ? "Inferred: blocked" : "Blocked"}
      </RagChip>
    );
  }
  if (task.source === "inferred") {
    return (
      <RagChip tone="warning" className="h-5 px-2 text-[11px]">
        Inferred
      </RagChip>
    );
  }
  return <RagBadge rag={task.rag} quiet />;
}

/** What Jira says of the task today: its status, since when, who moved it. Never anyone's word. */
function JiraToday({ task, today }: { task: PodTaskDto; today: string }) {
  const line = jiraTodayLine(task.observed, { today });
  const said = line ? line.replace(/^Jira today: /, "") : task.tracker_status;
  return <span className="text-grey-body">{said || "—"}</span>;
}

/** "In review · MR !3 open", and who said it, when and where; "Nothing said" when nobody has. */
function Said({ task }: { task: PodTaskDto }) {
  const words = statementWords(task.last_update);
  if (!task.last_update || !words) {
    return <span className="text-grey-secondary">Nothing said</span>;
  }
  return (
    <span className="inline-block">
      <span className="text-ink">{words.charAt(0).toUpperCase() + words.slice(1)}</span>
      <span className="block text-[12px] text-grey-secondary">
        {task.last_update_by ? `${task.last_update_by} · ` : ""}
        {formatDay(task.last_update.at)} {viaWords(task.last_update.via)}
      </span>
    </span>
  );
}

/** The open blockers on it; else the owners' own ETA (late against the due date says so); else the due date. */
function EtaOrBlocker({ task }: { task: PodTaskDto }) {
  if (task.open_blockers.length > 0) {
    return (
      <span className="inline-flex flex-wrap gap-1.5">
        {task.open_blockers.map((blocker) => (
          <BlockerChip
            key={blocker.blocker_id}
            description={blocker.description}
            ageDays={blocker.age_days}
          />
        ))}
      </span>
    );
  }
  if (task.eta) {
    const late =
      startState({
        last_update: task.last_update ?? null,
        tracker_status: task.tracker_status ?? null,
      }) !== "done" && etaAfterDue({ my_eta: task.eta, deadline: task.deadline });
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5 whitespace-nowrap">
        <span className="font-bold text-ink">ETA {formatDay(task.eta)}</span>
        {late ? (
          <RagChip tone="warning" className="h-5 px-2 text-[11px]">
            after due
          </RagChip>
        ) : null}
      </span>
    );
  }
  return task.deadline ? (
    <span className="whitespace-nowrap">
      Due <DueDate deadline={task.deadline} trackerStatus={task.tracker_status} />
    </span>
  ) : (
    <span className="text-grey-secondary">No ETA or due date</span>
  );
}
