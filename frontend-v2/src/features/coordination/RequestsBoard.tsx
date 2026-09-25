import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
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
  const { canReadAggregate } = useRole();

  // A developer has no aggregate scope, so the portfolio endpoint 403s for
  // them. Showing their own requests is both what they are allowed to see and
  // what they actually need -- and it keeps this board consistent with the
  // "Waiting on you" panel on Today.
  const requests = useQuery({
    queryKey: ["persona", "cross-person-requests", canReadAggregate ? "portfolio" : "mine"],
    queryFn: () =>
      canReadAggregate
        ? apiClient.portfolioCrossPersonRequests(null)
        : apiClient.myCrossPersonRequests(),
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

  if (requests.isError) {
    return (
      <p className="rounded-3xl bg-grey-fill px-5 py-6 text-[14px] text-grey-secondary">
        Requests could not be loaded:{" "}
        {requests.error instanceof Error ? requests.error.message : "unknown error"}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {canReadAggregate ? null : (
        <p className="text-[13px] text-grey-secondary">
          Showing requests you raised or that are waiting on you. The portfolio-wide board needs a
          team or executive role.
        </p>
      )}
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
                      updateStatus.mutate({
                        id: request.source_correlation_id,
                        status: "acknowledged",
                      })
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
  const { people } = useRole();
  // A request stores the requester as an id; the counterpart carries a resolved
  // display name. Look the requester up in the directory so both read as people.
  const requester =
    people.find((person) => person.id === request.requester_id)?.name ?? request.requester_id;
  const counterpart = request.counterpart_display_name ?? request.raw_name ?? "unmatched";
  return (
    <div className="rounded-2xl border border-grey-border bg-white p-4">
      <div className="text-xs font-bold uppercase tracking-wide text-magenta">{request.kind}</div>
      <div className="mt-1.5 text-[15px] font-bold">{request.note}</div>
      <div className="mt-1.5 text-[13px] text-grey-secondary">
        {requester} → {counterpart}
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
