import { useMutation, useQuery } from "@tanstack/react-query";
import { Handshake, Network, TableCellsSplit } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { apiClient, ApiError } from "../api/client";
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
} from "../components/ops/primitives";
import { StatusBadge } from "../components/ops/status";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { resolveSelection } from "../lib/selection";
import { HeatmapChart } from "../features/personas/HeatmapChart";
import { HierarchyFlow } from "../features/personas/HierarchyFlow";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function PortfolioPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const [programId, setProgramId] = useState("");
  const [feedSince, setFeedSince] = useState(
    () => localStorage.getItem("portfolio-feed-since") ?? "",
  );
  const [question, setQuestion] = useState("");
  const programs = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
  });

  const selectedProgramId = useMemo(
    () => resolveSelection(programId, programs.data),
    [programId, programs.data],
  );

  useEffect(() => {
    if (selectedProgramId !== programId) {
      setProgramId(selectedProgramId);
    }
  }, [programId, selectedProgramId]);

  const selectedProgram = programs.data?.find((program) => program.id === selectedProgramId);
  const tree = useQuery({
    queryKey: ["persona", "tree", selectedProgramId, asOf],
    queryFn: () => apiClient.personaProgramTree(selectedProgramId, asOf),
    enabled: Boolean(selectedProgramId),
    staleTime: 5 * 60_000,
  });
  const heatmap = useQuery({
    queryKey: ["persona", "heatmap", selectedProgramId, asOf],
    queryFn: () => apiClient.portfolioHeatmap(asOf, selectedProgramId),
    enabled: Boolean(selectedProgramId),
    staleTime: 5 * 60_000,
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
    enabled: Boolean(selectedProgramId),
  });
  const feed = useQuery({
    queryKey: ["persona", "portfolio-feed", feedSince],
    queryFn: () => apiClient.portfolioFeed(feedSince || undefined),
    enabled: Boolean(selectedProgramId),
    staleTime: 60_000,
  });
  const inbox = useQuery({
    queryKey: ["persona", "my-cross-person-requests"],
    queryFn: async () => {
      try {
        return await apiClient.myCrossPersonRequests();
      } catch (error) {
        if (error instanceof ApiError && error.status === 403) {
          return { requests: [] };
        }
        throw error;
      }
    },
    enabled: Boolean(selectedProgramId),
    staleTime: 60_000,
  });
  const ask = useMutation({
    mutationFn: async (input: { question: string }) =>
      apiClient.ask({ question: input.question, as_of: asOf }),
  });
  const workstreamById = useMemo(
    () => new Map((workstreams.data ?? []).map((workstream) => [workstream.id, workstream])),
    [workstreams.data],
  );
  const atRiskWorkstreams = useMemo(
    () =>
      (heatmap.data?.cells ?? [])
        .filter(
          (cell) =>
            cell.entity_ref.kind === "workstream" && (cell.rag === "red" || cell.rag === "amber"),
        )
        .sort((left, right) => ragRank(right.rag) - ragRank(left.rag))
        .slice(0, 5),
    [heatmap.data?.cells],
  );
  const refreshing = [programs, tree, heatmap, workstreams].some((query) => query.isFetching);
  const feedItems = feed.data?.items ?? [];
  const lastFeedSeen = feedItems.length > 0 ? feedItems[0].observed_at : feedSince || null;

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Portfolio"
          title="Program Health"
          description="Program hierarchy and heatmap for configured programs. Raw developer messages are not exposed here."
          actions={selectedProgram?.rag && <StatusBadge rag={selectedProgram.rag} />}
        />

        <Toolbar>
          <EntitySelector
            value={selectedProgramId}
            onChange={setProgramId}
            items={programs.data ?? []}
            placeholder="Select program"
            className="min-w-56"
          />
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={refreshing}
            onClick={() => {
              void programs.refetch();
              void tree.refetch();
              void heatmap.refetch();
              void workstreams.refetch();
              void feed.refetch();
            }}
          />
        </Toolbar>

        {!selectedProgramId ? (
          <EmptyState
            title="No programs configured"
            description="Add a program in Admin Config before reviewing portfolio health."
          />
        ) : (
          <>
            <section className="grid gap-3 md:grid-cols-3">
              <KpiCard
                icon={<Network className="h-4 w-4" />}
                label="Program"
                value={selectedProgram?.name ?? selectedProgramId}
                detail={selectedProgram?.id}
                tone="info"
              />
              <KpiCard
                label="Tree nodes"
                value={tree.data?.nodes.length ?? "-"}
                detail={tree.isFetching ? "refreshing" : "hierarchy"}
              />
              <KpiCard
                icon={<TableCellsSplit className="h-4 w-4" />}
                label="Heatmap cells"
                value={heatmap.data?.cells.length ?? "-"}
                detail={heatmap.isFetching ? "refreshing" : "RAG cells"}
                tone="warning"
              />
            </section>

            <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
              <DataPanel
                title="Program Tree"
                description="Graph-backed hierarchy with rollup status at each layer."
              >
                <QueryState query={tree} loadingRows={6}>
                  {(data) => <HierarchyFlow data={data} />}
                </QueryState>
              </DataPanel>
              <DataPanel title="Portfolio Heatmap" description="RAG rollup cells and reasons.">
                <QueryState query={heatmap} loadingRows={6}>
                  {(data) => (
                    <div className="space-y-3">
                      <HeatmapChart data={data} />
                      {atRiskWorkstreams.length === 0 ? (
                        <EmptyState title="No at-risk workstreams" />
                      ) : (
                        <div className="divide-y divide-border rounded-md border border-border">
                          {atRiskWorkstreams.map((cell) => {
                            const workstream = workstreamById.get(cell.entity_ref.id);
                            return (
                              <Link
                                key={cell.entity_ref.id}
                                to={`/workstreams/${cell.entity_ref.id}`}
                                className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-sm hover:bg-surface-muted/50"
                              >
                                <div className="min-w-0">
                                  <div className="truncate font-medium">
                                    {workstream?.name ?? cell.entity_ref.id}
                                  </div>
                                  <div className="truncate text-xs text-muted-foreground">
                                    {cell.why} / {cell.source}
                                  </div>
                                </div>
                                <StatusBadge rag={cell.rag} />
                              </Link>
                            );
                          })}
                        </div>
                      )}
                      <div className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border scrollbar-thin">
                        {data.cells.map((cell) => (
                          <div
                            key={`${cell.entity_ref.kind}-${cell.entity_ref.id}`}
                            className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-sm"
                          >
                            <div className="min-w-0">
                              <div className="truncate font-medium">
                                {cell.entity_ref.kind}:{cell.entity_ref.id}
                              </div>
                              <div className="truncate text-xs text-muted-foreground">
                                {cell.why} / {cell.source}
                              </div>
                            </div>
                            <StatusBadge rag={cell.rag} />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </QueryState>
              </DataPanel>
            </section>

            <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <DataPanel
                title="Portfolio Feed"
                description="What changed since you last looked."
                action={
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      const next = feedItems[0]?.observed_at ?? "";
                      setFeedSince(next);
                      localStorage.setItem("portfolio-feed-since", next);
                    }}
                    disabled={feedItems.length === 0}
                  >
                    Mark viewed
                  </Button>
                }
              >
                <QueryState query={feed} loadingRows={4}>
                  {(data) =>
                    data.items.length === 0 ? (
                      <EmptyState
                        title="Nothing new"
                        description="No recent portfolio events were found for the current cursor."
                      />
                    ) : (
                      <div className="space-y-3">
                        <div className="text-xs text-muted-foreground">
                          Since {data.since ?? lastFeedSeen ?? "the beginning"} ·{" "}
                          {data.items.length} events
                        </div>
                        <div className="divide-y divide-border rounded-md border border-border">
                          {data.items.map((item) => (
                            <div
                              key={`${item.source}:${item.entity_ref.kind}:${item.entity_ref.id}:${item.observed_at}`}
                              className="px-3 py-2"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-medium">{item.summary}</div>
                                  <div className="mt-0.5 text-xs text-muted-foreground">
                                    {item.entity_ref.kind}:{item.entity_ref.id} · {item.source}
                                  </div>
                                </div>
                                <div className="shrink-0 text-xs text-muted-foreground">
                                  {new Date(item.observed_at).toLocaleString()}
                                </div>
                              </div>
                              {Object.keys(item.details).length > 0 && (
                                <div className="mt-2 text-xs text-muted-foreground">
                                  {Object.entries(item.details)
                                    .filter(
                                      ([, value]) =>
                                        value !== null && value !== undefined && value !== "",
                                    )
                                    .map(([key, value]) => `${key}: ${String(value)}`)
                                    .join(" · ")}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  }
                </QueryState>
              </DataPanel>

              <div className="space-y-4">
                <DataPanel
                  title="Request Inbox"
                  description="Open cross-person requests assigned to you."
                  action={
                    <Link
                      to="/cross-person-requests"
                      className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-2.5 text-xs font-medium text-muted-foreground hover:bg-surface-muted hover:text-foreground"
                    >
                      <Handshake className="h-3.5 w-3.5" />
                      View all
                    </Link>
                  }
                >
                  <QueryState query={inbox} loadingRows={2}>
                    {(data) =>
                      data.requests.length === 0 ? (
                        <EmptyState title="No open requests" />
                      ) : (
                        <div className="divide-y divide-border rounded-md border border-border">
                          {data.requests.slice(0, 4).map((request) => (
                            <Link
                              key={request.id}
                              to="/cross-person-requests"
                              className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-sm hover:bg-surface-muted/50"
                            >
                              <div className="min-w-0">
                                <div className="truncate font-medium">{request.note}</div>
                                <div className="truncate text-xs text-muted-foreground">
                                  {request.kind} · from {request.requester_id}
                                </div>
                              </div>
                              <Handshake className="h-4 w-4 shrink-0 text-info" />
                            </Link>
                          ))}
                        </div>
                      )
                    }
                  </QueryState>
                </DataPanel>

                <DataPanel
                  title="Ask the graph"
                  description="LLM tool-calling over work items, flow, and recent facts."
                >
                  <form
                    className="space-y-3"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (!question.trim()) return;
                      ask.mutate({ question: question.trim() });
                    }}
                  >
                    <Textarea
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      rows={5}
                      placeholder='Try "what is stuck in payments?"'
                    />
                    <div className="flex items-center gap-2">
                      <Button type="submit" disabled={ask.isPending || !question.trim()}>
                        {ask.isPending ? "Asking..." : "Ask"}
                      </Button>
                      {ask.data && (
                        <span className="text-xs text-muted-foreground">
                          Trace {ask.data.trace_id}
                        </span>
                      )}
                    </div>
                  </form>
                  {ask.isError && (
                    <div className="mt-3 rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
                      {(ask.error as Error).message}
                    </div>
                  )}
                  {ask.data && (
                    <div className="mt-4 space-y-3">
                      <div className="rounded-md border border-border px-3 py-2 text-sm">
                        {ask.data.answer}
                      </div>
                      {ask.data.references.length > 0 && (
                        <div>
                          <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
                            References
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {ask.data.references.map((reference) => (
                              <span
                                key={reference}
                                className="rounded-full border border-border px-2 py-1 text-xs text-muted-foreground"
                              >
                                {reference}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {ask.data.tools_used.length > 0 && (
                        <div className="text-xs text-muted-foreground">
                          Tools: {ask.data.tools_used.join(", ")}
                        </div>
                      )}
                    </div>
                  )}
                </DataPanel>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}

function ragRank(rag: string) {
  if (rag === "red") return 3;
  if (rag === "amber") return 2;
  if (rag === "green") return 1;
  return 0;
}
