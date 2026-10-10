import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse } from "../../api/schema";
import { usePods, usePrograms, useProjects, useWorkstreams } from "../../app/directory";
import { deriveLinks, mergePodTasks, type EntityKind, type Links } from "./structure";

const LISTERS: Record<EntityKind, () => Promise<ConfigNodeResponse[]>> = {
  program: () => apiClient.configPrograms(),
  project: () => apiClient.configProjects(),
  pod: () => apiClient.configPods(),
  workstream: () => apiClient.configWorkstreams(),
  member: () => apiClient.configMembers(),
};

/** Every node of a kind, including a workstream nothing is in: the config API lists them all. */
export function useConfigList(kind: EntityKind) {
  return useQuery({ queryKey: ["config", "entities", kind], queryFn: LISTERS[kind] });
}

/**
 * Who is linked to whom, from the directory. The directory's workstream list leaves out a
 * workstream that holds no task (workstreams are optional), so each one the config list has and
 * the directory list lacks is read on its own: its links are still the admin's to see and change.
 */
export function useLinks() {
  const programs = usePrograms();
  const projects = useProjects();
  const pods = usePods();
  const listed = useWorkstreams();
  const configured = useConfigList("workstream");

  const inUse = useMemo(() => new Set((listed.data ?? []).map((ws) => ws.id)), [listed.data]);
  // Wait for both lists: before the directory's arrives, every workstream would look empty.
  const empty =
    listed.data && configured.data ? configured.data.filter((ws) => !inUse.has(ws.id)) : [];
  const direct = useQueries({
    queries: empty.map((ws) => ({
      queryKey: ["directory", "workstream", ws.id],
      queryFn: () => apiClient.workstream(ws.id),
    })),
  });

  // The lists decide whether the screen is ready. A workstream's own read never takes it down:
  // that workstream says it is being read, or could not be, and the rest stays where it is.
  const isLoading =
    programs.isLoading ||
    projects.isLoading ||
    pods.isLoading ||
    listed.isLoading ||
    configured.isLoading;
  const error =
    programs.error ?? projects.error ?? pods.error ?? listed.error ?? configured.error ?? null;
  const reading = new Set<string>();
  const unreadable = new Map<string, unknown>();
  empty.forEach((ws, index) => {
    const query = direct[index];
    if (query?.isLoading) reading.add(ws.id);
    // A workstream deleted a moment ago answers 404 until the list catches up: not a fault.
    else if (query?.error && (query.error as { status?: number }).status !== 404) {
      unreadable.set(ws.id, query.error);
    }
  });

  const links: Links | null =
    programs.data && projects.data && pods.data && listed.data && configured.data
      ? deriveLinks({
          programs: programs.data,
          projects: projects.data,
          pods: pods.data,
          workstreams: [...listed.data, ...direct.flatMap((query) => query.data ?? [])],
        })
      : null;
  return { links, inUse, isLoading, error, reading, unreadable };
}

/** Each pod's people with their role in it, read from the escalation picker (active members only). */
export function usePodRoles(podIds: string[]) {
  const queries = useQueries({
    queries: podIds.map((podId) => ({
      queryKey: ["config", "pod-escalation-candidates", podId],
      queryFn: () => apiClient.podEscalationCandidates(podId),
    })),
  });
  const roles: Record<string, Record<string, string | null>> = {};
  podIds.forEach((podId, index) => {
    const byMember: Record<string, string | null> = {};
    for (const candidate of queries[index]?.data ?? []) {
      if (candidate.in_pod) byMember[candidate.member_id] = candidate.pod_role;
    }
    roles[podId] = byMember;
  });
  return roles;
}

/**
 * The tasks the pods hold, for suggestions and for who has them; a task two
 * pods hold is one, with the owners and blockers of both (`mergePodTasks`).
 */
export function useKnownTasks(podIds: string[]) {
  const queries = useQueries({
    queries: podIds.map((podId) => ({
      queryKey: ["pod", podId, "tasks"],
      queryFn: () => apiClient.podTasks(podId),
    })),
  });
  const tasks = mergePodTasks(queries.map((query) => query.data?.tasks ?? []));
  return { tasks, isLoading: queries.some((query) => query.isLoading) };
}

/**
 * Called after any change to the structure. Every screen reads it (Today, Delivery, Signals,
 * Reports), so all cached reads go stale, not only the Admin ones.
 */
export function useStructureChanged() {
  const queryClient = useQueryClient();
  return useCallback(() => queryClient.invalidateQueries(), [queryClient]);
}
