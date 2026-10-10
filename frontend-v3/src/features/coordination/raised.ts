// No runtime imports: this module runs under `node --test` as written.

/**
 * Whether an ask the viewer raised is still theirs to watch. One whose name was
 * not matched to anyone ("needs resolution") waits on the requester's own
 * answer, so it stays on their list; only a resolved or dismissed one leaves.
 */
export function raisedStillOpen(status: string): boolean {
  return status === "open" || status === "acknowledged" || status === "needs_resolution";
}

/** Whether an ask is still waiting on the person it asks: open, or acknowledged and not yet resolved. */
export function waitingStillOpen(status: string): boolean {
  return status === "open" || status === "acknowledged";
}

type Pod = { id: string; member_ids: string[]; project_ids: string[] };

/**
 * The people whose asks a board scope covers: the members of the given pods,
 * or of every pod working on the given projects. A request carries no pod or
 * project of its own, so the board goes by who is on it.
 */
export function scopePeople(
  pods: Pod[],
  scope: { podIds?: string[]; projectIds?: string[] },
): Set<string> {
  const podIds = new Set(scope.podIds ?? []);
  const projectIds = new Set(scope.projectIds ?? []);
  const people = new Set<string>();
  for (const pod of pods) {
    if (podIds.has(pod.id) || pod.project_ids.some((id) => projectIds.has(id))) {
      for (const member of pod.member_ids) people.add(member);
    }
  }
  return people;
}

/** The requests where the person asking or the person asked is one of `people`. */
export function requestsAmong<R extends { requester_id: string; counterpart_id: string | null }>(
  requests: R[],
  people: Set<string>,
): R[] {
  return requests.filter(
    (request) =>
      people.has(request.requester_id) ||
      (request.counterpart_id !== null && people.has(request.counterpart_id)),
  );
}
