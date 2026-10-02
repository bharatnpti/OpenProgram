import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { Card } from "../../components/ui/Card";

/** The cross-person requests waiting on whoever is being acted as.
 *
 * Every role is a person other people ask things of, so this belongs on each
 * persona's Today rather than only the developer's: a request raised *to* a
 * product owner or scrum master was invisible on the screen they open each
 * morning, reachable only by going to Coordination.
 */
export function WaitingOnYou({ animateDelay }: { animateDelay?: number }) {
  const navigate = useNavigate();
  const { people } = useRole();
  const { isPast } = useViewingDate();

  const myRequests = useQuery({
    queryKey: ["persona", "my-cross-person-requests"],
    queryFn: () => apiClient.myCrossPersonRequests(),
  });

  const requests = myRequests.data?.requests ?? [];

  return (
    <Card variant="grey" padding="p-0" animateDelay={animateDelay}>
      <div className="flex items-center justify-between px-5 pt-5 pb-2">
        <h2 className="text-[18px] font-bold">Waiting on you</h2>
        <button
          type="button"
          onClick={() => navigate("/coordination")}
          className="text-[14px] font-bold text-magenta"
        >
          All requests
        </button>
      </div>
      {/* Requests have no as_of, so a past day still shows today's inbox. */}
      {isPast ? (
        <p className="px-5 pb-1 text-[13px] text-grey-secondary">Requests show current state.</p>
      ) : null}
      {requests.map((request) => (
        // The request's own id: one check-in reply can mint several requests
        // that all share a source correlation id.
        <div key={request.id} className="px-5 py-3.5">
          <div className="text-[15px] font-bold">{request.note}</div>
          <div className="mt-0.5 text-[13px] text-grey-secondary">
            {/* These are requests where *you* are the counterpart, so the person
                to name is the requester -- not the counterpart display name,
                which is your own. */}
            from {requesterName(request.requester_id, people)}
          </div>
        </div>
      ))}
      {myRequests.isError ? (
        <div className="px-5 py-6 text-sm text-grey-secondary">
          Requests could not be loaded:{" "}
          {myRequests.error instanceof Error ? myRequests.error.message : "unknown error"}
        </div>
      ) : null}
      {myRequests.data && requests.length === 0 ? (
        <div className="px-5 py-6 text-sm text-grey-secondary">Nothing waiting on you.</div>
      ) : null}
    </Card>
  );
}

function requesterName(requesterId: string, people: { id: string; name: string }[]): string {
  return people.find((person) => person.id === requesterId)?.name ?? requesterId;
}
