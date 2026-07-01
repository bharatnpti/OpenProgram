import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { Activity, Clock3, PlayCircle, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import type { PortfolioFlowResponse, WorkstreamFlowSummaryResponse } from "../api/schema";
import { DataTable } from "../components/ops/DataTable";
import {
  AsOfControl,
  DataPanel,
  KpiCard,
  PageHeader,
  QueryState,
  RefreshButton,
  Toolbar,
} from "../components/ops/primitives";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function FlowPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const query = useQuery({
    queryKey: ["persona", "portfolio-flow", asOf],
    queryFn: () => apiClient.portfolioFlow(asOf),
  });

  const rows = query.data?.workstreams ?? [];
  const columns = useMemo<ColumnDef<WorkstreamFlowSummaryResponse>[]>(
    () => [
      {
        accessorKey: "workstream_name",
        header: "Workstream",
        cell: ({ row }) => (
          <Link className="font-medium text-foreground hover:underline" to={`/workstreams/${row.original.workstream_id}`}>
            {row.original.workstream_name}
          </Link>
        ),
      },
      { accessorKey: "active_count", header: "Active" },
      { accessorKey: "features_in_flight", header: "In flight" },
      { accessorKey: "completed_count", header: "Done" },
      { accessorKey: "stale_count", header: "Stale" },
      { accessorKey: "abandoned_count", header: "Abandoned" },
      {
        accessorKey: "avg_cycle_time_days",
        header: "Cycle time",
        cell: ({ getValue }) => formatDays(getValue<number | null>()),
      },
      {
        accessorKey: "avg_pr_age_days",
        header: "PR age",
        cell: ({ getValue }) => formatDays(getValue<number | null>()),
      },
    ],
    [],
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Flow"
          title="Portfolio Flow"
          description="What is moving, what is stuck, and how much is in flight across the portfolio."
          actions={<span className="text-sm text-muted-foreground">{query.data ? `As of ${query.data.as_of}` : ""}</span>}
        />

        <Toolbar>
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton refreshing={query.isFetching} onClick={() => void query.refetch()} />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-4">
          <KpiCard
            icon={<Activity className="h-4 w-4" />}
            label="Active"
            value={query.data?.active_count ?? "-"}
            detail="work items in active states"
            tone="info"
          />
          <KpiCard
            icon={<PlayCircle className="h-4 w-4" />}
            label="Features in flight"
            value={query.data?.features_in_flight ?? "-"}
            detail="feature work items currently moving"
            tone="success"
          />
          <KpiCard
            icon={<Clock3 className="h-4 w-4" />}
            label="Stale"
            value={query.data?.stale_count ?? "-"}
            detail="active items idle for 7+ days"
            tone="warning"
          />
          <KpiCard
            icon={<TriangleAlert className="h-4 w-4" />}
            label="Abandoned"
            value={query.data?.abandoned_count ?? "-"}
            detail="active items idle for 21+ days"
            tone="danger"
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <DataPanel title="Portfolio by Workstream" description="Sortable flow and throughput table.">
            <QueryState query={query} loadingRows={6}>
              {(data: PortfolioFlowResponse) => (
                <DataTable
                  data={data.workstreams}
                  columns={columns}
                  emptyTitle="No workstreams"
                  emptyDescription="Add work items and link them to workstreams to see flow metrics."
                />
              )}
            </QueryState>
          </DataPanel>

          <DataPanel title="Portfolio Totals" description="Cycle and PR aging across the selected snapshot.">
            <QueryState query={query}>
              {(data) => (
                <div className="space-y-2 text-sm">
                  <MetricRow label="Completed" value={data.completed_count} />
                  <MetricRow label="Avg cycle time" value={formatDays(data.avg_cycle_time_days)} />
                  <MetricRow label="Avg PR age" value={formatDays(data.avg_pr_age_days)} />
                  <MetricRow label="As of" value={data.as_of} />
                </div>
              )}
            </QueryState>
          </DataPanel>
        </section>
      </div>
    </main>
  );
}

function MetricRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function formatDays(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value.toFixed(1)}d`;
}
