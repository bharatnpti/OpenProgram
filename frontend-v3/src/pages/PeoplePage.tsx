import { useSearchParams } from "react-router-dom";

import { useOwnTree, usePods } from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { fullPodIds } from "../features/delivery/ownTree";
import { PodPeoplePanel } from "../features/profiles/PodPeople";

/**
 * People & roles (`/people`), the read side: who is in which pod, with which
 * role and profile, for the pods whose people the person reads (a manager or
 * admin every pod; a scrum master and a product owner the pods of their own
 * part whose panel is theirs). Profiles change where the server says `can_edit`
 * (an admin, the scrum master who runs the pod). One pod at a time
 * (`?pod=`). Time zones and check-in times stay in Admin › Directory.
 */
export function PeoplePage() {
  const { access } = useRole();
  const own = access.deliveryScope === "own";
  const pods = usePods();
  const tree = useOwnTree(own);
  const full = own && tree.data ? fullPodIds(tree.data) : null;
  const readable = [...(pods.data ?? [])]
    .filter((pod) => !own || full?.has(pod.id))
    .sort((a, b) => a.name.localeCompare(b.name));
  const [search, setSearch] = useSearchParams();
  const asked = search.get("pod");
  const pod = readable.find((item) => item.id === asked) ?? readable[0];
  return (
    <>
      <SectionHeader
        title="People & roles"
        meta="Who is in which pod, with which role and profile. A profile is what someone brings to a pod, never a rating."
      />
      <PanelState
        isLoading={pods.isLoading || (own && tree.isLoading)}
        error={pods.error ?? (own ? tree.error : null)}
        isEmpty={readable.length === 0}
        emptyText="You read no pod's people yet."
      >
        {readable.length > 1 ? (
          <div className="mb-5">
            <label htmlFor="people-pod" className="sr-only">
              Pod
            </label>
            <select
              id="people-pod"
              className="h-10 max-w-full rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
              value={pod?.id ?? ""}
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
            </select>
          </div>
        ) : null}
        {pod ? <PodPeoplePanel key={pod.id} podId={pod.id} /> : null}
      </PanelState>
    </>
  );
}
