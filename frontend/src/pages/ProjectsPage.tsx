import { useQuery } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";
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

const todayIso = () => new Date().toISOString().slice(0, 10);

export function ProjectsPage() {
  const [asOf, setAsOf] = useState(todayIso);
  const [query, setQuery] = useState("");
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });

  const filteredProjects = useMemo(
    () => filterDirectoryItems(projects.data ?? [], query),
    [projects.data, query],
  );

  const columns = useMemo<DataTableColumn<DirectoryItemResponse>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Project",
        cell: ({ row }) => (
          <div className="min-w-0">
            <Link
              className="font-medium text-foreground hover:text-primary"
              to={`/projects/${row.original.id}`}
            >
              {row.original.name}
            </Link>
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">
              {row.original.code ?? row.original.id}
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
        accessorFn: (row) => row.pod_ids.length,
        id: "pods",
        header: "Pods",
        cell: ({ row }) => <span className="tabular-nums">{row.original.pod_ids.length}</span>,
      },
      {
        accessorFn: (row) => row.program_ids.length,
        id: "programs",
        header: "Programs",
        cell: ({ row }) => <span className="tabular-nums">{row.original.program_ids.length}</span>,
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
            to={`/projects/${row.original.id}`}
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
          title="Projects"
          description="Configured projects with current rollup status and delivery structure links."
        />

        <Toolbar>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search projects"
            className="min-w-64"
          />
          <AsOfControl value={asOf} onChange={setAsOf} />
          <RefreshButton refreshing={projects.isFetching} onClick={() => void projects.refetch()} />
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            icon={<FolderKanban className="h-4 w-4" />}
            label="Projects"
            value={projects.data?.length ?? "-"}
            detail={`${filteredProjects.length} shown`}
            tone="info"
          />
          <KpiCard
            label="Pods"
            value={sum(projects.data, (project) => project.pod_ids.length)}
            detail="project-pod links"
          />
          <KpiCard
            label="Programs"
            value={sum(projects.data, (project) => project.program_ids.length)}
            detail="program links"
          />
        </section>

        <QueryState query={projects}>
          {() => (
            <DataTable
              data={filteredProjects}
              columns={columns}
              emptyTitle="No projects found"
              emptyDescription="Adjust the search, date filter, or create projects in Admin Config."
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
