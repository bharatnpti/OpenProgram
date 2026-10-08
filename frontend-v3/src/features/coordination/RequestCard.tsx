import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { CrossPersonRequestResponse, CrossPersonRequestStatus } from "../../api/schema";
import { useMemberId, useNames } from "../../app/directory";
import { useReadOnly } from "../../app/viewingDate";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { actionError } from "../../lib/errors";
import { daysBetween } from "../../lib/format";
import { daysLabel, deliveryNote, requestKindLabel, requestStatusLabel } from "../../lib/words";

/**
 * One request, the same card on Today's Your asks and on Coordination's board.
 * The person asking and the person asked are named (you, when it is the
 * viewer); the board's column already says where it stands, the personal lists
 * say it on the card (`showStatus`).
 *
 * The backend's rule, and it refuses anyone else with a 403 whatever role they
 * read (an admin too): only the person asked acknowledges a request, and only
 * while it is open; they or the requester resolve it. Each change records who
 * made it. The buttons show only where the viewer is one of those people; a
 * board reader who is not sees the request, and no line about who may act.
 */
export function RequestCard({
  request,
  showStatus = false,
}: {
  request: CrossPersonRequestResponse;
  showStatus?: boolean;
}) {
  const names = useNames();
  const memberId = useMemberId();
  const { readOnly, reason } = useReadOnly();
  const queryClient = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const age = daysBetween(request.created_at, today);
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
  const requester = raisedByViewer ? "You" : names.or(request.requester_id, "Someone");
  const counterpart = askedOfViewer
    ? "you"
    : (request.counterpart_display_name ?? request.raw_name ?? "someone not matched yet");
  const delivery = deliveryNote(request.delivery, raisedByViewer);
  const live = request.status === "open" || request.status === "acknowledged";
  const canAcknowledge = request.status === "open" && askedOfViewer;
  const canResolve = askedOfViewer || raisedByViewer;

  return (
    <li className="rounded-2xl border border-grey-border bg-white p-3">
      <p className="text-[13px] font-bold">
        {requester} → {counterpart}
      </p>
      <p className="mt-0.5 text-[13px] text-grey-body">{request.note || "No note."}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-grey-secondary">
        <RagChip tone="neutral" className="h-5 px-2 text-[11px]">
          {requestKindLabel(request.kind)}
        </RagChip>
        {showStatus ? (
          <RagChip
            tone={request.status === "acknowledged" ? "success" : "warning"}
            className="h-5 px-2 text-[11px]"
          >
            {requestStatusLabel(request.status)}
          </RagChip>
        ) : null}
        <span>{age <= 0 ? "raised today" : `waiting ${daysLabel(age)}`}</span>
        {delivery ? (
          <span className={delivery.bad ? "font-bold text-rag-red" : ""}>· {delivery.text}</span>
        ) : null}
      </div>
      {request.status === "needs_resolution" ? (
        <p className="mt-2 text-[12px] text-grey-secondary">
          The name wasn't matched to anyone. OpenProgram asked the requester who was meant; it stays
          here until they answer.
        </p>
      ) : null}
      {(live || request.status === "needs_resolution") && canResolve ? (
        <div className="mt-2 flex gap-1.5">
          {canAcknowledge ? (
            <Pill
              size="sm"
              variant="ghost"
              disabled={update.isPending || readOnly}
              title={reason ?? undefined}
              aria-label={`Acknowledge: ${request.note || requestKindLabel(request.kind)}`}
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
            aria-label={`Resolve: ${request.note || requestKindLabel(request.kind)}`}
            onClick={() => update.mutate("resolved")}
          >
            Resolve
          </Pill>
        </div>
      ) : null}
    </li>
  );
}
