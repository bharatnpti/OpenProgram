import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useNames } from "../../app/directory";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { daysBetween } from "../../lib/format";

const KIND_WORDS: Record<string, string> = {
  review: "a review",
  input: "input",
  dependency: "work they depend on",
};

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
        <Link to="/coordination" className="font-bold">
          All requests
        </Link>
      }
    >
      <PanelState
        needs="anyone with a member record"
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
                title={`${names(request.requester_id)} asks for ${KIND_WORDS[request.kind] ?? request.kind}`}
                meta={`${request.note || "No note"} · ${request.status === "acknowledged" ? "acknowledged, " : ""}waiting ${age === 0 ? "since today" : `${age} ${age === 1 ? "day" : "days"}`}`}
                right={request.kind.toUpperCase()}
              />
            );
          })}
        </ul>
      </PanelState>
    </Panel>
  );
}
