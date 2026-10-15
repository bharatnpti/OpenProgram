import type { CrossPersonRequestResponse } from "../../api/schema";
import { useMemberId, useNames } from "../../app/directory";
import { useToday } from "../../app/viewingDate";
import { RagChip } from "../../components/ui/RagChip";
import { daysBetween } from "../../lib/format";
import { daysLabel, deliveryNote, requestKindLabel, requestStatusLabel } from "../../lib/words";
import { RequestActions } from "./RequestActions";

/**
 * One request as a card, on Today's Your asks. The person asking and the person
 * asked are named (you, when it is the viewer); the personal lists say where it
 * stands on the card (`showStatus`). Acknowledge and Resolve are drawn only for
 * the people on it (`RequestActions`). The Requests page lists them as a table.
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
  const today = useToday();
  const age = daysBetween(request.created_at, today);

  const raisedByViewer = Boolean(memberId && request.requester_id === memberId);
  const askedOfViewer = Boolean(memberId && request.counterpart_id === memberId);
  const requester = raisedByViewer ? "You" : names.or(request.requester_id, "Someone");
  const counterpart = askedOfViewer
    ? "you"
    : (request.counterpart_display_name ?? request.raw_name ?? "someone not matched yet");
  const delivery = deliveryNote(request.delivery, raisedByViewer);

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
      <RequestActions request={request} />
    </li>
  );
}
