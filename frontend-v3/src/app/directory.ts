import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { GraphTreeDto, PortfolioHeatmapResponse } from "../api/schema";
import {
  fromDirectory,
  fromHeatmapCells,
  fromNodes,
  fromRoster,
  indexNames,
  nameLookup,
  type NameEntry,
  type NameOf,
} from "./names";
import { useRole } from "./role";
import { chooseProgram, rankPrograms, reportProjects } from "./scope";

/*
 * The directory (programs, projects, workstreams, pods) is readable by every
 * role and is what most screens start from: selectors, heat rows, the delivery
 * navigator, and "where your check-in rolls up". One query key per kind, so
 * every screen shares the same request.
 */

export {
  podsOf,
  podsOfPerson,
  projectsOf,
  projectsOfPerson,
  programsOfProjects,
  pickerProjects,
  rankProjects,
  reportProjects,
} from "./scope";

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
 * The program a portfolio screen shows, when a tenant has several: the one in
 * the URL (`?program=`, set by every pick), else the worst. `programs` is ranked worst first, for
 * a picker whose chips carry each program's colour.
 */
export function useProgramChoice() {
  const programs = usePrograms();
  const [search, setSearch] = useSearchParams();
  const ranked = useMemo(() => rankPrograms(programs.data ?? []), [programs.data]);
  const program = chooseProgram(ranked, search.get("program"));
  const choose = useCallback(
    (id: string) =>
      setSearch(
        (current) => {
          // Other parameters (the day being viewed) are the shell's; leave them.
          // A pick is always kept, the worst program too: without it the screen
          // would follow the ranking and switch program when the colours move.
          const next = new URLSearchParams(current);
          next.set("program", id);
          return next;
        },
        { replace: true },
      ),
    [setSearch],
  );
  return { query: programs, programs: ranked, program, choose };
}

/**
 * The member id the console speaks as: the person picked in the header on a
 * local tenant, else the signed-in subject, which the backend reads as the
 * member (`/me/status` is keyed on it). Null when nobody is known.
 */
export function useMemberId(): string | null {
  const { actingAs, user } = useRole();
  return actingAs?.id ?? user?.subject ?? null;
}

/**
 * The projects Reports lists for this person, one rule for the Reports home and
 * for the project picker inside a project's reports: a developer's are those of
 * their own pods, everyone else's are all of them (`reportProjects`). `listed`
 * is empty until what the rule needs has been read, so a developer never sees
 * every project flash by before their own; `directory` is every project, for
 * naming one the list leaves out.
 */
export function useReportProjects() {
  const { canReadAggregate } = useRole();
  const memberId = useMemberId();
  const projects = useProjects();
  const pods = usePods();
  const narrowed = !canReadAggregate;
  const ready = !projects.isLoading && !(narrowed && pods.isLoading);
  const shown = ready
    ? reportProjects(projects.data ?? [], pods.data ?? [], memberId, narrowed)
    : { projects: [], own: false };
  return {
    listed: shown.projects,
    /** The list is the person's own pods' projects, not everything. */
    own: shown.own,
    narrowed,
    directory: projects.data ?? [],
    isLoading: !ready,
    error: projects.error ?? (narrowed ? pods.error : null),
    retry: () => void projects.refetch(),
  };
}

const heatmapOf = (results: UseQueryResult<PortfolioHeatmapResponse>[]) =>
  results.map((result) => result.data);
const treeOf = (results: UseQueryResult<GraphTreeDto>[]) => results.map((result) => result.data);

/**
 * Display names for member ids, from what the viewing role may read:
 *
 * - everyone: the people the directory names on pods, projects and workstreams,
 *   and the person themselves;
 * - a local tenant: the acting-as roster;
 * - an admin: every member (`/config/members`), by node id and by chat id;
 * - a manager or executive: the people on the heat map, which names every member
 *   in a team and everyone in no team;
 * - a scrum master or product owner: the people in each program's team graph.
 *
 * A developer reads none of the lists, so a requester outside their own pods
 * stays unnamed for them. An id nobody names stays an id, and `names.known(id)`
 * says whether anything did: never a guessed name.
 */
export function useNames(): NameOf {
  const { people, user, canManageConfig, canReadPortfolio, canReadAggregate } = useRole();
  const pods = usePods();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const programs = usePrograms();
  const programIds = (programs.data ?? []).map((program) => program.id);

  const members = useQuery({
    queryKey: ["config", "members"],
    queryFn: () => apiClient.configMembers(),
    enabled: canManageConfig,
  });
  const heat = useQueries({
    queries: programIds.map((id) => ({
      queryKey: ["portfolio", "heatmap", id],
      queryFn: () => apiClient.portfolioHeatmap(undefined, id),
      enabled: canReadPortfolio && !canManageConfig,
    })),
    combine: heatmapOf,
  });
  const trees = useQueries({
    queries: programIds.map((id) => ({
      queryKey: ["graph", "tree", id],
      queryFn: () => apiClient.programTree(id),
      enabled: canReadAggregate && !canReadPortfolio,
    })),
    combine: treeOf,
  });

  const index = useMemo(() => {
    const self: NameEntry[] = user?.name ? [{ id: user.subject, name: user.name }] : [];
    return indexNames(
      self,
      fromRoster(people),
      fromNodes(members.data),
      fromDirectory([...(pods.data ?? []), ...(projects.data ?? []), ...(workstreams.data ?? [])]),
      heat.flatMap((data) => fromHeatmapCells(data?.cells)),
      trees.flatMap((data) => fromNodes(data?.nodes)),
    );
  }, [heat, members.data, people, pods.data, projects.data, trees, user, workstreams.data]);

  return useMemo(() => nameLookup(index), [index]);
}
