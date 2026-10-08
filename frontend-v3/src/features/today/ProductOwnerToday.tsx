import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

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
import { ChipPicker, Greeting, Panel, ProgressRing, RagBadge, Row } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { greetingTitle, PERSON_KEY_WORDS, plural, sourceLine, todayEyebrow } from "../../lib/words";
import { YourAsks } from "./YourAsks";

const ATTENTION_SHOWN = 8;

/**
 * A product owner's day: how far along the project is, which workstreams are
 * worst, and the tasks that need a decision. Defaults to the projects of the
 * pods the person belongs to.
 */
export function ProductOwnerToday() {
  const shownDay = useShownDay();
  const { canReadProjectProgress, roleLabel, greetingName } = useRole();
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const ownPods = podsOfPerson(pods.data ?? [], memberId);
  const { projects: ofPods, fallback } = projectsOfPerson(
    projects.data ?? [],
    ownPods.pods,
    ownPods.own,
  );
  // Opens on the worst of them; equally bad ones, on the project of the person's first pod.
  const mine = rankProjects(ofPods, ownPods.pods);
  const [chosen, setChosen] = useState("");
  const projectId = mine.some((p) => p.id === chosen) ? chosen : (mine[0]?.id ?? "");
  const project = mine.find((p) => p.id === projectId);

  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
    enabled: Boolean(projectId) && canReadProjectProgress,
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
  const ws = [...(workstreams.data ?? [])].sort((a, b) => ragSeverity(b.rag) - ragSeverity(a.rag));
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
        isEmpty={mine.length === 0}
        emptyText="No projects are configured yet."
      >
        <ChipPicker
          label="Project"
          value={projectId}
          onChange={setChosen}
          options={mine.map((x) => ({ value: x.id, label: x.name, rag: x.rag }))}
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
              {p ? (
                <div className="flex flex-wrap items-center gap-5">
                  <ProgressRing percent={p.total_tasks > 0 ? p.percent_complete : null} />
                  <div className="grid gap-2">
                    <div className="flex flex-wrap gap-1.5">
                      <RagChip tone="success" className="h-6 px-2.5 text-[12px]">
                        {p.green_tasks} green
                      </RagChip>
                      <RagChip tone="warning" className="h-6 px-2.5 text-[12px]">
                        {p.amber_tasks} amber
                      </RagChip>
                      <RagChip tone="danger" className="h-6 px-2.5 text-[12px]">
                        {p.red_tasks} red
                      </RagChip>
                      <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                        {p.unknown_tasks} unknown
                      </RagChip>
                    </div>
                    <p className="text-[12px] text-grey-secondary">
                      {plural(p.total_tasks, "task", "tasks")}
                      {p.total_tasks > 0 && p.confidence != null
                        ? ` · ${Math.round(p.confidence * 100)}% average confidence`
                        : ""}
                    </p>
                  </div>
                </div>
              ) : null}
            </PanelState>
          </Panel>
          <Panel title="Workstreams" note="worst first">
            <PanelState
              isLoading={workstreams.isLoading}
              error={workstreams.error}
              isEmpty={ws.length === 0}
              emptyText="This project runs without workstreams."
            >
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
                      title={
                        <Link
                          to={`/delivery/workstream/${w.id}`}
                          className="text-ink no-underline hover:underline"
                        >
                          {w.name}
                        </Link>
                      }
                      meta={bits.join(" · ") || undefined}
                      right={<RagBadge rag={w.rag} />}
                    />
                  );
                })}
              </ul>
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
                    meta={`${t.id}${t.tracker_status ? ` · ${t.tracker_status}` : ""} · ${sourceLine(t.source, t.confidence)}${t.deadline ? ` · due ${formatDay(t.deadline)}` : ""}`}
                    right={<RagBadge rag={t.rag} />}
                  />
                ))}
              </ul>
            </PanelState>
          </Panel>
          <YourAsks />
        </div>
      </PanelState>
    </>
  );
}
