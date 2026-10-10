import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

import { apiClient } from "../../api/client";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { RequestCard } from "../coordination/RequestCard";
import { raisedStillOpen, waitingStillOpen } from "../coordination/raised";

/**
 * Every Today's asks between people: what waits on you, and what you asked of
 * others and where it has got to, each with its buttons (Acknowledge while it
 * is open, Resolve for either party). It used to live on Coordination, where a
 * developer had nothing else; a link to Coordination opens it here (`#asks`).
 */
export function YourAsks() {
  const waiting = useQuery({
    queryKey: ["requests", "mine", "waiting"],
    queryFn: () => apiClient.myCrossPersonRequests("waiting"),
  });
  const raised = useQuery({
    queryKey: ["requests", "mine", "raised"],
    queryFn: () => apiClient.myCrossPersonRequests("raised"),
  });
  const waitingOn = (waiting.data?.requests ?? []).filter((r) => waitingStillOpen(r.status));
  const asked = (raised.data?.requests ?? []).filter((r) => raisedStillOpen(r.status));

  // A link to Coordination from a role without it lands here: bring the panel into
  // view once both lists are in, so it does not jump while they load.
  const { hash } = useLocation();
  const panel = useRef<HTMLDivElement>(null);
  const loaded = waiting.isSuccess && raised.isSuccess;
  useEffect(() => {
    if (hash === "#asks" && loaded) panel.current?.scrollIntoView?.({ block: "start" });
  }, [hash, loaded]);

  return (
    <div id="asks" ref={panel} className="min-w-0 scroll-mt-36">
      <Panel
        title="Your asks"
        variant="grey"
        note={
          loaded ? `${waitingOn.length} waiting on you · ${asked.length} raised by you` : undefined
        }
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <AskList
            title="Waiting on you"
            read={waiting}
            items={waitingOn}
            emptyText="Nothing waiting on you."
          />
          <AskList
            title="Raised by you"
            read={raised}
            items={asked}
            emptyText="You have no open asks of others."
          />
        </div>
      </Panel>
    </div>
  );
}

function AskList({
  title,
  read,
  items,
  emptyText,
}: {
  title: string;
  read: { isLoading: boolean; error: unknown; refetch: () => unknown };
  items: Parameters<typeof RequestCard>[0]["request"][];
  emptyText: string;
}) {
  return (
    <div className="min-w-0">
      <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
        {title}
      </h3>
      <PanelState
        isLoading={read.isLoading}
        error={read.error}
        onRetry={() => void read.refetch()}
        isEmpty={items.length === 0}
        emptyText={emptyText}
      >
        <ul className="grid gap-2">
          {items.map((request) => (
            <RequestCard key={request.id} request={request} showStatus />
          ))}
        </ul>
      </PanelState>
    </div>
  );
}
