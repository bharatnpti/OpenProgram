import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowLeft, GitPullRequest } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import {
  AsOfControl,
  DataPanel,
  EmptyState,
  KpiCard,
  PageHeader,
  QueryState,
  RefreshButton,
  Toolbar,
} from "../components/ops/primitives";
import { SourceConfidence, StatusBadge } from "../components/ops/status";
import { Badge } from "../components/ui/badge";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [asOf, setAsOf] = useState(todayIso);
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const progress = useQuery({
    queryKey: ["persona", "progress", projectId, asOf],
    queryFn: () => apiClient.projectProgress(projectId, asOf),
    enabled: Boolean(projectId),
  });
  const workstreams = useQuery({
    queryKey: ["directory", "project-workstreams", projectId, asOf],
    queryFn: () => apiClient.projectWorkstreams(projectId, asOf),
    enabled: Boolean(projectId),
  });
  const workstreamProgresses = useQueries({
    queries: (workstreams.data ?? []).map((workstream) => ({
      queryKey: ["persona", "workstream-progress", workstream.id, asOf],
      queryFn: () => apiClient.workstreamProgress(workstream.id, asOf),
      enabled: Boolean(projectId),
    })),
  });

  const project = projects.data?.find((item) => item.id === projectId);
  const relatedPods = useMemo(
    () => pods.data?.filter((pod) => project?.pod_ids.includes(pod.id)) ?? [],
    [pods.data, project?.pod_ids],
  );
  const refreshing =
    [projects, pods, progress, workstreams].some((query) => query.isFetching) ||
    workstreamProgresses.some((query) => query.isFetching);

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow={
            <Link
              to="/projects"
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to projects
            </Link>
          }
          title={project?.name ?? projectId}
          description={
            project?.description ?? "Project delivery progress, related pods, and task health."
          }
          actions={progress.data && <StatusBadge rag={progress.data.rag} />}
        />

        <Toolbar>
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={refreshing}
            onClick={() => {
              void projects.refetch();
              void pods.refetch();
              void progress.refetch();
              void workstreams.refetch();
              workstreamProgresses.forEach((query) => void query.refetch());
            }}
          />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-4">
          <KpiCard
            icon={<GitPullRequest className="h-4 w-4" />}
            label="Progress"
            value={progress.data ? `${Math.round(progress.data.percent_complete)}%` : "-"}
            detail={progress.data?.source ?? "loading"}
            tone="info"
          />
          <KpiCard label="Done" value={progress.data?.green_tasks ?? "-"} tone="success" />
          <KpiCard label="At risk" value={progress.data?.amber_tasks ?? "-"} tone="warning" />
          <KpiCard label="Blocked" value={progress.data?.red_tasks ?? "-"} tone="danger" />
        </section>

        <div className="grid gap-4 xl:grid-cols-2">
          <DataPanel title="Associated Pods" description="Pods linked to this project.">
            <QueryState query={pods}>
              {() =>
                relatedPods.length === 0 ? (
                  <EmptyState title="No linked pods" />
                ) : (
                  <div className="divide-y divide-border rounded-md border border-border">
                    {relatedPods.map((pod) => (
                      <Link
                        key={pod.id}
                        to={`/pods/${pod.id}`}
                        className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm hover:bg-surface-muted/50"
                      >
                        <div className="truncate font-medium">{pod.name}</div>
                        <StatusBadge rag={pod.rag} />
                      </Link>
                    ))}
                  </div>
                )
              }
            </QueryState>
          </DataPanel>

          <DataPanel title="Rollup" description="Computed project status and source.">
            <QueryState query={progress}>
              {(data) => (
                <div className="space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">RAG</span>
                    <StatusBadge rag={data.rag} />
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">Source</span>
                    <Badge tone="info">{data.source}</Badge>
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">Total tasks</span>
                    <span className="font-semibold tabular-nums">{data.total_tasks}</span>
                  </div>
                </div>
              )}
            </QueryState>
          </DataPanel>
        </div>

        <DataPanel title="Workstreams" description="Delivery streams linked to this project.">
          <QueryState query={workstreams}>
            {(items) =>
              items.length === 0 ? (
                <EmptyState title="No workstreams linked" />
              ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                  {items.map((workstream, index) => {
                    const workstreamProgress = workstreamProgresses[index]?.data;
                    return (
                      <Link
                        key={workstream.id}
                        to={`/workstreams/${workstream.id}`}
                        className="block rounded-md border border-border bg-surface px-3 py-3 text-sm hover:bg-surface-muted/40"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate font-medium">{workstream.name}</div>
                            <div className="mt-0.5 truncate text-xs text-muted-foreground">
                              {metadataText(workstream, "phase") || "phase unknown"} /{" "}
                              {metadataText(workstream, "type") || "type unknown"}
                            </div>
                          </div>
                          <StatusBadge rag={workstreamProgress?.rag ?? workstream.rag} />
                        </div>
                        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                          <MiniCount label="Tasks" value={workstreamProgress?.total_tasks ?? 0} />
                          <MiniCount label="Risk" value={workstreamProgress?.amber_tasks ?? 0} />
                          <MiniCount label="Blocked" value={workstreamProgress?.red_tasks ?? 0} />
                        </div>
                        {workstreamProgress?.tasks.length ? (
                          <div className="mt-3 space-y-1">
                            {workstreamProgress.tasks.slice(0, 3).map((task) => (
                              <div
                                key={task.id}
                                className="flex items-center justify-between gap-2 rounded-md bg-surface-muted/50 px-2 py-1"
                              >
                                <span className="truncate">{task.name}</span>
                                <StatusBadge rag={task.rag} />
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </Link>
                    );
                  })}
                </div>
              )
            }
          </QueryState>
        </DataPanel>

        <DataPanel title="Task Breakdown" description="Task RAG, source, and confidence.">
          <QueryState query={progress}>
            {(data) =>
              data.tasks.length === 0 ? (
                <EmptyState title="No tasks assigned" />
              ) : (
                <div className="divide-y divide-border rounded-md border border-border">
                  {data.tasks.map((task) => (
                    <div
                      key={task.id}
                      className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium">{task.name}</div>
                        <SourceConfidence source={task.source} confidence={task.confidence} />
                      </div>
                      <StatusBadge rag={task.rag} />
                    </div>
                  ))}
                </div>
              )
            }
          </QueryState>
        </DataPanel>
      </div>
    </main>
  );
}

function MiniCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface-muted/40 px-2 py-1">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function metadataText(
  item: { metadata: Record<string, string | number | boolean | null> },
  key: string,
) {
  const value = item.metadata[key];
  return typeof value === "string" ? value : "";
}
