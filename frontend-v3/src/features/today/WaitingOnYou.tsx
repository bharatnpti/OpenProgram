import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useNames } from "../../app/directory";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { daysBetween } from "../../lib/format";
import { daysLabel, requestKindLabel, requestSentence } from "../../lib/words";

/** Open and acknowledged asks where this person is the one asked. */
export function WaitingOnYou() {
  const names = useNames();
  const requests = useQuery({
    queryKey: ["requests", "mine", "waiting"],
    queryFn: () => apiClient.myCrossPersonRequests("waiting"),
  });
  const today = new Date().toISOString().slice(0, 10);
  const open = (requests.data?.requests ?? []).filter(
    (request) => request.status === "open" || request.status === "acknowledged",
  );

  return (
    <Panel
      title="Waiting on you"
      variant="grey"
      note={
        <Link
          to="/coordination"
          className="font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
        >
          All requests
        </Link>
      }
    >
      <PanelState
        isLoading={requests.isLoading}
        error={requests.error}
        isEmpty={open.length === 0}
        emptyText="Nothing waiting on you."
      >
        <ul>
          {open.map((request) => {
            const age = daysBetween(request.created_at, today);
            return (
              <Row
                key={request.id}
                rag={request.status === "acknowledged" ? "green" : "amber"}
                title={requestSentence(names.or(request.requester_id, "Someone"), request.kind)}
                meta={`${request.note || "No note"} · ${request.status === "acknowledged" ? "acknowledged, " : ""}${age <= 0 ? "raised today" : `waiting ${daysLabel(age)}`}`}
                right={requestKindLabel(request.kind).toUpperCase()}
              />
            );
          })}
        </ul>
      </PanelState>
    </Panel>
  );
}
