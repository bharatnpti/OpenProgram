import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarCheck,
  GitPullRequest,
  Network,
  RefreshCw,
  UserRound,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { apiClient } from "../../api/client";
import type { Rag, StatusSource } from "../../api/schema";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { HeatmapChart } from "./HeatmapChart";
import { HierarchyFlow } from "./HierarchyFlow";

const demoAsOf = "2026-06-15";

export function PersonaDashboard() {
  const [asOf, setAsOf] = useState(demoAsOf);
  const health = useQuery({ queryKey: ["health"], queryFn: apiClient.health });
  const focus = useQuery({
    queryKey: ["persona", "focus", asOf],
    queryFn: () => apiClient.focus(asOf),
  });
  const blockers = useQuery({
    queryKey: ["persona", "blockers", "pod-runtime", asOf],
    queryFn: () => apiClient.podBlockers("pod-runtime", asOf),
  });
  const checkins = useQuery({
    queryKey: ["persona", "checkins", "pod-runtime", asOf],
    queryFn: () => apiClient.podCheckins("pod-runtime", asOf),
  });
  const progress = useQuery({
    queryKey: ["persona", "progress", "project-foundations", asOf],
    queryFn: () => apiClient.projectProgress("project-foundations", asOf),
  });
  const tree = useQuery({
    queryKey: ["persona", "tree", "program-platform", asOf],
    queryFn: () => apiClient.personaProgramTree("program-platform", asOf),
  });
  const heatmap = useQuery({
    queryKey: ["persona", "heatmap", asOf],
    queryFn: () => apiClient.portfolioHeatmap(asOf),
  });

  const queries = [health, focus, blockers, checkins, progress, tree, heatmap];
  const isRefreshing = queries.some((query) => query.isFetching);

  return (
    <main className="min-h-screen">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-5 py-5">
        <header className="flex flex-col gap-3 border-b border-border pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-normal">PulseOps Persona Console</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>{health.data?.environment ?? "local"}</span>
              <span>/</span>
              <span>{health.data?.tenant_id ?? "demo"}</span>
              <Badge tone={health.data?.status === "ok" ? "success" : "warning"}>
                {health.data?.status ?? "loading"}
              </Badge>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="date"
              className="h-9 rounded border border-border bg-white px-3 text-sm"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
            />
            <Button
              onClick={() => {
                queries.forEach((query) => {
                  void query.refetch();
                });
              }}
              disabled={isRefreshing}
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        </header>

        <section className="grid gap-3 md:grid-cols-4">
          <Metric
            icon={<UserRound className="h-4 w-4" />}
            label="Dev Focus"
            value={focus.data?.focus.length.toString() ?? "-"}
            detail={focus.data?.status_source ?? statusForQuery(focus)}
          />
          <Metric
            icon={<AlertTriangle className="h-4 w-4" />}
            label="Pod Blockers"
            value={blockers.data?.blockers.length.toString() ?? "-"}
            detail={statusForQuery(blockers)}
          />
          <Metric
            icon={<CalendarCheck className="h-4 w-4" />}
            label="Check-ins"
            value={
              checkins.data ? `${checkins.data.confirmed}/${checkins.data.developers.length}` : "-"
            }
            detail={statusForQuery(checkins)}
          />
          <Metric
            icon={<GitPullRequest className="h-4 w-4" />}
            label="Project Progress"
            value={progress.data ? `${Math.round(progress.data.percent_complete)}%` : "-"}
            detail={progress.data?.source ?? statusForQuery(progress)}
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
          <Panel title="Dev Focus" action={<SourceBadge source={focus.data?.status_source} />}>
            <LoadState query={focus}>
              {focus.data && (
                <div className="space-y-4">
                  <div>
                    <div className="text-sm font-semibold">{focus.data.developer_name}</div>
                    <p className="mt-1 text-sm text-muted-foreground">{focus.data.summary}</p>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <ListBlock
                      title="Focus"
                      empty="No active focus items"
                      items={focus.data.focus.map((item) => ({
                        id: `${item.kind}-${item.label}`,
                        primary: item.label,
                        secondary: `${item.kind} / ${item.source}`,
                        badge: item.deadline ?? item.source_ref.kind,
                        tone: item.kind === "blocker" ? "danger" : "warning",
                      }))}
                    />
                    <ListBlock
                      title="Tasks"
                      empty="No assigned tasks"
                      items={focus.data.tasks.map((task) => ({
                        id: task.id,
                        primary: task.name,
                        secondary: sourceLine(task.source, task.confidence),
                        badge: task.rag,
                        tone: toneForRag(task.rag),
                      }))}
                    />
                  </div>
                </div>
              )}
            </LoadState>
          </Panel>

          <Panel title="SM Check-ins" action={<Badge tone="info">pod-runtime</Badge>}>
            <LoadState query={checkins}>
              {checkins.data && (
                <div className="space-y-3">
                  <div className="grid grid-cols-3 gap-2 text-center text-sm">
                    <Count label="Confirmed" value={checkins.data.confirmed} tone="success" />
                    <Count label="Stale" value={checkins.data.stale} tone="warning" />
                    <Count label="Missing" value={checkins.data.missing} tone="danger" />
                  </div>
                  <div className="divide-y divide-border">
                    {checkins.data.developers.map((developer) => (
                      <Row
                        key={developer.developer_id}
                        primary={developer.developer_name}
                        secondary={developer.summary}
                        badge={developer.state}
                        tone={toneForState(developer.state)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </LoadState>
          </Panel>
        </section>

        <section className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
          <Panel title="SM Blocker Board" action={<Badge tone="info">source + age</Badge>}>
            <LoadState query={blockers}>
              {blockers.data && (
                <div className="divide-y divide-border">
                  {blockers.data.blockers.length === 0 && (
                    <EmptyState>No blockers reported for this pod.</EmptyState>
                  )}
                  {blockers.data.blockers.map((blocker) => (
                    <Row
                      key={blocker.id}
                      primary={blocker.description}
                      secondary={`${blocker.owner_name} / ${blocker.source}`}
                      badge={`${blocker.age_days}d`}
                      tone={blocker.age_days > 0 ? "warning" : "neutral"}
                    />
                  ))}
                </div>
              )}
            </LoadState>
          </Panel>

          <Panel
            title="PO Project Progress"
            action={progress.data && <RagBadge rag={progress.data.rag} />}
          >
            <LoadState query={progress}>
              {progress.data && (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-5">
                    <Count label="Done" value={progress.data.green_tasks} tone="success" />
                    <Count label="At risk" value={progress.data.amber_tasks} tone="warning" />
                    <Count label="Blocked" value={progress.data.red_tasks} tone="danger" />
                    <Count label="Unknown" value={progress.data.unknown_tasks} tone="neutral" />
                    <Count label="Total" value={progress.data.total_tasks} tone="info" />
                  </div>
                  <div className="divide-y divide-border">
                    {progress.data.tasks.map((task) => (
                      <Row
                        key={task.id}
                        primary={task.name}
                        secondary={sourceLine(task.source, task.confidence)}
                        badge={task.rag}
                        tone={toneForRag(task.rag)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </LoadState>
          </Panel>
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <Panel
            title="Mgr / Exec Program Tree"
            action={<Network className="h-4 w-4 text-muted-foreground" />}
          >
            <LoadState query={tree}>
              <HierarchyFlow data={tree.data} />
            </LoadState>
          </Panel>

          <Panel title="Portfolio Heatmap" action={<Badge tone="info">RAG</Badge>}>
            <LoadState query={heatmap}>
              <HeatmapChart data={heatmap.data} />
              <div className="mt-2 divide-y divide-border">
                {heatmap.data?.cells.map((cell) => (
                  <Row
                    key={`${cell.entity_ref.kind}-${cell.entity_ref.id}`}
                    primary={`${cell.entity_ref.kind}:${cell.entity_ref.id}`}
                    secondary={`${cell.why} / ${cell.source}`}
                    badge={cell.rag}
                    tone={toneForRag(cell.rag)}
                  />
                ))}
              </div>
            </LoadState>
          </Panel>
        </section>
      </div>
    </main>
  );
}

function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded border border-border bg-white">
      <div className="flex min-h-12 items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {action}
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  );
}

function Metric({
  icon,
  label,
  value,
  detail,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded border border-border bg-white px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="text-2xl font-semibold">{value}</div>
        <div className="max-w-32 truncate text-xs text-muted-foreground">{detail}</div>
      </div>
    </div>
  );
}

function LoadState({
  query,
  children,
}: {
  query: { isLoading: boolean; isError: boolean; error: Error | null };
  children: ReactNode;
}) {
  if (query.isLoading) {
    return <EmptyState>Loading...</EmptyState>;
  }
  if (query.isError) {
    return (
      <EmptyState>
        {query.error?.message.includes("403")
          ? "Role scope does not include this view."
          : "Request failed."}
      </EmptyState>
    );
  }
  return children;
}

function ListBlock({
  title,
  empty,
  items,
}: {
  title: string;
  empty: string;
  items: Array<{ id: string; primary: string; secondary: string; badge: string; tone: BadgeTone }>;
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{title}</div>
      <div className="divide-y divide-border rounded border border-border">
        {items.length === 0 && <EmptyState>{empty}</EmptyState>}
        {items.map((item) => (
          <Row key={item.id} {...item} />
        ))}
      </div>
    </div>
  );
}

function Row({
  primary,
  secondary,
  badge,
  tone,
}: {
  primary: string;
  secondary: string;
  badge: string;
  tone: BadgeTone;
}) {
  return (
    <div className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium">{primary}</div>
        <div className="truncate text-xs text-muted-foreground">{secondary}</div>
      </div>
      <Badge tone={tone}>{badge}</Badge>
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone: BadgeTone }) {
  return (
    <div className="rounded border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-lg font-semibold">{value}</span>
        <Badge tone={tone}>{label.slice(0, 1)}</Badge>
      </div>
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return <div className="px-3 py-5 text-sm text-muted-foreground">{children}</div>;
}

function RagBadge({ rag }: { rag: Rag }) {
  return <Badge tone={toneForRag(rag)}>{rag}</Badge>;
}

function SourceBadge({ source }: { source: StatusSource | undefined }) {
  return <Badge tone={source === "confirmed" ? "success" : "neutral"}>{source ?? "unknown"}</Badge>;
}

function sourceLine(source: StatusSource, confidence: number | null): string {
  return `${source}${confidence === null ? "" : ` / ${Math.round(confidence * 100)}%`}`;
}

function statusForQuery(query: { isError: boolean; isLoading: boolean }): string {
  if (query.isError) {
    return "unavailable";
  }
  return query.isLoading ? "loading" : "ready";
}

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

function toneForRag(rag: Rag): BadgeTone {
  if (rag === "green") {
    return "success";
  }
  if (rag === "amber") {
    return "warning";
  }
  if (rag === "red") {
    return "danger";
  }
  return "neutral";
}

function toneForState(state: "confirmed" | "stale" | "missing"): BadgeTone {
  if (state === "confirmed") {
    return "success";
  }
  if (state === "stale") {
    return "warning";
  }
  return "danger";
}
