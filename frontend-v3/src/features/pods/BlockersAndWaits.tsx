import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useNames } from "../../app/directory";
import { useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { formatDay } from "../../lib/format";
import { guessToday } from "../../lib/viewingDate";
import { countedElsewhere, requestKindLabel } from "../../lib/words";
import { requestsAmong } from "../coordination/raised";
import { waitedDays } from "../coordination/requestWords";

/**
 * Blockers and waits: the open blockers the pod's people reported, oldest
 * first, then the asks among the pod's people (one of them waiting on someone,
 * or someone waiting on one of them), the longest wait first. The asks only for
 * those who read the requests board; each one's trail is on Requests.
 */
export function BlockersAndWaits({
  podId,
  memberIds,
  waits,
}: {
  podId: string;
  memberIds: string[];
  /** The requests board is the reader's: show the asks among the pod's people. */
  waits: boolean;
}) {
  const names = useNames();
  const day = useShownDay() ?? guessToday();
  const blockers = useQuery({
    queryKey: ["pod", podId, "blockers"],
    queryFn: () => apiClient.podBlockers(podId),
  });
  const board = useQuery({
    queryKey: ["requests", "portfolio"],
    queryFn: () => apiClient.portfolioCrossPersonRequests(null),
    enabled: waits,
  });
  const open = [...(blockers.data?.blockers ?? [])].sort((a, b) => b.age_days - a.age_days);
  const asks = waits
    ? requestsAmong(
        (board.data?.requests ?? []).filter(
          (request) => request.status === "open" || request.status === "acknowledged",
        ),
        new Set(memberIds),
      ).sort((a, b) => a.created_at.localeCompare(b.created_at))
    : [];
  return (
    <Panel title="Blockers and waits" note="oldest first">
      <PanelState
        isLoading={blockers.isLoading || (waits && board.isLoading)}
        error={blockers.error ?? (waits ? board.error : null)}
        onRetry={() => {
          void blockers.refetch();
          if (waits) void board.refetch();
        }}
        isEmpty={open.length === 0 && asks.length === 0}
        emptyText={
          waits ? "No open blockers, and nobody here is waiting on anyone." : "No open blockers."
        }
      >
        <ul>
          {open.map((blocker) => (
            <Row
              key={blocker.id}
              rag={blocker.age_days >= 7 ? "red" : "amber"}
              accent={blocker.age_days >= 7 ? "red" : "amber"}
              title={blocker.description}
              meta={[
                "Blocker",
                blocker.owner_name,
                blocker.work_item_ref?.id,
                `since ${formatDay(blocker.first_seen_on)}`,
                // On a task this pod lists, but its colour is another pod's.
                countedElsewhere(blocker),
              ]
                .filter(Boolean)
                .join(" · ")}
              right={`${blocker.age_days}d`}
            />
          ))}
          {asks.map((request) => (
            <Row
              key={request.id}
              rag="unknown"
              title={`${names.or(request.requester_id, "Someone")} waits on ${
                request.counterpart_display_name ?? request.raw_name ?? "someone not matched yet"
              }`}
              meta={[requestKindLabel(request.kind), request.note].filter(Boolean).join(" · ")}
              right={`${waitedDays(request, day)}d`}
            />
          ))}
        </ul>
        {asks.length > 0 ? (
          <Link
            to="/requests"
            className="mt-2 inline-flex min-h-11 items-center text-[13px] font-bold text-ink underline underline-offset-2 sm:min-h-0"
          >
            Each ask's trail is on Requests →
          </Link>
        ) : null}
      </PanelState>
    </Panel>
  );
}
