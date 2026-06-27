import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Boxes } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

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

const todayIso = () => new Date().toISOString().slice(0, 10);

export function PodsPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const [query, setQuery] = useState("");
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });

  const filteredPods = useMemo(
    () => filterDirectoryItems(pods.data ?? [], query),
    [pods.data, query],
  );

  const columns = useMemo<DataTableColumn<DirectoryItemResponse>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Pod",
        cell: ({ row }) => (
          <div className="min-w-0">
            <Link
              className="font-medium text-foreground hover:text-primary"
              to={`/pods/${row.original.id}`}
            >
              {row.original.name}
            </Link>
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">{row.original.id}</div>
          </div>
        ),
      },
      {
        accessorKey: "rag",
        header: "RAG",
        cell: ({ row }) => <StatusBadge rag={row.original.rag} />,
      },
      {
        accessorFn: (row) => row.member_ids.length,
        id: "members",
        header: "Members",
        cell: ({ row }) => <span className="tabular-nums">{row.original.member_ids.length}</span>,
      },
      {
        accessorFn: (row) => row.project_ids.length,
        id: "projects",
        header: "Projects",
        cell: ({ row }) => <span className="tabular-nums">{row.original.project_ids.length}</span>,
      },
      {
        accessorKey: "description",
        header: "Description",
        cell: ({ row }) => (
          <span className="line-clamp-2 text-muted-foreground">
            {row.original.description ?? "-"}
          </span>
        ),
      },
      {
        id: "actions",
        header: "Open",
        enableSorting: false,
        cell: ({ row }) => (
          <Link
            to={`/pods/${row.original.id}`}
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
          title="Pods"
          description="Configured delivery pods with latest rollup status, membership, and project links."
        />

        <Toolbar>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search pods"
            className="min-w-64"
          />
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton refreshing={pods.isFetching} onClick={() => void pods.refetch()} />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            icon={<Boxes className="h-4 w-4" />}
            label="Pods"
            value={pods.data?.length ?? "-"}
            detail={`${filteredPods.length} shown`}
            tone="info"
          />
          <KpiCard
            label="Members"
            value={sum(pods.data, (pod) => pod.member_ids.length)}
            detail="linked memberships"
          />
          <KpiCard
            label="Projects"
            value={sum(pods.data, (pod) => pod.project_ids.length)}
            detail="pod-project links"
          />
        </section>

        <QueryState query={pods}>
          {() => (
            <LinkTableWrapper items={filteredPods}>
              <DataTable
                data={filteredPods}
                columns={columns}
                emptyTitle="No pods found"
                emptyDescription="Adjust the search, date filter, or create pods in Admin Config."
              />
            </LinkTableWrapper>
          )}
        </QueryState>
      </div>
    </main>
  );
}

function LinkTableWrapper({
  items,
  children,
}: {
  items: DirectoryItemResponse[];
  children: ReactNode;
}) {
  return (
    <div className="relative">
      {children}
      <div className="sr-only">
        {items.map((item) => (
          <Link key={item.id} to={`/pods/${item.id}`}>
            Open {item.name}
          </Link>
        ))}
      </div>
    </div>
  );
}

function filterDirectoryItems(items: DirectoryItemResponse[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return items;
  return items.filter((item) =>
    [item.id, item.name, item.code ?? "", item.description ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(normalized),
  );
}

function sum(
  items: DirectoryItemResponse[] | undefined,
  selector: (item: DirectoryItemResponse) => number,
) {
  if (!items) return "-";
  return items.reduce((total, item) => total + selector(item), 0);
}
