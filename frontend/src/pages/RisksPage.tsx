import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { ExternalLink, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { apiClient } from "../api/client";
import type { PortfolioRisksResponse, RiskFindingResponse } from "../api/schema";
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
import { SourceConfidence, StatusBadge } from "../components/ops/status";

const todayIso = () => new Date().toISOString().slice(0, 10);

const RULE_LABELS: Record<string, string> = {
  feature_no_pr: "No linked PR",
  pr_age: "PR open too long",
  stale_work_item: "Stale work item",
};

export function RisksPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const query = useQuery({
    queryKey: ["persona", "portfolio-risks", asOf],
    queryFn: () => apiClient.portfolioRisks(asOf),
  });

  const rows = query.data?.risks ?? [];
  const watermelonCount = rows.filter((risk) => risk.is_watermelon).length;

  const columns = useMemo<ColumnDef<RiskFindingResponse>[]>(
    () => [
      {
        accessorKey: "rule_id",
        header: "Signal",
        cell: ({ row }) => (
          <div className="flex flex-col gap-1">
            <span className="font-medium text-foreground">
              {RULE_LABELS[row.original.rule_id] ?? row.original.rule_id}
            </span>
            <StatusBadge rag={row.original.severity} />
          </div>
        ),
      },
      {
        accessorKey: "reason",
        header: "Reason",
        cell: ({ row }) => (
          <div className="flex flex-col gap-1 text-sm">
            <span>{row.original.reason}</span>
            <EvidenceLink evidence={row.original.evidence} />
          </div>
        ),
      },
      { accessorKey: "age_days", header: "Age (days)" },
      {
        id: "owner_status",
        header: "Owner reported status",
        cell: ({ row }) => (
          <div className="flex flex-col gap-1">
            <span className="text-sm">
              {row.original.owner_status_summary ?? "No check-in on record"}
            </span>
            {row.original.owner_status_source && (
              <SourceConfidence source={row.original.owner_status_source} confidence={null} />
            )}
          </div>
        ),
      },
      {
        id: "watermelon",
        header: "Divergence",
        cell: ({ row }) =>
          row.original.is_watermelon ? (
            <span className="inline-flex items-center gap-1 rounded-md bg-danger/10 px-2 py-1 text-xs font-medium text-danger">
              <ShieldAlert className="h-3.5 w-3.5" />
              Watermelon
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">-</span>
          ),
      },
    ],
    [],
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Risk"
          title="Signal-Derived Risks"
          description="Deterministic, no-human-input risk findings from PRs and work item activity, shown next to each owner's own reported status."
          actions={
            <span className="text-sm text-muted-foreground">
              {query.data ? `As of ${query.data.as_of}` : ""}
            </span>
          }
        />

        <Toolbar>
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton refreshing={query.isFetching} onClick={() => void query.refetch()} />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            icon={<ShieldAlert className="h-4 w-4" />}
            label="Open risks"
            value={rows.length}
            detail="signal-derived findings currently open"
            tone="warning"
          />
          <KpiCard
            icon={<ShieldAlert className="h-4 w-4" />}
            label="Watermelons"
            value={watermelonCount}
            detail="owner reports fine, signal says risk"
            tone="danger"
          />
          <KpiCard
            icon={<ShieldAlert className="h-4 w-4" />}
            label="Red severity"
            value={rows.filter((risk) => risk.severity === "red").length}
            detail="findings at the higher severity band"
            tone="danger"
          />
        </section>

        <DataPanel
          title="Open Risk Findings"
          description="No human input required -- computed from PR and work-item activity already tracked."
        >
          <QueryState query={query} loadingRows={6}>
            {(data: PortfolioRisksResponse) => (
              <DataTable
                data={data.risks}
                columns={columns}
                emptyTitle="No open risks"
                emptyDescription="Nothing crossed the configured PR-age, no-PR, or staleness thresholds."
              />
            )}
          </QueryState>
        </DataPanel>
      </div>
    </main>
  );
}

function EvidenceLink({ evidence }: { evidence: RiskFindingResponse["evidence"] }) {
  if (!evidence.url) {
    return <span className="text-xs text-muted-foreground">{evidence.identifier}</span>;
  }
  return (
    <a
      href={evidence.url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
    >
      {evidence.identifier}
      <ExternalLink className="h-3 w-3" />
    </a>
  );
}
