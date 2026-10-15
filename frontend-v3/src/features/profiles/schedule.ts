// Each person's check-in time and time zone, from a pod's board, pure. Type
// imports only, so `node --test` runs it as written.
import type { PodCheckinsResponse } from "../../api/schema";

/**
 * "Check-in 09:30 · Europe/Berlin" per member id, where the reader is sent
 * each person's schedule (`reads_schedule`: an admin, and the pod's own scrum
 * master); null where they are not, so nothing is drawn for them.
 */
export function scheduleOf(
  board: Pick<PodCheckinsResponse, "reads_schedule" | "developers"> | undefined,
): Map<string, string> | null {
  if (!board?.reads_schedule) return null;
  return new Map(
    board.developers.flatMap((person): [string, string][] => {
      if (!person.checkin_time) return [];
      const zone = person.timezone ? ` · ${person.timezone}` : "";
      return [[person.developer_id, `Check-in ${person.checkin_time}${zone}`]];
    }),
  );
}
