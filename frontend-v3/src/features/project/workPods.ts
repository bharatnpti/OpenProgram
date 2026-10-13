// The pods the Project page's Work section lists, and whether each opens. Type
// imports only, so `node --test` runs it as written.
import type { DirectoryItemResponse } from "../../api/schema";
import type { OwnNode } from "../delivery/ownTree";

/** A pod the page lists: `opens` is false for one the person reads by name only. */
export type WorkPod = {
  id: string;
  name: string;
  rag: DirectoryItemResponse["rag"];
  opens: boolean;
};

/**
 * The pods a person reads on the page: under the `own` scope, theirs from their
 * own tree as the server lists them (a neighbouring pod a name only, with no
 * colour); else the directory's pods of the project, which open for a role
 * with Pods.
 */
export function workPods(
  project: Pick<DirectoryItemResponse, "pod_ids"> | undefined,
  directoryPods: Pick<DirectoryItemResponse, "id" | "name" | "rag">[],
  own: Pick<OwnNode, "id" | "name" | "rag" | "opens">[] | null,
  podsOffered: boolean,
): WorkPod[] {
  if (own) {
    return own.map((pod) => ({
      id: pod.id,
      name: pod.name,
      rag: pod.opens ? pod.rag : null,
      opens: pod.opens,
    }));
  }
  if (!project) return [];
  return directoryPods
    .filter((pod) => project.pod_ids.includes(pod.id))
    .map((pod) => ({ id: pod.id, name: pod.name, rag: pod.rag, opens: podsOffered }));
}
