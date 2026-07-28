import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Pill } from "../../components/ui/Pill";
import type { CrossPersonRequestResponse, CrossPersonRequestStatus } from "../../api/schema";

const COLUMNS: {
  status: CrossPersonRequestStatus;
  label: string;
  dot: string;
}[] = [
  { status: "open", label: "Open", dot: "var(--op-magenta)" },
  { status: "acknowledged", label: "Acknowledged", dot: "var(--op-info)" },
  { status: "needs_resolution", label: "Needs resolution", dot: "var(--op-amber-deep)" },
];

export function RequestsBoard() {
  const queryClient = useQueryClient();

  const requests = useQuery({
    queryKey: ["persona", "cross-person-requests", null],
    queryFn: () => apiClient.portfolioCrossPersonRequests(null),
  });

  const updateStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: CrossPersonRequestStatus }) =>
      apiClient.updateCrossPersonRequestStatus(id, { status }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "cross-person-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-cross-person-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "portfolio-feed"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Update failed"),
  });

  const byStatus = (status: CrossPersonRequestStatus) =>
    (requests.data?.requests ?? []).filter((request) => request.status === status);

  return (
    <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
      {COLUMNS.map((column) => {
        const items = byStatus(column.status);
        return (
          <div key={column.status} className="rounded-3xl bg-grey-fill p-4">
            <div className="flex items-center gap-2.5 px-1.5 pb-3">
              <span className="h-3 w-3 rounded-full" style={{ backgroundColor: column.dot }} />
              <h3 className="text-[15px] font-bold">{column.label}</h3>
              <span
                className="ml-auto flex h-6 min-w-6 items-center justify-center rounded-full px-2 text-[12px] font-bold text-white"
                style={{ backgroundColor: column.dot }}
              >
                {items.length}
              </span>
            </div>
            <div className="flex flex-col gap-2.5">
              {items.map((request) => (
                <RequestCard
                  key={request.source_correlation_id}
                  request={request}
                  onAcknowledge={() =>
                    updateStatus.mutate({ id: request.source_correlation_id, status: "acknowledged" })
                  }
                  onResolve={() =>
                    updateStatus.mutate({ id: request.source_correlation_id, status: "resolved" })
                  }
                />
              ))}
              {items.length === 0 ? (
                <p className="px-1.5 text-[13px] text-grey-secondary">Nothing here.</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RequestCard({
  request,
  onAcknowledge,
  onResolve,
}: {
  request: CrossPersonRequestResponse;
  onAcknowledge: () => void;
  onResolve: () => void;
}) {
  const counterpart = request.counterpart_display_name ?? request.raw_name ?? "unmatched";
  return (
    <div className="rounded-2xl border border-grey-border bg-white p-4">
      <div className="text-xs font-bold uppercase tracking-wide text-magenta">{request.kind}</div>
      <div className="mt-1.5 text-[15px] font-bold">{request.note}</div>
      <div className="mt-1.5 text-[13px] text-grey-secondary">
        {request.requester_id} → {counterpart}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Pill variant="ghost" size="sm" onClick={onAcknowledge}>
          Acknowledge
        </Pill>
        <button
          type="button"
          onClick={onResolve}
          className="text-[13px] font-bold text-grey-secondary hover:text-ink"
        >
          Resolve
        </button>
      </div>
    </div>
  );
}
