import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "../api/client";
import type { DirectoryItemResponse } from "../api/schema";
import { useRole } from "./role";

/*
 * The directory (programs, projects, workstreams, pods) is readable by every
 * role and is what most screens start from: selectors, heat rows, the delivery
 * navigator, and "where your check-in rolls up". One query key per kind, so
 * every screen shares the same request.
 */

export function usePrograms() {
  return useQuery({ queryKey: ["directory", "programs"], queryFn: () => apiClient.programs() });
}

export function useProjects() {
  return useQuery({ queryKey: ["directory", "projects"], queryFn: () => apiClient.projects() });
}

export function useWorkstreams() {
  return useQuery({
    queryKey: ["directory", "workstreams"],
    queryFn: () => apiClient.workstreams(),
  });
}

export function usePods() {
  return useQuery({ queryKey: ["directory", "pods"], queryFn: () => apiClient.pods() });
}

/** The first program: the root every portfolio read hangs off. */
export function useProgram() {
  const programs = usePrograms();
  return { query: programs, program: programs.data?.[0] ?? null };
}

/**
 * Display names for member ids, from the people the directory names on pods,
 * projects and workstreams, plus the acting-as roster on a local tenant. An id
 * nobody names stays an id: never a guessed name.
 */
export function useNames(): (id: string | null | undefined) => string {
  const { people } = useRole();
  const pods = usePods();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const map = useMemo(() => {
    const names = new Map<string, string>();
    const items = [...(pods.data ?? []), ...(projects.data ?? []), ...(workstreams.data ?? [])];
    for (const item of items) {
      for (const person of item.people) {
        if (person.name) {
          names.set(person.id, person.name);
          if (person.member_id) names.set(person.member_id, person.name);
        }
      }
    }
    for (const person of people) names.set(person.id, person.name);
    return names;
  }, [people, pods.data, projects.data, workstreams.data]);
  return (id) => (id ? (map.get(id) ?? id) : "—");
}

/** Pods the given member belongs to; every pod when nobody is known (real sign-in). */
export function podsOf(pods: DirectoryItemResponse[], memberId: string | null | undefined) {
  if (!memberId) return pods;
  const mine = pods.filter((pod) => pod.member_ids.includes(memberId));
  return mine.length > 0 ? mine : pods;
}

/** Projects the given pods work on; every project when the pods name none. */
export function projectsOf(projects: DirectoryItemResponse[], pods: DirectoryItemResponse[]) {
  const ids = new Set(pods.flatMap((pod) => pod.project_ids));
  const mine = projects.filter((project) => ids.has(project.id));
  return mine.length > 0 ? mine : projects;
}
