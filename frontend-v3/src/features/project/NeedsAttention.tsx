import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "../../api/client";
import { useProjects } from "../../app/directory";
import { useShownDay } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { DueDate, Panel, RagBadge, Row } from "../../components/ui/Bits";
import { pastDue } from "../../components/ui/dateStripWords";
import { Pill } from "../../components/ui/Pill";
import { plural, sourceLine } from "../../lib/words";
import { ProgressSummary, TaskTable } from "../delivery/ProgressBlock";
import { metadataFacts } from "../delivery/factors";
import { ATTENTION_SHOWN, attentionNote, attentionTasks } from "./attentionTasks";

/**
 * Progress and tasks, on a project's Will we make the date?: how far along it
 * is and where its code lives, the tasks that need a decision (the worst eight
 * that are not green, a past due date marked), and every task a click away.
 * The reasons behind today's health are on the project's Early warnings, said
 * once there; the Jira project and the owners are in the header.
 */
export function NeedsAttention({ projectId }: { projectId: string }) {
  const shownDay = useShownDay();
  const [allTasks, setAllTasks] = useState(false);
  const projects = useProjects();
  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
  });
  const p = progress.data;
  const attention = attentionTasks(p?.tasks ?? []);
  const tableId = `all-tasks-${projectId}`;
  const repos = metadataFacts(
    projects.data?.find((item) => item.id === projectId)?.metadata ?? {},
  ).find(([label]) => label === "Repositories")?.[1];

  return (
    <section aria-labelledby="progress-h">
      <SectionHeader
        id="progress-h"
        title="Progress and tasks"
        meta={`How far along it is, and the tasks that need a decision${repos ? ` · code in ${repos}` : ""}`}
      />
      <PanelState
        isLoading={progress.isLoading}
        error={progress.error}
        onRetry={() => void progress.refetch()}
      >
        {p ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
              <Panel title="Progress">
                <ProgressSummary progress={p} />
              </Panel>
              <Panel title="Needs your attention" note={attentionNote(attention.length)}>
                {attention.length === 0 ? (
                  <p className="text-[14px] text-grey-secondary">Every task is green.</p>
                ) : (
                  <ul>
                    {attention.slice(0, ATTENTION_SHOWN).map((task) => (
                      <Row
                        key={task.id}
                        rag={task.rag}
                        title={task.name}
                        accent={
                          pastDue(task.deadline, shownDay, task.tracker_status)
                            ? "amber"
                            : undefined
                        }
                        meta={[
                          task.id,
                          task.tracker_status,
                          sourceLine(task.source, task.confidence),
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                        right={<RagBadge rag={task.rag} quiet />}
                      >
                        {task.deadline ? (
                          <p className="mt-0.5 text-[12px]">
                            <DueDate
                              prefix="Due "
                              deadline={task.deadline}
                              trackerStatus={task.tracker_status}
                            />
                          </p>
                        ) : null}
                      </Row>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
            {p.tasks.length > 0 ? (
              <div>
                <Pill
                  size="sm"
                  variant="ghost"
                  aria-expanded={allTasks}
                  aria-controls={tableId}
                  onClick={() => setAllTasks((on) => !on)}
                >
                  {allTasks
                    ? "Hide the task list"
                    : `Show all ${plural(p.tasks.length, "task", "tasks")}`}
                </Pill>
              </div>
            ) : null}
            {allTasks ? (
              <div id={tableId}>
                <TaskTable tasks={p.tasks} />
              </div>
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </section>
  );
}
