import { useQuery } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import type { DirectoryItemResponse } from "../api/schema";
import { DataTable, type DataTableColumn } from "../components/ops/DataTable";
import {
  AsOfControl,
  KpiCard,
  PageHeader,
  QueryState,
  RefreshButton,
  SearchInput,
  Toolbar,
} from "../components/ops/primitives";
import { StatusBadge } from "../components/ops/status";
import { Badge } from "../components/ui/badge";

const todayIso = () => new Date().toISOString().slice(0, 10);

export function WorkstreamsPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const [query, setQuery] = useState("");
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
  });

  const filteredWorkstreams = useMemo(
    () => filterDirectoryItems(workstreams.data ?? [], query),
    [workstreams.data, query],
  );

  const columns = useMemo<DataTableColumn<DirectoryItemResponse>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Workstream",
        cell: ({ row }) => (
          <div className="min-w-0">
            <Link
              className="font-medium text-foreground hover:text-primary"
              to={`/workstreams/${row.original.id}`}
            >
              {row.original.name}
            </Link>
            <div className="mt-0.5 flex flex-wrap gap-1">
              <Badge tone="neutral">{metadataLabel(row.original, "type")}</Badge>
              <Badge tone="info">{metadataLabel(row.original, "phase")}</Badge>
            </div>
          </div>
        ),
      },
      {
        accessorKey: "rag",
        header: "RAG",
        cell: ({ row }) => <StatusBadge rag={row.original.rag} />,
      },
      {
        accessorFn: (row) => row.project_ids.length,
        id: "projects",
        header: "Projects",
        cell: ({ row }) => <span className="tabular-nums">{row.original.project_ids.length}</span>,
      },
      {
        accessorFn: (row) => row.pod_ids.length,
        id: "pods",
        header: "Pods",
        cell: ({ row }) => <span className="tabular-nums">{row.original.pod_ids.length}</span>,
      },
      {
        accessorFn: (row) => row.task_ids.length,
        id: "tasks",
        header: "Tasks",
        cell: ({ row }) => <span className="tabular-nums">{row.original.task_ids.length}</span>,
      },
      {
        accessorKey: "description",
        header: "Summary",
        cell: ({ row }) => (
          <span className="line-clamp-2 text-muted-foreground">
            {metadataText(row.original, "summary") || row.original.description || "-"}
          </span>
        ),
      },
      {
        id: "actions",
        header: "Open",
        enableSorting: false,
        cell: ({ row }) => (
          <Link
            to={`/workstreams/${row.original.id}`}
            className="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-xs font-medium hover:bg-surface-muted"
          >
            Details
          </Link>
        ),
      },
    ],
    [],
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Runtime directory"
          title="Workstreams"
          description="Configured delivery workstreams with current rollup status and graph links."
        />

        <Toolbar>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search workstreams"
            className="min-w-64"
          />
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton
            refreshing={workstreams.isFetching}
            onClick={() => void workstreams.refetch()}
          />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-4">
          <KpiCard
            icon={<GitBranch className="h-4 w-4" />}
            label="Workstreams"
            value={workstreams.data?.length ?? "-"}
            detail={`${filteredWorkstreams.length} shown`}
            tone="info"
          />
          <KpiCard
            label="Projects"
            value={sum(workstreams.data, (item) => item.project_ids.length)}
            detail="linked projects"
          />
          <KpiCard
            label="Pods"
            value={sum(workstreams.data, (item) => item.pod_ids.length)}
            detail="assigned pods"
          />
          <KpiCard
            label="Tasks"
            value={sum(workstreams.data, (item) => item.task_ids.length)}
            detail="child tasks"
          />
        </section>

        <QueryState query={workstreams}>
          {() => (
            <DataTable
              data={filteredWorkstreams}
              columns={columns}
              emptyTitle="No workstreams found"
              emptyDescription="Adjust the search, date filter, or create workstreams in Admin Config."
            />
          )}
        </QueryState>
      </div>
    </main>
  );
}

function filterDirectoryItems(items: DirectoryItemResponse[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return items;
  return items.filter((item) =>
    [
      item.id,
      item.name,
      item.description ?? "",
      metadataText(item, "type"),
      metadataText(item, "phase"),
      metadataText(item, "owner_id"),
      metadataText(item, "summary"),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalized),
  );
}

function metadataText(item: DirectoryItemResponse, key: string) {
  const value = item.metadata[key];
  return typeof value === "string" ? value : "";
}

function metadataLabel(item: DirectoryItemResponse, key: string) {
  return metadataText(item, key) || key;
}

function sum(
  items: DirectoryItemResponse[] | undefined,
  selector: (item: DirectoryItemResponse) => number,
) {
  if (!items) return "-";
  return items.reduce((total, item) => total + selector(item), 0);
}
