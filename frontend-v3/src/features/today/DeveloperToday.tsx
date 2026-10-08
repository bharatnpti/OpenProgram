import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { FocusResponse } from "../../api/schema";
import { usePods, usePrograms, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useReadOnly, useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import {
  BlockerChip,
  DueDate,
  Greeting,
  Panel,
  RagBadge,
  RagDot,
  Row,
} from "../../components/ui/Bits";
import { pastDue } from "../../components/ui/dateStripWords";
import { formatDay } from "../../lib/format";
import { readState, type ReadState } from "../../lib/readState";
import { daysLabel, greetingTitle, sourceLine, todayEyebrow } from "../../lib/words";
import { isNoReplyPlaceholder, whyCheckinMatters } from "./checkin";
import { CheckinCard } from "./CheckinCard";
import { YourAsks } from "./YourAsks";

/**
 * A developer's day: the check-in to confirm or correct, what to work on next,
 * what others are waiting on, their tasks, and where their status rolls up.
 * Most of a developer's OpenProgram happens in chat; this is the console side.
 */
export function DeveloperToday() {
  const shownDay = useShownDay();
  const { readOnly } = useReadOnly();
  const { roleLabel, greetingName } = useRole();
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rollup = useRollUp(focus.data?.developer_id);
  // Each task with its own open blockers: blocked work first and red-edged, then work
  // past its due date; the check-in card keeps only the blockers no task here holds.
  const details = focus.data?.blocker_details ?? [];
  const tasks = (focus.data?.tasks ?? [])
    .map((task) => ({
      task,
      blockers: details.filter((blocker) => blocker.work_item_id === task.id),
      late: pastDue(task.deadline, shownDay),
    }))
    .sort(
      (a, b) =>
        Number(b.blockers.length > 0) - Number(a.blockers.length > 0) ||
        Number(b.late) - Number(a.late),
    );
  const taskIds = new Set(tasks.map(({ task }) => task.id));

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          rollup.programs.map((program) => program.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Confirm today's check-in and clear what's blocking you."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard podNames={rollup.pods.map((pod) => pod.name)} shownOnTasks={taskIds} />
          <Panel title="Focus today" note="ranked by urgency">
            <PanelState
              isLoading={focus.isLoading}
              error={focus.error}
              isEmpty={(focus.data?.focus ?? []).length === 0}
              emptyText="Nothing ranked for today."
            >
              <ul>
                {(focus.data?.focus ?? []).map((item, i) => {
                  const line = focusLine(item, focus.data);
                  return (
                    <Row
                      key={`${item.kind}-${item.source_ref.id}-${i}`}
                      rag={line.urgent ? "red" : null}
                      title={line.title}
                      meta={line.meta}
                      right={line.tag}
                    />
                  );
                })}
              </ul>
            </PanelState>
          </Panel>
          <Panel title="Your tasks" note="source-linked · silence is never green">
            <PanelState
              isLoading={focus.isLoading}
              error={focus.error}
              isEmpty={(focus.data?.tasks ?? []).length === 0}
              emptyText="No tasks are assigned to you."
            >
              <ul>
                {tasks.map(({ task, blockers, late }) => (
                  <Row
                    key={task.id}
                    title={task.name}
                    accent={blockers.length > 0 ? "red" : late ? "amber" : undefined}
                    meta={`${task.id} · ${sourceLine(task.source, task.confidence)}`}
                    right={<RagBadge rag={task.rag} quiet />}
                  >
                    {task.deadline ? (
                      <p className="mt-0.5 text-[12px]">
                        <DueDate prefix="Due " deadline={task.deadline} />
                      </p>
                    ) : null}
                    {blockers.length > 0 ? (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {blockers.map((blocker) => (
                          <BlockerChip
                            key={blocker.blocker_id}
                            description={blocker.description}
                            ageDays={blocker.age_days}
                          />
                        ))}
                      </div>
                    ) : null}
                  </Row>
                ))}
              </ul>
            </PanelState>
          </Panel>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <YourAsks />
          <RollsUpInto rollup={rollup} read={readState(focus)} />
          <Panel title="Why this matters">
            <p className="text-[14px] text-grey-body">{whyCheckinMatters(readOnly)}</p>
          </Panel>
        </div>
      </div>
    </>
  );
}

/**
 * One Focus row. A blocker names its work item and how long it has stood (the
 * status source it carries is the owner's, not worth saying); a task its
 * deadline, else where its status came from. The "blocker" the backend puts
 * there when nobody answered a check-in is no blocker: it says the check-in
 * wants confirming, and is not red.
 */
function focusLine(item: FocusResponse["focus"][number], focus: FocusResponse | undefined) {
  if (item.kind === "blocker" && isNoReplyPlaceholder(item.label)) {
    return {
      title: "Confirm your check-in",
      meta: "no reply yet",
      tag: "CHECK-IN",
      urgent: false,
    };
  }
  return {
    title: item.label,
    meta: focusMeta(item, focus),
    tag: item.kind.toUpperCase(),
    urgent: item.kind === "blocker",
  };
}

function focusMeta(item: FocusResponse["focus"][number], focus: FocusResponse | undefined) {
  if (item.deadline) return `due ${formatDay(item.deadline)}`;
  if (item.kind === "blocker") {
    const detail = focus?.blocker_details.find((blocker) => blocker.description === item.label);
    if (detail) {
      const age = detail.age_days > 0 ? `open ${daysLabel(detail.age_days)}` : "raised today";
      return `${detail.work_item_id ?? "no work item"} · ${age}`;
    }
  }
  return sourceLine(item.source, item.confidence);
}

/** Pods the developer is in, the projects those pods work on, and their programs. */
function useRollUp(developerId: string | undefined) {
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const mine = developerId
    ? (pods.data ?? []).filter((pod) => pod.member_ids.includes(developerId))
    : [];
  const projectIds = new Set(mine.flatMap((pod) => pod.project_ids));
  const myProjects = (projects.data ?? []).filter((p) => projectIds.has(p.id));
  const programIds = new Set(myProjects.flatMap((p) => p.program_ids));
  const myPrograms = (programs.data ?? []).filter((p) => programIds.has(p.id));
  return {
    pods: mine,
    projects: myProjects,
    programs: myPrograms,
    isLoading: pods.isLoading || projects.isLoading || programs.isLoading,
    error: pods.error ?? projects.error ?? programs.error,
  };
}

/**
 * The chain is the pods, projects and programs of the person `focus` names, so
 * "you are in no pod" is only said once that read has answered.
 */
function RollsUpInto({ rollup, read }: { rollup: ReturnType<typeof useRollUp>; read: ReadState }) {
  const chain = [...rollup.pods, ...rollup.projects, ...rollup.programs];
  return (
    <Panel title="Where you roll up" note="your check-in feeds each of these">
      <PanelState
        isLoading={read.isLoading || rollup.isLoading}
        error={read.error ?? rollup.error}
        isEmpty={chain.length === 0}
        emptyText="You are in no pod yet, so your check-in counts toward no pod, project or program."
      >
        <ul className="mt-2 flex flex-wrap items-center gap-2">
          {chain.map((item) => (
            <li
              key={`${item.kind}-${item.id}`}
              className="inline-flex items-center gap-2 rounded-full border border-grey-border px-3 py-1.5 text-[13px] font-bold"
            >
              <RagDot rag={item.rag} />
              {item.name}
              <span className="text-[11px] font-medium text-grey-secondary">{item.kind}</span>
            </li>
          ))}
        </ul>
      </PanelState>
    </Panel>
  );
}
