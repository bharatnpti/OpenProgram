import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { CheckCheck, ClipboardCheck, Handshake, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { apiClient } from "../api/client";
import type {
  CrossPersonRequestResponse,
  CrossPersonRequestStatus,
  CrossPersonRequestsResponse,
} from "../api/schema";
import { DataTable } from "../components/ops/DataTable";
import {
  DataPanel,
  KpiCard,
  PageHeader,
  QueryState,
  RefreshButton,
  Toolbar,
} from "../components/ops/primitives";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";

const STATUS_FILTERS: Array<{ label: string; value: CrossPersonRequestStatus | null }> = [
  { label: "Open", value: "open" },
  { label: "Acknowledged", value: "acknowledged" },
  { label: "Needs resolution", value: "needs_resolution" },
  { label: "All active", value: null },
];

export function CrossPersonRequestsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<CrossPersonRequestStatus | null>("open");
  const query = useQuery({
    queryKey: ["persona", "cross-person-requests", status],
    queryFn: () => apiClient.portfolioCrossPersonRequests(status),
  });

  const updateStatus = useMutation({
    mutationFn: (input: { id: string; status: CrossPersonRequestStatus }) =>
      apiClient.updateCrossPersonRequestStatus(input.id, { status: input.status }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "cross-person-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-cross-person-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "portfolio-feed"] });
    },
  });

  const rows = query.data?.requests ?? [];
  const columns = useMemo<ColumnDef<CrossPersonRequestResponse>[]>(
    () => [
      {
        accessorKey: "kind",
        header: "Kind",
        cell: ({ row }) => (
          <div className="flex flex-col gap-1">
            <span className="font-medium capitalize">{row.original.kind}</span>
            <RequestStatusBadge status={row.original.status} />
          </div>
        ),
      },
      {
        accessorKey: "note",
        header: "Request",
        cell: ({ row }) => (
          <div className="min-w-64">
            <div className="text-sm">{row.original.note}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              from {row.original.requester_id}
              {row.original.counterpart_id ? ` to ${row.original.counterpart_id}` : ""}
            </div>
          </div>
        ),
      },
      {
        accessorKey: "counterpart_display_name",
        header: "Counterpart",
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <span>{row.original.counterpart_display_name ?? row.original.raw_name ?? "-"}</span>
            <span className="text-xs text-muted-foreground">
              {row.original.counterpart_email ?? row.original.counterpart_id ?? ""}
            </span>
          </div>
        ),
      },
      {
        accessorKey: "updated_at",
        header: "Updated",
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {new Date(row.original.updated_at).toLocaleString()}
          </span>
        ),
      },
      {
        id: "actions",
        header: "Actions",
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={row.original.status === "acknowledged" || updateStatus.isPending}
              onClick={() => updateStatus.mutate({ id: row.original.id, status: "acknowledged" })}
            >
              <ClipboardCheck className="h-3.5 w-3.5" />
              Ack
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={row.original.status === "resolved" || updateStatus.isPending}
              onClick={() => updateStatus.mutate({ id: row.original.id, status: "resolved" })}
            >
              <CheckCheck className="h-3.5 w-3.5" />
              Resolve
            </Button>
          </div>
        ),
      },
    ],
    [updateStatus],
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow="Coordination"
          title="Cross-Person Requests"
          description="Open dependency, review, and input requests extracted from developer check-ins."
          actions={
            <RefreshButton refreshing={query.isFetching} onClick={() => void query.refetch()} />
          }
        />

        <Toolbar>
          <div className="flex flex-wrap gap-2">
            {STATUS_FILTERS.map((item) => (
              <Button
                key={item.label}
                type="button"
                size="sm"
                variant={status === item.value ? "primary" : "outline"}
                onClick={() => setStatus(item.value)}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </Toolbar>

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            icon={<Handshake className="h-4 w-4" />}
            label="Visible requests"
            value={rows.length}
            detail={status ?? "active"}
            tone="info"
          />
          <KpiCard
            icon={<ClipboardCheck className="h-4 w-4" />}
            label="Acknowledged"
            value={rows.filter((request) => request.status === "acknowledged").length}
            detail="counterpart has responded"
            tone="success"
          />
          <KpiCard
            icon={<RefreshCw className="h-4 w-4" />}
            label="Needs resolution"
            value={rows.filter((request) => request.status === "needs_resolution").length}
            detail="person match was ambiguous"
            tone="warning"
          />
        </section>

        <DataPanel title="Requests" description="Counterpart lifecycle and review state.">
          <QueryState query={query} loadingRows={6}>
            {(data: CrossPersonRequestsResponse) => (
              <DataTable
                data={data.requests}
                columns={columns}
                emptyTitle="No cross-person requests"
                emptyDescription="No requests match the current filter."
              />
            )}
          </QueryState>
          {updateStatus.isError && (
            <div className="mt-3 rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
              {(updateStatus.error as Error).message}
            </div>
          )}
        </DataPanel>
      </div>
    </main>
  );
}

function RequestStatusBadge({ status }: { status: CrossPersonRequestStatus }) {
  const tone =
    status === "resolved"
      ? "success"
      : status === "needs_resolution"
        ? "warning"
        : status === "dismissed"
          ? "neutral"
          : "info";
  return <Badge tone={tone}>{status.replace("_", " ")}</Badge>;
}
