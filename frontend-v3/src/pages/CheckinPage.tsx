import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { usePods, usePrograms, useProjects } from "../app/directory";
import { useShownDay } from "../app/viewingDate";
import { Greeting } from "../components/ui/Bits";
import { localIso } from "../lib/viewingDate";
import { todayEyebrow } from "../lib/words";
import { Conversation } from "../features/checkin/Conversation";
import { CheckinCard } from "../features/today/CheckinCard";
import { TaskList } from "../features/today/TaskList";

/**
 * Check-in (`/check-in`): where the person's own work stands. On the left
 * their own conversation with Ora for the day, read-only and styled like the
 * chat; on the right the console's way to say the same, the check-in to
 * confirm or correct and each task to update, which counts as the day's reply.
 * There is no reply box, so the console never speaks in the chat as the
 * person, and nobody reads anyone else's conversation here, admins included.
 */
export function CheckinPage() {
  const shownDay = useShownDay();
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rollup = useRollUp(focus.data?.developer_id);
  // The server's day, as the form judges "today" by it; the browser's only when it can't say.
  const today = focus.data?.as_of ?? (focus.isError ? localIso() : "");
  const day = shownDay ?? today;
  // Blockers on a listed task show on its row; the check-in card keeps the rest.
  const taskIds = new Set((focus.data?.tasks ?? []).map((task) => task.id));

  return (
    <div className="op-viz">
      <Greeting
        eyebrow={todayEyebrow(
          rollup.programs.map((program) => program.name),
          shownDay,
        )}
        title="Check-in"
        sub="Where your work stands: tell Ora in your chat, or update it here."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Conversation day={day} isToday={!shownDay} />
        <section
          aria-labelledby="console-update"
          className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4"
        >
          <div>
            <h2 id="console-update" className="text-[17px] font-extrabold">
              Or update from the console
            </h2>
            <p className="mt-0.5 text-[13px] text-grey-secondary">
              Saving here counts as today&apos;s reply.
            </p>
          </div>
          <CheckinCard
            podNames={rollup.pods.map((pod) => pod.name)}
            shownOnTasks={taskIds}
            footer={focus.data && !rollup.isLoading ? feedsLine(rollup) : null}
          />
          <TaskList />
        </section>
      </div>
    </div>
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

/** Pods the person is in, the projects those pods work on, and their programs. */
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
