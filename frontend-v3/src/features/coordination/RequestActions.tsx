import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { CrossPersonRequestResponse, CrossPersonRequestStatus } from "../../api/schema";
import { useMemberId } from "../../app/directory";
import { useReadOnly } from "../../app/viewingDate";
import { Pill } from "../../components/ui/Pill";
import { actionError } from "../../lib/errors";
import { requestKindLabel } from "../../lib/words";

/**
 * Acknowledge and Resolve, for the people on a request only.
 *
 * The backend's rule, and it refuses anyone else with a 403 whatever role they
 * read (an admin too): only the person asked acknowledges a request, and only
 * while it is open; they or the requester resolve it. Each change records who
 * made it. Nothing is drawn for anyone else, and no line says who may act.
 */
export function RequestActions({ request }: { request: CrossPersonRequestResponse }) {
  const memberId = useMemberId();
  const { readOnly, reason } = useReadOnly();
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (status: CrossPersonRequestStatus) =>
      apiClient.updateCrossPersonRequestStatus(request.id, { status }),
    onSuccess: (_r, status) => {
      toast.success(status === "resolved" ? "Request resolved." : "Request acknowledged.");
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
    onError: (e: unknown) => {
      toast.error(actionError(e, "update this request"));
      // Someone else may have closed or removed it: show the lists as they are now.
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
  });

  const raisedByViewer = Boolean(memberId && request.requester_id === memberId);
  const askedOfViewer = Boolean(memberId && request.counterpart_id === memberId);
  const live =
    request.status === "open" ||
    request.status === "acknowledged" ||
    request.status === "needs_resolution";
  const canAcknowledge = request.status === "open" && askedOfViewer;
  const canResolve = askedOfViewer || raisedByViewer;
  if (!live || !canResolve) return null;
  const what = request.note || requestKindLabel(request.kind);
  return (
    <div className="mt-2 flex gap-1.5">
      {canAcknowledge ? (
        <Pill
          size="sm"
          variant="ghost"
          disabled={update.isPending || readOnly}
          title={reason ?? undefined}
          aria-label={`Acknowledge: ${what}`}
          onClick={() => update.mutate("acknowledged")}
        >
          Acknowledge
        </Pill>
      ) : null}
      <Pill
        size="sm"
        variant="dark"
        disabled={update.isPending || readOnly}
        title={reason ?? undefined}
        aria-label={`Resolve: ${what}`}
        onClick={() => update.mutate("resolved")}
      >
        Resolve
      </Pill>
    </div>
  );
}
