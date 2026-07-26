import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarCheck,
  CheckCircle2,
  GitPullRequest,
  Network,
  Pencil,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { apiClient } from "../../api/client";
import type { NodeTrendResponse } from "../../api/schema";
import {
  AsOfControl,
  DataPanel,
  EmptyState,
  EntitySelector,
  KpiCard,
  PageHeader,
  QueryState,
  RefreshButton,
  Toolbar,
} from "../../components/ops/primitives";
import { SourceConfidence, StateBadge, StatusBadge } from "../../components/ops/status";
import { sourceLine, toneForRag, type BadgeTone } from "../../components/ops/status-utils";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Dialog } from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import { resolveSelection } from "../../lib/selection";
import { HeatmapChart } from "./HeatmapChart";
import { HierarchyFlow } from "./HierarchyFlow";
import { Sparkline } from "./Sparkline";

const todayIso = () => new Date().toISOString().slice(0, 10);

type DashboardRole = "dev" | "sm" | "po" | "mgr" | "exec";

const roleTitles: Record<DashboardRole, string> = {
  dev: "Developer Focus",
  sm: "Scrum Master Team Health",
  po: "Product Owner Delivery View",
  mgr: "Manager Portfolio Health",
  exec: "Executive Portfolio Health",
};

const roleDescriptions: Record<DashboardRole, string> = {
  dev: "Current work, blockers, source quality, and assigned tasks.",
  sm: "Check-in completeness, stale/missing signals, and open blocker aging.",
  po: "Project completion, RAG task mix, and source confidence.",
  mgr: "Program hierarchy and RAG heatmap for delivery-layer drill-down.",
  exec: "Program hierarchy and portfolio health without raw developer-message exposure.",
};

