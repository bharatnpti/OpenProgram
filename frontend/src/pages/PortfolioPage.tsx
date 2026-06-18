import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { apiClient } from "../api/client";
import { resolveSelection } from "../lib/selection";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Select } from "../components/ui/select";
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

  return (
    <main className="px-5 py-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-semibold">Portfolio</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Program tree and heatmap for configured programs.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={selectedProgramId}
            onChange={(event) => setProgramId(event.target.value)}
            className="min-w-48"
          >
            {programs.data?.map((program) => (
              <option key={program.id} value={program.id}>
                {program.name}
              </option>
            ))}
          </Select>
          <input
            type="date"
            className="h-9 rounded border border-border bg-white px-3 text-sm"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
          <Button
            onClick={() => {
              void programs.refetch();
              void tree.refetch();
              void heatmap.refetch();
            }}
          >
            Refresh
          </Button>
        </div>
      </header>

      {!selectedProgramId && (
        <p className="text-sm text-muted-foreground">
          No programs configured. Add a program in Admin Config first.
        </p>
      )}

      {selectedProgramId && (
        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <Panel title="Program tree">
            {tree.isLoading && <p className="text-sm text-muted-foreground">Loading tree...</p>}
            {tree.isError && <p className="text-sm text-red-600">Tree unavailable.</p>}
            {tree.data && <HierarchyFlow data={tree.data} />}
          </Panel>
          <Panel title="Portfolio heatmap">
            {heatmap.isLoading && <p className="text-sm text-muted-foreground">Loading heatmap...</p>}
            {heatmap.isError && <p className="text-sm text-red-600">Heatmap unavailable.</p>}
            {heatmap.data && (
              <>
                <HeatmapChart data={heatmap.data} />
                <div className="mt-2 divide-y divide-border">
                  {heatmap.data.cells.map((cell) => (
                    <div
                      key={`${cell.entity_ref.kind}-${cell.entity_ref.id}`}
                      className="flex items-center justify-between gap-2 py-2 text-sm"
                    >
                      <span>
                        {cell.entity_ref.kind}:{cell.entity_ref.id}
                      </span>
                      <Badge tone="info">{cell.rag}</Badge>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Panel>
        </section>
      )}
    </main>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-border bg-white">
      <div className="border-b border-border px-4 py-3 text-sm font-semibold">{title}</div>
      <div className="px-4 py-4">{children}</div>
    </section>
  );
}
