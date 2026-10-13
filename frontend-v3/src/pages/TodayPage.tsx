import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { projectPath } from "../app/links";
import { useRole } from "../app/role";
import { useReadOnly, useShownDay } from "../app/viewingDate";
import { Greeting } from "../components/ui/Bits";
import { greetingTitle, todayEyebrow } from "../lib/words";
import { JiraChanges } from "../features/today/JiraChanges";
import {
  type TodaySubject,
  useMyJiraChanges,
  useTodayCounts,
  useTodaySubject,
} from "../features/today/queries";
import { CheckinAction, RepliesCard, WarningsCard } from "../features/today/TodayCards";
import { TodayAnswer } from "../features/today/TodayAnswer";
import { taskRows } from "../features/today/taskUpdate";
import { footerLine } from "../features/today/todayWords";
import { WhoMustAct } from "../features/today/WhoMustAct";
import { YourAsks } from "../features/today/YourAsks";

/**
 * Today (D96): the landing page for every role, with what needs them now and
 * nothing that lives in full elsewhere. It leads with one answer: the worst
 * project for an executive, a manager and an admin; the worst of their own for
 * a product owner; the worst pod they run for a scrum master; their own
 * check-in for a developer. Then the action cards: their check-in, the replies
 * and warnings of the day (not for a developer, who reads no aggregate), the
 * Jira changes waiting for their yes, who must act, and their asks.
 */
export function TodayPage() {
  const { role, roleLabel, greetingName, access } = useRole();
  const shownDay = useShownDay();
  const { readOnly } = useReadOnly();
  const subject = useTodaySubject();
  const developer = role === "dev";
  const counts = useTodayCounts(!developer);
  const changes = useMyJiraChanges(!readOnly);
  const focus = useQuery({
    queryKey: ["me", "focus"],
    queryFn: () => apiClient.focus(),
    enabled: developer,
  });
  const day = shownDay ?? focus.data?.as_of ?? counts.data?.as_of ?? null;

  return (
    <div className="op-viz">
      <Greeting
        eyebrow={todayEyebrow(eyebrowNames(subject), shownDay)}
        title={greetingTitle(greetingName, roleLabel)}
        sub="What changed, and who needs to act today."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        {developer ? (
          <CheckinAction
            tasks={
              focus.data
                ? taskRows(focus.data.tasks, focus.data.blocker_details, shownDay).filter(
                    (row) => row.group !== "done",
                  ).length
                : null
            }
          />
        ) : (
          <>
            <TodayAnswer subject={subject} />
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-3">
              <CheckinAction />
              {counts.data?.replies === null ? null : (
                // No replies over fewer than five people for an executive (D104).
                <RepliesCard
                  counts={counts.data}
                  isLoading={counts.isLoading}
                  error={counts.error}
                  tracker={changes.data?.tracker}
                />
              )}
              <WarningsCard
                counts={counts.data}
                isLoading={counts.isLoading}
                error={counts.error}
                link={warningsLink(access.pages.warnings, subject)}
              />
            </div>
          </>
        )}
        {readOnly ? null : <JiraChanges />}
        {developer ? null : <WhoMustActFor subject={subject} />}
        <YourAsks />
        <p className="text-[12px] text-grey-secondary">{footerLine(day)}</p>
      </div>
    </div>
  );
}

function WhoMustActFor({ subject }: { subject: TodaySubject }) {
  if (subject.kind === "project") return <WhoMustAct projectIds={[subject.projectId]} />;
  if (subject.kind === "pod") {
    return (
      <WhoMustAct
        projectIds={subject.pod.project_ids}
        teams={new Set([subject.pod.name])}
        empty={`Nobody needs to act on anything in ${subject.pod.name} today.`}
      />
    );
  }
  return null;
}

/** Where "Review warnings" goes: Early warnings for its readers, else the project's own section. */
function warningsLink(
  warningsPage: boolean,
  subject: TodaySubject,
): { to: string; text: string } | null {
  if (warningsPage) return { to: "/warnings", text: "Review warnings" };
  const projectId =
    subject.kind === "project"
      ? subject.projectId
      : subject.kind === "pod"
        ? subject.pod.project_ids[0]
        : undefined;
  return projectId ? { to: projectPath(projectId, "warnings"), text: "Review warnings" } : null;
}

function eyebrowNames(subject: TodaySubject): string[] {
  if (subject.kind === "project") return [subject.name];
  if (subject.kind === "pod") return [subject.pod.name];
  return [];
}