export function PersonaDashboard({ role }: { role: DashboardRole }) {
  const queryClient = useQueryClient();
  const [asOf, setAsOf] = useState(todayIso);
  const [podId, setPodId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [programId, setProgramId] = useState("");
  const [correctOpen, setCorrectOpen] = useState(false);
  const [statusSummary, setStatusSummary] = useState("");
  const [statusBlockers, setStatusBlockers] = useState("");
  const [statusEtaChange, setStatusEtaChange] = useState("");

  const showFocus = role === "dev";
  const showTeam = role === "sm";
  const showProgress = role === "po";
  const showPortfolio = role === "mgr" || role === "exec";
  // The Manager view adds a delivery-momentum trend so it is no longer a
  // byte-for-byte copy of the point-in-time Exec heatmap.
  const showTrend = role === "mgr";

  const podsDirectory = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
    enabled: showTeam,
  });
  const projectsDirectory = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
    enabled: showProgress,
  });
  const programsDirectory = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
    enabled: showPortfolio,
  });

  const selectedPodId = useMemo(
    () => resolveSelection(podId, podsDirectory.data),
    [podId, podsDirectory.data],
  );
  const selectedProjectId = useMemo(
    () => resolveSelection(projectId, projectsDirectory.data),
    [projectId, projectsDirectory.data],
  );
  const selectedProgramId = useMemo(
    () => resolveSelection(programId, programsDirectory.data),
    [programId, programsDirectory.data],
  );

  useEffect(() => {
    if (selectedPodId !== podId) setPodId(selectedPodId);
  }, [podId, selectedPodId]);
  useEffect(() => {
    if (selectedProjectId !== projectId) setProjectId(selectedProjectId);
  }, [projectId, selectedProjectId]);
  useEffect(() => {
    if (selectedProgramId !== programId) setProgramId(selectedProgramId);
  }, [programId, selectedProgramId]);

  const selectedPod = podsDirectory.data?.find((pod) => pod.id === selectedPodId);
  const selectedProject = projectsDirectory.data?.find(
    (project) => project.id === selectedProjectId,
  );
  const selectedProgram = programsDirectory.data?.find(
    (program) => program.id === selectedProgramId,
  );

  const health = useQuery({ queryKey: ["health"], queryFn: apiClient.health });
  const focus = useQuery({
    queryKey: ["persona", "focus", asOf],
    queryFn: () => apiClient.focus(asOf),
    enabled: showFocus,
  });
  const myStatus = useQuery({
    queryKey: ["persona", "my-status", asOf],
    queryFn: () => apiClient.myStatus(asOf),
    enabled: showFocus,
  });
  const confirmStatus = useMutation({
    mutationFn: () => apiClient.confirmMyStatus(asOf),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-status"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "focus"] });
    },
  });
  const correctStatus = useMutation({
    mutationFn: () =>
      apiClient.correctMyStatus(
        {
          summary: statusSummary,
          blockers: statusBlockers
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean),
          eta_change_days: statusEtaChange.trim() ? Number(statusEtaChange) : null,
        },
        asOf,
      ),
    onSuccess: async () => {
      setCorrectOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-status"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "focus"] });
    },
  });
  const blockers = useQuery({
    queryKey: ["persona", "blockers", selectedPodId, asOf],
    queryFn: () => apiClient.podBlockers(selectedPodId, asOf),
    enabled: showTeam && Boolean(selectedPodId),
  });
  const checkins = useQuery({
    queryKey: ["persona", "checkins", selectedPodId, asOf],
    queryFn: () => apiClient.podCheckins(selectedPodId, asOf),
    enabled: showTeam && Boolean(selectedPodId),
  });
  const progress = useQuery({
    queryKey: ["persona", "progress", selectedProjectId, asOf],
    queryFn: () => apiClient.projectProgress(selectedProjectId, asOf),
    enabled: showProgress && Boolean(selectedProjectId),
  });
  const tree = useQuery({
    queryKey: ["persona", "tree", selectedProgramId, asOf],
    queryFn: () => apiClient.personaProgramTree(selectedProgramId, asOf),
    enabled: showPortfolio && Boolean(selectedProgramId),
    staleTime: 5 * 60_000,
  });
  const heatmap = useQuery({
    queryKey: ["persona", "heatmap", selectedProgramId, asOf],
    queryFn: () => apiClient.portfolioHeatmap(asOf, selectedProgramId),
    enabled: showPortfolio && Boolean(selectedProgramId),
    staleTime: 5 * 60_000,
  });
  const trend = useQuery({
    queryKey: ["persona", "trend", selectedProgramId, asOf],
    queryFn: () => apiClient.nodeTrend("program", selectedProgramId, { asOf, windowDays: 30 }),
    enabled: showTrend && Boolean(selectedProgramId),
    staleTime: 5 * 60_000,
  });

  const queries = [
    health,
    ...(showFocus ? [focus, myStatus] : []),
    ...(showTeam ? [podsDirectory, blockers, checkins] : []),
    ...(showProgress ? [projectsDirectory, progress] : []),
    ...(showPortfolio ? [programsDirectory, tree, heatmap] : []),
    ...(showTrend ? [trend] : []),
  ];
  const isRefreshing = queries.some((query) => query.isFetching);

  useEffect(() => {
    if (!myStatus.data) return;
    setStatusSummary(myStatus.data.summary);
    setStatusBlockers(myStatus.data.blockers.join("\n"));
    setStatusEtaChange(
      myStatus.data.eta_change_days === null || myStatus.data.eta_change_days === undefined
        ? ""
        : String(myStatus.data.eta_change_days),
    );
  }, [myStatus.data]);

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow={
            <span className="inline-flex items-center gap-2">
              <Badge tone={health.data?.status === "ok" ? "success" : "warning"}>
                {health.data?.status ?? "checking"}
              </Badge>
              {health.data?.environment ?? "local"} / {health.data?.tenant_id ?? "demo"}
            </span>
          }
          title={roleTitles[role]}
          description={roleDescriptions[role]}
        />

        <Toolbar>
          {showTeam && (
            <EntitySelector
              value={selectedPodId}
              onChange={setPodId}
              items={podsDirectory.data ?? []}
              placeholder="Select pod"
              className="min-w-52"
            />
          )}
          {showProgress && (
            <EntitySelector
              value={selectedProjectId}
              onChange={setProjectId}
              items={projectsDirectory.data ?? []}
              placeholder="Select project"
              className="min-w-52"
            />
          )}
          {showPortfolio && (
            <EntitySelector
              value={selectedProgramId}
              onChange={setProgramId}
              items={programsDirectory.data ?? []}
              placeholder="Select program"
              className="min-w-52"
            />
          )}
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={isRefreshing}
            onClick={() => {
              queries.forEach((query) => void query.refetch());
            }}
          />
          {(selectedPod || selectedProject || selectedProgram) && (
            <span className="text-xs text-muted-foreground">
              Context: {selectedPod?.name ?? selectedProject?.name ?? selectedProgram?.name}
            </span>
          )}
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {showFocus && (
            <KpiCard
              icon={<UserRound className="h-4 w-4" />}
              label="Focus items"
              value={focus.data?.focus.length ?? "-"}
              detail={
                myStatus.data?.developer_confirmed
                  ? "confirmed by you"
                  : (focus.data?.status_source ?? statusForQuery(focus))
              }
              tone={myStatus.data?.developer_confirmed ? "success" : "info"}
            />
          )}
          {showFocus && (
            <KpiCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label="Assigned tasks"
              value={focus.data?.tasks.length ?? "-"}
              detail="source-linked"
              tone="neutral"
            />
          )}
          {showTeam && (
            <KpiCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label="Open blockers"
              value={blockers.data?.blockers.length ?? "-"}
              detail={statusForQuery(blockers)}
              tone={blockers.data?.blockers.length ? "warning" : "success"}
            />
          )}
          {showTeam && (
            <KpiCard
              icon={<CalendarCheck className="h-4 w-4" />}
              label="Check-ins"
              value={
                checkins.data
                  ? `${checkins.data.confirmed}/${checkins.data.developers.length}`
                  : "-"
              }
              detail={
                checkins.data
                  ? `${checkins.data.partial} partial / ${checkins.data.missing} missing`
                  : statusForQuery(checkins)
              }
              tone={checkins.data?.missing ? "danger" : checkins.data?.partial ? "info" : "success"}
            />
          )}
          {showProgress && (
            <KpiCard
              icon={<GitPullRequest className="h-4 w-4" />}
              label="Project progress"
              value={progress.data ? `${Math.round(progress.data.percent_complete)}%` : "-"}
              detail={progress.data?.source ?? statusForQuery(progress)}
              tone={toneForRag(progress.data?.rag)}
            />
          )}
          {showProgress && (
            <KpiCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label="Blocked tasks"
              value={progress.data?.red_tasks ?? "-"}
              detail={progress.data ? `${progress.data.total_tasks} total` : "loading"}
              tone={progress.data?.red_tasks ? "danger" : "success"}
            />
          )}
          {showPortfolio && (
            <KpiCard
              icon={<Network className="h-4 w-4" />}
              label="Tree nodes"
              value={tree.data?.nodes.length ?? "-"}
              detail={statusForQuery(tree)}
              tone="info"
            />
          )}
          {showPortfolio && (
            <KpiCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label="Heatmap cells"
              value={heatmap.data?.cells.length ?? "-"}
              detail={statusForQuery(heatmap)}
              tone="warning"
            />
          )}
        </section>

        {showFocus && (
          <DataPanel
            title="Work Focus"
            description="Source-backed priorities and tasks. Silence is never counted as green."
            action={
              <div className="flex flex-wrap items-center gap-2">
                <SourceConfidence source={focus.data?.status_source} />
                <Badge tone={myStatus.data?.developer_confirmed ? "success" : "warning"}>
                  {myStatus.data?.developer_confirmed ? "confirmed by you" : "inferred/stale"}
                </Badge>
              </div>
            }
          >
            <QueryState query={focus}>
              {(data) => (
                <div className="grid gap-4 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
                  <div className="space-y-4">
                    <div className="border-b border-border pb-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="text-sm font-semibold">{data.developer_name}</h3>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {myStatus.data?.summary ?? data.summary}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-wrap gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={
                              confirmStatus.isPending ||
                              correctStatus.isPending ||
                              !myStatus.data ||
                              myStatus.data.developer_confirmed
                            }
                            onClick={() => confirmStatus.mutate()}
                          >
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Confirm
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={!myStatus.data || correctStatus.isPending}
                            onClick={() => setCorrectOpen(true)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                            Correct
                          </Button>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(myStatus.data?.blockers ?? data.blockers).map((blocker) => (
                          <Badge key={blocker} tone="danger">
                            {blocker}
                          </Badge>
                        ))}
                        {myStatus.data?.eta_change_days !== null &&
                          myStatus.data?.eta_change_days !== undefined && (
                            <Badge tone="warning">{myStatus.data.eta_change_days}d ETA</Badge>
                          )}
                      </div>
                    </div>
                    <div className="mt-4">
                      <ListBlock
                        empty="No active focus items"
                        items={data.focus.map((item) => ({
                          id: `${item.kind}-${item.label}`,
                          primary: item.label,
                          secondary: `${item.kind} / ${item.source}`,
                          badge: item.deadline ?? item.source_ref.kind,
                          tone: item.kind === "blocker" ? "danger" : "warning",
                        }))}
                      />
                    </div>
                  </div>
                  <ListBlock
                    empty="No assigned tasks"
                    items={data.tasks.map((task) => ({
                      id: task.id,
                      primary: task.name,
                      secondary: sourceLine(task.source, task.confidence),
                      badge: task.rag,
                      tone: toneForRag(task.rag),
                    }))}
                  />
                </div>
              )}
            </QueryState>
          </DataPanel>
        )}

        {showTeam && (
          <section className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
            <DataPanel
              title="Check-in Completeness"
              description="Confirmed, partial, stale, and missing developer status."
              action={selectedPod ? <Badge tone="info">{selectedPod.name}</Badge> : undefined}
            >
              <QueryState query={checkins}>
                {(data) => (
                  <div className="space-y-3">
                    <div className="grid grid-cols-4 gap-2 text-center">
                      <Count label="Confirmed" value={data.confirmed} tone="success" />
                      <Count label="Partial" value={data.partial} tone="info" />
                      <Count label="Stale" value={data.stale} tone="warning" />
                      <Count label="Missing" value={data.missing} tone="danger" />
                    </div>
                    <div className="divide-y divide-border rounded-md border border-border">
                      {data.developers.map((developer) => (
                        <ItemRow
                          key={developer.developer_id}
                          primary={developer.developer_name}
                          secondary={developer.summary}
                          badge={<StateBadge state={developer.state} />}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </QueryState>
            </DataPanel>

            <DataPanel
              title="Blocker Board"
              description="Open blockers by owner, source, and age."
              action={<Badge tone="warning">source + age</Badge>}
            >
              <QueryState query={blockers}>
                {(data) =>
                  data.blockers.length === 0 ? (
                    <EmptyState
                      title="No blockers reported"
                      description="This pod has no open blockers for the selected date."
                    />
                  ) : (
                    <div className="divide-y divide-border rounded-md border border-border">
                      {data.blockers.map((blocker) => (
                        <ItemRow
                          key={blocker.id}
                          primary={blocker.description}
                          secondary={`${blocker.owner_name} / ${blocker.source}`}
                          badge={
                            <Badge tone={blocker.age_days > 0 ? "warning" : "neutral"}>
                              {blocker.age_days}d
                            </Badge>
                          }
                        />
                      ))}
                    </div>
                  )
                }
              </QueryState>
            </DataPanel>
          </section>
        )}

        {showProgress && (
          <DataPanel
            title="Project Progress"
            description="Task health, rollup source, and confidence by project."
            action={progress.data && <StatusBadge rag={progress.data.rag} />}
          >
            <QueryState query={progress}>
              {(data) => (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-5">
                    <Count label="Done" value={data.green_tasks} tone="success" />
                    <Count label="At risk" value={data.amber_tasks} tone="warning" />
                    <Count label="Blocked" value={data.red_tasks} tone="danger" />
                    <Count label="Unknown" value={data.unknown_tasks} tone="neutral" />
                    <Count label="Total" value={data.total_tasks} tone="info" />
                  </div>
                  {data.tasks.length === 0 ? (
                    <EmptyState title="No tasks assigned" />
                  ) : (
                    <div className="divide-y divide-border rounded-md border border-border">
                      {data.tasks.map((task) => (
                        <ItemRow
                          key={task.id}
                          primary={task.name}
                          secondary={
                            <SourceConfidence source={task.source} confidence={task.confidence} />
                          }
                          badge={<StatusBadge rag={task.rag} />}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </QueryState>
          </DataPanel>
        )}

        {showTrend && (
          <DataPanel
            title="Delivery Momentum"
            description="30-day RAG trend for the selected program — is it improving or sliding?"
            action={<MomentumBadge data={trend.data} />}
          >
            <QueryState query={trend} loadingRows={3}>
              {(data) => <Sparkline data={data} />}
            </QueryState>
          </DataPanel>
        )}

        {showPortfolio && (
          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
            <DataPanel
              title="Program Hierarchy"
              description="Rollup path from program to execution layers."
            >
              <QueryState query={tree} loadingRows={6}>
                {(data) => <HierarchyFlow data={data} />}
              </QueryState>
            </DataPanel>

            <DataPanel
              title="Portfolio Heatmap"
              description="RAG cells grouped by entity kind and rollup reason."
            >
              <QueryState query={heatmap} loadingRows={6}>
                {(data) => (
                  <div className="space-y-3">
                    <HeatmapChart data={data} />
                    <div className="max-h-72 divide-y divide-border overflow-y-auto rounded-md border border-border scrollbar-thin">
                      {data.cells.map((cell) => (
                        <ItemRow
                          key={`${cell.entity_ref.kind}-${cell.entity_ref.id}`}
                          primary={`${cell.entity_ref.kind}:${cell.entity_ref.id}`}
                          secondary={`${cell.why} / ${cell.source}`}
                          badge={<StatusBadge rag={cell.rag} />}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </QueryState>
            </DataPanel>
          </section>
        )}
      </div>
      {showFocus && (
        <Dialog
          open={correctOpen}
          onOpenChange={setCorrectOpen}
          title="Correct Status"
          description="Update the structured status used by focus and rollups."
        >
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              correctStatus.mutate();
            }}
          >
            <label className="block text-xs font-medium text-muted-foreground">
              Summary
              <Textarea
                className="mt-1"
                value={statusSummary}
                onChange={(event) => setStatusSummary(event.target.value)}
                required
              />
            </label>
            <label className="block text-xs font-medium text-muted-foreground">
              Blockers
              <Textarea
                className="mt-1"
                value={statusBlockers}
                onChange={(event) => setStatusBlockers(event.target.value)}
              />
            </label>
            <label className="block text-xs font-medium text-muted-foreground">
              ETA Change Days
              <Input
                className="mt-1"
                type="number"
                value={statusEtaChange}
                onChange={(event) => setStatusEtaChange(event.target.value)}
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setCorrectOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={correctStatus.isPending}>
                Save
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </main>
  );
}

function ListBlock({
  empty,
  items,
}: {
  empty: string;
  items: Array<{
    id: string;
    primary: ReactNode;
    secondary: ReactNode;
    badge: ReactNode;
    tone: BadgeTone;
  }>;
}) {
  if (items.length === 0) {
    return <EmptyState title={empty} />;
  }
  return (
    <div className="divide-y divide-border rounded-md border border-border">
      {items.map((item) => (
        <ItemRow
          key={item.id}
          primary={item.primary}
          secondary={item.secondary}
          badge={<Badge tone={item.tone}>{item.badge}</Badge>}
        />
      ))}
    </div>
  );
}

function ItemRow({
  primary,
  secondary,
  badge,
}: {
  primary: ReactNode;
  secondary: ReactNode;
  badge: ReactNode;
}) {
  return (
    <div className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium">{primary}</div>
        <div className="truncate text-xs text-muted-foreground">{secondary}</div>
      </div>
      {badge}
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone: BadgeTone }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-lg font-semibold tabular-nums">{value}</span>
        <Badge tone={tone}>{label.slice(0, 1)}</Badge>
      </div>
    </div>
  );
}

function MomentumBadge({ data }: { data: NodeTrendResponse | undefined }) {
  if (!data || data.points.length < 2) {
    return <Badge tone="neutral">insufficient history</Badge>;
  }
  const first = data.points[0].score;
  const last = data.points[data.points.length - 1].score;
  if (last > first) {
    return <Badge tone="success">improving</Badge>;
  }
  if (last < first) {
    return <Badge tone="danger">sliding</Badge>;
  }
  return <Badge tone="info">steady</Badge>;
}

function statusForQuery(query: {
  isError: boolean;
  isLoading: boolean;
  isFetching?: boolean;
}): string {
  if (query.isError) return "unavailable";
  if (query.isLoading) return "loading";
  return query.isFetching ? "refreshing" : "ready";
}
