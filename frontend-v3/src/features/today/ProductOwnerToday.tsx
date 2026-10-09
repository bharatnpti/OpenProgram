import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import {
  podsOfPerson,
  programsOfProjects,
  projectsOfPerson,
  rankProjects,
  useMemberId,
  usePods,
  usePrograms,
  useProjects,
} from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { ChipPicker, DueDate, Greeting, Panel, RagBadge, Row } from "../../components/ui/Bits";
import { pastDue } from "../../components/ui/dateStripWords";
import { Pill } from "../../components/ui/Pill";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { greetingTitle, PERSON_KEY_WORDS, plural, sourceLine, todayEyebrow } from "../../lib/words";
import { useAssistantSubject } from "../assistant/assistantContext";
import { ProjectDateStrip } from "../delivery/DeliveryStrips";
import { FactorsPanel } from "../delivery/NodeBits";
import { ProgressSummary, TaskTable } from "../delivery/ProgressBlock";
import { CheckinCard } from "./CheckinCard";
import { YourAsks } from "./YourAsks";

const ATTENTION_SHOWN = 8;

/**
 * A product owner's day, one project at a time (`?project=`): how far along it
 * is, the tasks that need a decision (and every task a click away), why it has
 * its colour, and its workstreams where it has any. Opens on the projects of
 * the pods the person belongs to, the worst first; a project named in the link
 * is offered too.
 */
