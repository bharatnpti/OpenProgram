import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, GitBranch } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { DirectoryItemResponse } from "../api/schema";
import { DataTable } from "../components/ops/DataTable";
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

export function WorkstreamDetailPage() {
  const { workstreamId = "" } = useParams();
  const [asOf, setAsOf] = useState(todayIso);
  const workstream = useQuery({
    queryKey: ["directory", "workstream", workstreamId, asOf],
    queryFn: () => apiClient.workstream(workstreamId, asOf),
    enabled: Boolean(workstreamId),
  });
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const progress = useQuery({
    queryKey: ["persona", "workstream-progress", workstreamId, asOf],
    queryFn: () => apiClient.workstreamProgress(workstreamId, asOf),
    enabled: Boolean(workstreamId),
  });
  const flow = useQuery({
    queryKey: ["persona", "workstream-flow", workstreamId, asOf],
    queryFn: () => apiClient.workstreamFlow(workstreamId, asOf),
    enabled: Boolean(workstreamId),
  });

  const item = workstream.data;
  const relatedProjects = useMemo(
    () => projects.data?.filter((project) => item?.project_ids.includes(project.id)) ?? [],
    [item?.project_ids, projects.data],
  );
  const relatedPods = useMemo(
    () => pods.data?.filter((pod) => item?.pod_ids.includes(pod.id)) ?? [],
    [item?.pod_ids, pods.data],
  );
  const refreshing = [workstream, projects, pods, progress, flow].some((query) => query.isFetching);

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow={
            <Link
              to="/workstreams"
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to workstreams
            </Link>
          }
          title={item?.name ?? workstreamId}
          description={
            metadataText(item, "summary") ||
            item?.description ||
            "Workstream progress, linked pods, and task health."
          }
          actions={progress.data && <StatusBadge rag={progress.data.rag} />}
        />

        <Toolbar>
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={refreshing}
            onClick={() => {
              void workstream.refetch();
              void projects.refetch();
              void pods.refetch();
              void progress.refetch();
              void flow.refetch();
            }}
          />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-4">
          <KpiCard
            icon={<GitBranch className="h-4 w-4" />}
            label="Progress"
            value={progress.data ? `${Math.round(progress.data.percent_complete)}%` : "-"}
            detail={progress.data?.source ?? "loading"}
            tone="info"
          />
          <KpiCard label="Done" value={progress.data?.green_tasks ?? "-"} tone="success" />
          <KpiCard label="At risk" value={progress.data?.amber_tasks ?? "-"} tone="warning" />
          <KpiCard label="Blocked" value={progress.data?.red_tasks ?? "-"} tone="danger" />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <DataPanel
            title="Graph Links"
            description="Projects and pods connected to this workstream."
          >
            <div className="grid gap-4 lg:grid-cols-2">
              <LinkedItems title="Projects" items={relatedProjects} basePath="/projects" />
              <LinkedItems title="Pods" items={relatedPods} basePath="/pods" />
            </div>
          </DataPanel>

          <DataPanel
            title="Metadata"
            description="Static planning fields for ownership and target date."
          >
            <div className="space-y-2 text-sm">
              <MetadataRow label="Type" value={metadataText(item, "type")} />
              <MetadataRow label="Phase" value={metadataText(item, "phase")} />
              <MetadataRow label="Owner" value={metadataText(item, "owner_id")} />
              <MetadataRow label="TPM" value={metadataText(item, "tpm_id")} />
              <MetadataRow label="SM" value={metadataText(item, "sm_id")} />
              <MetadataRow label="Target" value={metadataText(item, "target_date")} />
            </div>
          </DataPanel>
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <DataPanel title="Flow" description="Work-item throughput and aging for this workstream.">
            <QueryState query={flow} loadingRows={5}>
              {(data) => (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-4">
                    <KpiCard label="Active" value={data.active_count} tone="info" />
                    <KpiCard label="In flight" value={data.features_in_flight} tone="success" />
                    <KpiCard label="Stale" value={data.stale_count} tone="warning" />
                    <KpiCard label="Abandoned" value={data.abandoned_count} tone="danger" />
                  </div>
                  <DataTable
                    data={data.work_items}
                    columns={[
                      { accessorKey: "name", header: "Work item" },
                      { accessorKey: "state", header: "State" },
                      { accessorKey: "item_type", header: "Type" },
                      { accessorKey: "age_days", header: "Age" },
                      { accessorKey: "cycle_time_days", header: "Cycle time" },
                      { accessorKey: "repo", header: "Repo" },
                      { accessorKey: "branch", header: "Branch" },
                      { accessorKey: "pr_id", header: "PR" },
                    ]}
                    emptyTitle="No work items"
                    emptyDescription="Link or create work items to see workstream flow."
                  />
                </div>
              )}
            </QueryState>
          </DataPanel>

          <DataPanel title="Portfolio context" description="How this stream contributes to the broader flow picture.">
            <QueryState query={flow}>
              {(data) => (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">Avg cycle time</span>
                    <span className="font-medium tabular-nums">{formatDays(data.avg_cycle_time_days)}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">Avg PR age</span>
                    <span className="font-medium tabular-nums">{formatDays(data.avg_pr_age_days)}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                    <span className="text-muted-foreground">Work items tracked</span>
                    <span className="font-medium tabular-nums">{data.work_items.length}</span>
                  </div>
                </div>
              )}
            </QueryState>
          </DataPanel>
        </section>

        <div className="grid gap-4 xl:grid-cols-2">
          <DataPanel title="Rollup" description="Computed workstream status and source.">
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

          <DataPanel title="Factors" description="Most relevant signals driving current status.">
            <QueryState query={progress}>
              {(data) =>
                data.factors.length === 0 ? (
                  <EmptyState title="No rollup factors" />
                ) : (
                  <div className="divide-y divide-border rounded-md border border-border">
                    {data.factors.map((factor, index) => (
                      <div key={`${factor.description}-${index}`} className="px-3 py-2 text-sm">
                        <div className="font-medium">{factor.description}</div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {factor.source_ref.kind}:{factor.source_ref.id}
                        </div>
                      </div>
                    ))}
                  </div>
                )
              }
            </QueryState>
          </DataPanel>
        </div>

        <DataPanel title="Task Breakdown" description="Child task RAG, source, and confidence.">
          <QueryState query={progress}>
            {(data) =>
              data.tasks.length === 0 ? (
                <EmptyState title="No tasks linked" />
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

function LinkedItems({
  title,
  items,
  basePath,
}: {
  title: string;
  items: DirectoryItemResponse[];
  basePath: string;
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{title}</div>
      {items.length === 0 ? (
        <EmptyState title={`No linked ${title.toLowerCase()}`} />
      ) : (
        <div className="divide-y divide-border rounded-md border border-border">
          {items.map((item) => (
            <Link
              key={item.id}
              to={`${basePath}/${item.id}`}
              className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm hover:bg-surface-muted/50"
            >
              <div className="min-w-0">
                <div className="truncate font-medium">{item.name}</div>
                <div className="truncate font-mono text-xs text-muted-foreground">{item.id}</div>
              </div>
              <StatusBadge rag={item.rag} />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value || "-"}</span>
    </div>
  );
}

function metadataText(item: DirectoryItemResponse | undefined, key: string) {
  const value = item?.metadata[key];
  return typeof value === "string" ? value : "";
}

function formatDays(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value.toFixed(1)}d`;
}
