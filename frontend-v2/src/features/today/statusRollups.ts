import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "../../api/client";
import type { DirectoryItemResponse, StatusSource } from "../../api/schema";

/** The rollups the signed-in developer's own check-in feeds, nearest first. */
export interface StatusRollups {
  pods: DirectoryItemResponse[];
  projects: DirectoryItemResponse[];
  programs: DirectoryItemResponse[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  /** A load was refused outright, so there is nothing honest to show. */
  forbidden: boolean;
}

/**
 * Where this developer's check-in rolls up to, read from the directory.
 *
 * The rollup walks `contains` edges -- program > project > pod > developer --
 * so a check-in reaches the pods listing the developer as a member, the
 * projects containing those pods, and the programs containing those projects.
 * Workstreams are left out on purpose: a pod is only `assigned_to` one, and a
 * workstream's colour comes from its tasks, not from anyone's check-in.
 *
 * Every role may read the directory, so this works for a developer, who
 * cannot open pod or project detail.
 */
export function useStatusRollups(asOf: string): StatusRollups {
  const focus = useQuery({
    queryKey: ["persona", "focus", asOf],
    queryFn: () => apiClient.focus(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const programs = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
  });

  const queries = [focus, pods, projects, programs];
  const developerId = focus.data?.developer_id;
  const myPods = developerId
    ? (pods.data ?? []).filter((pod) => pod.member_ids.includes(developerId))
    : [];
  const projectIds = new Set(myPods.flatMap((pod) => pod.project_ids));
  const myProjects = (projects.data ?? []).filter((project) => projectIds.has(project.id));
  const programIds = new Set(myProjects.flatMap((project) => project.program_ids));
  const myPrograms = (programs.data ?? []).filter((program) => programIds.has(program.id));

  return {
    pods: myPods,
    projects: myProjects,
    programs: myPrograms,
    isPending: queries.some((query) => query.isPending),
    isError: queries.some((query) => query.isError),
    error: queries.find((query) => query.isError)?.error ?? null,
    forbidden: queries.some(
      (query) => query.error instanceof ApiError && query.error.status === 403,
    ),
  };
}

/**
 * One line on how the developer's pod reads this check-in, or null when there
 * is nothing to flag: confirmed for today, still loading, or no pod at all (no
 * claim about a rollup that doesn't exist).
 *
 * Mirrors the backend's `_checkin_state` (persona_views.py), which the scrum
 * master's pod board applies: only a confirmed or partial status dated today
 * counts as answered, anything inferred or carried forward is stale, and no
 * status at all is missing.
 */
export function checkinHint(
  source: StatusSource | undefined,
  statusAsOf: string | null,
  asOf: string,
  podNames: string[],
): string | null {
  if (source === undefined || podNames.length === 0) return null;
  const state = checkinState(source, statusAsOf, asOf);
  if (state === "confirmed") return null;
  const pods =
    podNames.length === 1
      ? podNames[0]
      : podNames.length === 2
        ? `${podNames[0]} and ${podNames[1]}`
        : `Your ${podNames.length} pods`;
  const shows = podNames.length === 1 ? "shows" : "show";
  if (state === "missing") {
    return `${pods} ${shows} your check-in as missing. Silence is never read as green.`;
  }
  return `${pods} ${shows} your check-in as ${state} until you confirm or correct it.`;
}

function checkinState(
  source: StatusSource,
  statusAsOf: string | null,
  asOf: string,
): "confirmed" | "partial" | "stale" | "missing" {
  if (statusAsOf === null || source === "unknown") return "missing";
  if (statusAsOf === asOf && source === "confirmed") return "confirmed";
  if (statusAsOf === asOf && source === "partial") return "partial";
  return "stale";
}