export function ProductOwnerToday() {
  const shownDay = useShownDay();
  const { roleLabel, greetingName } = useRole();
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const [search, setSearch] = useSearchParams();
  const [allTasks, setAllTasks] = useState(false);
  const ownPods = podsOfPerson(pods.data ?? [], memberId);
  const { projects: ofPods, fallback } = projectsOfPerson(
    projects.data ?? [],
    ownPods.pods,
    ownPods.own,
  );
  // Opens on the worst of them; equally bad ones, on the project of the person's first pod.
  const mine = rankProjects(ofPods, ownPods.pods);
  const asked = search.get("project");
  // A project a link names (a palette jump, a Delivery link) is offered even when it is not theirs.
  const linked =
    asked && !mine.some((p) => p.id === asked)
      ? (projects.data ?? []).find((p) => p.id === asked)
      : undefined;
  const options = linked ? [linked, ...mine] : mine;
  const projectId = options.some((p) => p.id === asked) ? (asked ?? "") : (options[0]?.id ?? "");
  const project = options.find((p) => p.id === projectId);
  useAssistantSubject(project ? { kind: "project", name: project.name } : null);
  const choose = (next: string) => {
    setAllTasks(false);
    setSearch(
      (current) => {
        // Other parameters (the day being viewed) are the shell's; leave them.
        const params = new URLSearchParams(current);
        params.set("project", next);
        return params;
      },
      { replace: true },
    );
  };

  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
    enabled: Boolean(projectId),
  });
  const workstreams = useQuery({
    queryKey: ["project", projectId, "workstreams"],
    queryFn: () => apiClient.projectWorkstreams(projectId),
    enabled: Boolean(projectId),
  });

  const p = progress.data;
  const attention = [...(p?.tasks ?? [])]
    .filter((t) => t.rag === "red" || t.rag === "amber" || t.rag === "unknown")
    .sort((a, b) => ragSeverity(b.rag) - ragSeverity(a.rag));
  // Workstreams are optional: one with no work is left out, and so is the panel with none.
  const ws = [...(workstreams.data ?? [])]
    .filter((w) => w.in_use !== false)
    .sort((a, b) => ragSeverity(b.rag) - ragSeverity(a.rag));
  const meta = (value: unknown) => (typeof value === "string" && value ? value : null);

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          programsOfProjects(programs.data ?? [], project ? [project] : []).map((x) => x.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Project progress, task health and what needs a decision from you."
      />
      <PanelState
        isLoading={projects.isLoading || pods.isLoading}
        error={projects.error ?? pods.error}
        isEmpty={options.length === 0}
        emptyText="No projects are configured yet."
      >
        <ChipPicker
          label="Project"
          value={projectId}
          onChange={choose}
          options={options.map((x) => ({ value: x.id, label: x.name, rag: x.rag }))}
          note={
            fallback === "no-pod"
              ? "you are in no pod yet, so every project is shown"
              : fallback === "no-project"
                ? "your pods work on no project yet, so every project is shown"
                : mine.length > 1
                  ? "the projects of your pods · worst first"
                  : "the projects of your pods"
          }
        />
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          {projectId ? <ProjectDateStrip projectId={projectId} /> : null}
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            <Panel
              title="Progress"
              note={
                <Link
                  to={`/reports/${projectId}/overall`}
                  className="font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
                >
                  Overall report
                </Link>
              }
            >
              <PanelState isLoading={progress.isLoading} error={progress.error}>
                {p ? <ProgressSummary progress={p} /> : null}
              </PanelState>
            </Panel>
            <Panel
              title="Needs your attention"
              note={
                p
                  ? attention.length > ATTENTION_SHOWN
                    ? `worst ${ATTENTION_SHOWN} of ${attention.length} tasks`
                    : plural(attention.length, "task", "tasks")
                  : undefined
              }
            >
              <PanelState
                isLoading={progress.isLoading}
                error={progress.error}
                isEmpty={attention.length === 0}
                emptyText="Every task is green."
              >
                <ul>
                  {attention.slice(0, ATTENTION_SHOWN).map((t) => (
                    <Row
                      key={t.id}
                      rag={t.rag}
                      title={t.name}
                      accent={pastDue(t.deadline, shownDay, t.tracker_status) ? "amber" : undefined}
                      meta={`${t.id}${t.tracker_status ? ` · ${t.tracker_status}` : ""} · ${sourceLine(t.source, t.confidence)}`}
                      right={<RagBadge rag={t.rag} quiet />}
                    >
                      {t.deadline ? (
                        <p className="mt-0.5 text-[12px]">
                          <DueDate
                            prefix="Due "
                            deadline={t.deadline}
                            trackerStatus={t.tracker_status}
                          />
                        </p>
                      ) : null}
                    </Row>
                  ))}
                </ul>
              </PanelState>
              {p && p.tasks.length > 0 ? (
                <div className="mt-3">
                  <Pill
                    size="sm"
                    variant="ghost"
                    aria-expanded={allTasks}
                    aria-controls="po-all-tasks"
                    onClick={() => setAllTasks((on) => !on)}
                  >
                    {allTasks
                      ? "Hide the task list"
                      : `Show all ${plural(p.tasks.length, "task", "tasks")}`}
                  </Pill>
                </div>
              ) : null}
            </Panel>
          </div>
          {allTasks && p ? (
            <div id="po-all-tasks">
              <TaskTable tasks={p.tasks} />
            </div>
          ) : null}
          {p ? (
            <FactorsPanel
              always
              title={`Why ${project?.name ?? "this project"} is ${p.rag}`}
              factors={p.factors}
              names={p.source_names}
            />
          ) : null}
          {ws.length > 0 ? (
            <Panel title="Workstreams" note="worst first">
              <ul>
                {ws.map((w) => {
                  const bits = [
                    meta(w.metadata.type),
                    meta(w.metadata.phase),
                    meta(w.metadata.target_date)
                      ? `target ${formatDay(meta(w.metadata.target_date))}`
                      : null,
                    ...w.people
                      .filter((x) => x.name)
                      .map((x) => `${PERSON_KEY_WORDS[x.key] ?? x.key} ${x.name}`),
                  ].filter(Boolean);
                  return (
                    <Row
                      key={w.id}
                      rag={w.rag}
                      title={w.name}
                      meta={bits.join(" · ") || undefined}
                      right={<RagBadge rag={w.rag} />}
                    />
                  );
                })}
              </ul>
            </Panel>
          ) : null}
          {/* Their own check-in, asked in chat like everyone else's, and their asks. */}
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-2">
            <CheckinCard compact />
            <YourAsks />
          </div>
        </div>
      </PanelState>
    </>
  );
}
