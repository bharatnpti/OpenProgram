import { useQuery } from "@tanstack/react-query";
import { Network, TableCellsSplit } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { apiClient } from "../api/client";
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
import { resolveSelection } from "../lib/selection";
import { HeatmapChart } from "../features/personas/HeatmapChart";
import { HierarchyFlow } from "../features/personas/HierarchyFlow";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function PortfolioPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const [programId, setProgramId] = useState("");
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
  const refreshing = [programs, tree, heatmap].some((query) => query.isFetching);

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
          </>
        )}
      </div>
    </main>
  );
}
