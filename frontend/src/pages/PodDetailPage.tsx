import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Boxes } from "lucide-react";
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
import { StateBadge, StatusBadge } from "../components/ops/status";
import { Badge } from "../components/ui/badge";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function PodDetailPage() {
  const { podId = "" } = useParams();
  const [asOf, setAsOf] = useState(todayIso);
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
  });
  const blockers = useQuery({
    queryKey: ["persona", "blockers", podId, asOf],
    queryFn: () => apiClient.podBlockers(podId, asOf),
    enabled: Boolean(podId),
  });
  const checkins = useQuery({
    queryKey: ["persona", "checkins", podId, asOf],
    queryFn: () => apiClient.podCheckins(podId, asOf),
    enabled: Boolean(podId),
  });

  const pod = pods.data?.find((item) => item.id === podId);
  const relatedProjects = useMemo(
    () => projects.data?.filter((project) => pod?.project_ids.includes(project.id)) ?? [],
    [projects.data, pod?.project_ids],
  );
  const relatedWorkstreams = useMemo(
    () =>
      workstreams.data?.filter((workstream) => pod?.workstream_ids.includes(workstream.id)) ?? [],
    [pod?.workstream_ids, workstreams.data],
  );
  const refreshing = [pods, projects, workstreams, blockers, checkins].some(
    (query) => query.isFetching,
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow={
            <Link
              to="/pods"
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to pods
            </Link>
          }
          title={pod?.name ?? podId}
          description={
            pod?.description ?? "Pod delivery health, related projects, check-ins, and blockers."
          }
          actions={pod?.rag && <StatusBadge rag={pod.rag} />}
        />

        <Toolbar>
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={refreshing}
            onClick={() => {
              void pods.refetch();
              void projects.refetch();
              void workstreams.refetch();
              void blockers.refetch();
              void checkins.refetch();
            }}
          />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            icon={<Boxes className="h-4 w-4" />}
            label="Members"
            value={pod?.member_ids.length ?? "-"}
            detail={pod?.id ?? "loading"}
            tone="info"
          />
          <KpiCard
            label="Projects"
            value={pod?.project_ids.length ?? "-"}
            detail={`${relatedProjects.length} loaded`}
          />
          <KpiCard
            label="Blockers"
            value={blockers.data?.blockers.length ?? "-"}
            detail={blockers.isFetching ? "refreshing" : "open"}
            tone={blockers.data?.blockers.length ? "warning" : "success"}
          />
        </section>

        <div className="grid gap-4 xl:grid-cols-3">
          <DataPanel
            title="Related Projects"
            description="Projects linked to this pod at the selected date."
          >
            <QueryState query={projects}>
              {() =>
                relatedProjects.length === 0 ? (
                  <EmptyState title="No linked projects" />
                ) : (
                  <div className="divide-y divide-border rounded-md border border-border">
                    {relatedProjects.map((project) => (
                      <Link
                        key={project.id}
                        to={`/projects/${project.id}`}
                        className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm hover:bg-surface-muted/50"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium">{project.name}</div>
                          <div className="truncate font-mono text-xs text-muted-foreground">
                            {project.code ?? project.id}
                          </div>
                        </div>
                        <StatusBadge rag={project.rag} />
                      </Link>
                    ))}
                  </div>
                )
              }
            </QueryState>
          </DataPanel>

          <DataPanel
            title="Assigned Workstreams"
            description="Workstreams this pod contributes to."
          >
            <QueryState query={workstreams}>
              {() =>
                relatedWorkstreams.length === 0 ? (
                  <EmptyState title="No assigned workstreams" />
                ) : (
                  <div className="divide-y divide-border rounded-md border border-border">
                    {relatedWorkstreams.map((workstream) => (
                      <Link
                        key={workstream.id}
                        to={`/workstreams/${workstream.id}`}
                        className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm hover:bg-surface-muted/50"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium">{workstream.name}</div>
                          <div className="truncate text-xs text-muted-foreground">
                            {metadataText(workstream, "phase") || workstream.id}
                          </div>
                        </div>
                        <StatusBadge rag={workstream.rag} />
                      </Link>
                    ))}
                  </div>
                )
              }
            </QueryState>
          </DataPanel>

          <DataPanel title="Check-ins" description="Completeness by developer for this pod.">
            <QueryState query={checkins}>
              {(data) => (
                <div className="space-y-3">
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <MiniStat label="Confirmed" value={data.confirmed} />
                    <MiniStat label="Stale" value={data.stale} />
                    <MiniStat label="Missing" value={data.missing} />
                  </div>
                  <div className="divide-y divide-border rounded-md border border-border">
                    {data.developers.map((developer) => (
                      <div
                        key={developer.developer_id}
                        className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium">{developer.developer_name}</div>
                          <div className="truncate text-xs text-muted-foreground">
                            {developer.summary}
                          </div>
                        </div>
                        <StateBadge state={developer.state} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </QueryState>
          </DataPanel>
        </div>

        <DataPanel
          title="Open Blockers"
          description="Owner, source, and blocker age for escalation."
        >
          <QueryState query={blockers}>
            {(data) =>
              data.blockers.length === 0 ? (
                <EmptyState title="No blockers reported" />
              ) : (
                <div className="divide-y divide-border rounded-md border border-border">
                  {data.blockers.map((blocker) => (
                    <div
                      key={blocker.id}
                      className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium">{blocker.description}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {blocker.owner_name} / {blocker.source}
                        </div>
                      </div>
                      <Badge tone="warning">{blocker.age_days}d</Badge>
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

function metadataText(
  item: { metadata: Record<string, string | number | boolean | null> },
  key: string,
) {
  const value = item.metadata[key];
  return typeof value === "string" ? value : "";
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
