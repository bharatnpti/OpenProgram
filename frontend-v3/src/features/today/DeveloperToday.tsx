import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { usePods, usePrograms, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { Greeting } from "../../components/ui/Bits";
import { greetingTitle, todayEyebrow } from "../../lib/words";
import { CheckinCard } from "./CheckinCard";
import { TaskList } from "./TaskList";
import { YourAsks } from "./YourAsks";

/**
 * A developer's day: the check-in to confirm or correct, their tasks to update
 * one at a time, and what others are waiting on. Most of a developer's
 * OpenProgram happens in chat; this is the console side.
 */
export function DeveloperToday() {
  const shownDay = useShownDay();
  const { roleLabel, greetingName } = useRole();
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rollup = useRollUp(focus.data?.developer_id);
  // Blockers on a listed task show on its row; the check-in card keeps the rest.
  const taskIds = new Set((focus.data?.tasks ?? []).map((task) => task.id));

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          rollup.programs.map((program) => program.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Confirm today's check-in and say where each task stands."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard
            podNames={rollup.pods.map((pod) => pod.name)}
            shownOnTasks={taskIds}
            footer={focus.data && !rollup.isLoading ? feedsLine(rollup) : null}
          />
          <TaskList />
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <YourAsks />
        </div>
      </div>
    </>
  );
}

/** "Feeds Payments Pod › Checkout Revamp › Digital Platform Program": where the check-in counts. */
function feedsLine(rollup: ReturnType<typeof useRollUp>): string | null {
  if (rollup.error) return null;
  const chain = [...rollup.pods, ...rollup.projects, ...rollup.programs].map((item) => item.name);
  return chain.length > 0
    ? `Feeds ${chain.join(" › ")}`
    : "You are in no pod yet, so your check-in counts toward no pod, project or program.";
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
