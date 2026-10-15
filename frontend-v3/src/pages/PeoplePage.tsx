import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { useOwnTree, usePods, usePrograms } from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { Panel, Row } from "../components/ui/Bits";
import { fullPodIds } from "../features/delivery/ownTree";
import { noPodTiles } from "../features/portfolio/heat";
import { useHeatmap } from "../features/portfolio/useHeatmap";
import { PodPeoplePanel } from "../features/profiles/PodPeople";
import { scheduleOf } from "../features/profiles/schedule";

/** The pick that lists the people in no pod instead of one pod's. */
const NO_POD = "none";

/**
 * People & roles (`/people`), the read side: who is in which pod, with which
 * role and profile, for the pods whose people the person reads (a manager or
 * admin every pod; a scrum master and a product owner the pods of their own
 * part whose panel is theirs). Profiles change where the server says
 * `can_edit` (an admin, the scrum master who runs the pod). One pod at a time
 * (`?pod=`); for a manager and an admin also the people in no pod
 * (`?pod=none`), whom no team's colour counts. Each person's time zone and
 * check-in time show for an admin and for the pod's own scrum master: the
 * server sends them only to those (`reads_schedule`).
 */
export function PeoplePage() {
  const { access, canReadPortfolio, canReadPodDetail } = useRole();
  const own = access.deliveryScope === "own";
  // A manager and an admin: the readers of every pod's people who read the heat map with names.
  const noPodOffered = canReadPortfolio && !access.hub.countsOnly;
  const pods = usePods();
  const tree = useOwnTree(own);
  const full = own && tree.data ? fullPodIds(tree.data) : null;
  const readable = [...(pods.data ?? [])]
    .filter((pod) => !own || full?.has(pod.id))
    .sort((a, b) => a.name.localeCompare(b.name));
  const [search, setSearch] = useSearchParams();
  const asked = search.get("pod");
  const noPod = noPodOffered && asked === NO_POD;
  const pod = noPod ? undefined : (readable.find((item) => item.id === asked) ?? readable[0]);
  const choices = readable.length + (noPodOffered ? 1 : 0);

  return (
    <div className="op-viz">
      <SectionHeader
        title="People & roles"
        meta="Who is in which pod, with which role and profile. A profile is what someone brings to a pod, never a rating."
      />
      <PanelState
        isLoading={pods.isLoading || (own && tree.isLoading)}
        error={pods.error ?? (own ? tree.error : null)}
        isEmpty={readable.length === 0 && !noPodOffered}
        emptyText="You read no pod's people yet."
      >
        {choices > 1 ? (
          <div className="mb-5">
            <label htmlFor="people-pod" className="mb-1 block text-[12px] font-bold">
              Pod
            </label>
            <select
              id="people-pod"
              className="h-10 max-w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] font-bold text-ink"
              value={noPod ? NO_POD : (pod?.id ?? "")}
              onChange={(event) =>
                setSearch(
                  (current) => {
                    const next = new URLSearchParams(current);
                    next.set("pod", event.target.value);
                    return next;
                  },
                  { replace: true },
                )
              }
            >
              {readable.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
              {noPodOffered ? <option value={NO_POD}>Not in any pod</option> : null}
            </select>
          </div>
        ) : null}
        {noPod ? (
          <NotInAnyPod />
        ) : pod ? (
          <PodPeople key={pod.id} podId={pod.id} readsBoard={canReadPodDetail} />
        ) : null}
      </PanelState>
    </div>
  );
}

/**
 * A pod's people, with each one's time zone and check-in time where the
 * server gives them (`reads_schedule`), read from the pod's board under the
 * key the pod pages use.
 */
function PodPeople({ podId, readsBoard }: { podId: string; readsBoard: boolean }) {
  const board = useQuery({
    queryKey: ["pod", podId, "checkins"],
    queryFn: () => apiClient.podCheckins(podId),
    enabled: readsBoard,
  });
  return <PodPeoplePanel podId={podId} schedule={scheduleOf(board.data)} />;
}

/**
 * The people in no pod, project or programme (someone not yet placed, an
 * executive): each with their own check-in, which no team's colour counts.
 * The heat map names them, so this reads it; an admin places them in a pod
 * under Admin › Entities.
 */
function NotInAnyPod() {
  const { canManageConfig } = useRole();
  const programs = usePrograms();
  const first = programs.data?.[0]?.id ?? "";
  const heatmap = useHeatmap(first);
  const people = noPodTiles(heatmap.data?.cells, Number.POSITIVE_INFINITY).tiles;
  return (
    <Panel
      title="Not in any pod"
      note={
        heatmap.data ? `${people.length} ${people.length === 1 ? "person" : "people"}` : undefined
      }
    >
      <PanelState
        isLoading={programs.isLoading || heatmap.isLoading}
        error={programs.error ?? heatmap.error}
        onRetry={() => void heatmap.refetch()}
        isEmpty={people.length === 0}
        emptyText="Everyone is in a pod."
      >
        <ul>
          {people.map((person) => (
            <Row
              key={person.id}
              title={person.name}
              meta={[`Check-in: ${person.state}`, person.reason].filter(Boolean).join(" · ")}
            />
          ))}
        </ul>
        {canManageConfig ? (
          <p className="mt-3 text-[13px]">
            <Link to="/admin?tab=entities" className="font-bold">
              Place someone in a pod: Admin › Entities →
            </Link>
          </p>
        ) : null}
      </PanelState>
    </Panel>
  );
}
