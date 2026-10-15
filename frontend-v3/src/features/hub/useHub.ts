import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, apiClient } from "../../api/client";
import type { DirectoryItemResponse, PortfolioProjectDto } from "../../api/schema";
import { useMemberId, useOwnTree, usePods, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { formatTime } from "../../lib/format";
import { ownProjects } from "../delivery/ownTree";
import { useGateBoard, useReleases } from "../overall/queries";
import type { HubPlace } from "./hubPaths";
import { podReads } from "../pods/podLevel";
import { useScopeRead, useWarnings } from "../warnings/queries";
import {
  badgeScope,
  opensPodLevel,
  podChoices,
  reportsBadge,
  scopeBadgeAt,
  warningsBadge,
  type Badge,
  type PodChoice,
} from "./hubTabs";
import type { HubTab } from "./hubPaths";

/** As many projects as one bulk read gives: the switcher's verdict dots, the pod bar's pods. */
export const INDEX_LIMIT = 200;

/**
 * Every project the person reads, from the kept forecast rows, worst first: one
 * bulk read shared by the switcher, the header's pill and the pod bar. Not a
 * developer's (the server refuses them).
 */
export function usePortfolioIndex(enabled = true) {
  const { access } = useRole();
  return useQuery({
    queryKey: ["portfolio", "delivery", "index"],
    queryFn: () => apiClient.portfolioDelivery({ limit: INDEX_LIMIT }),
    enabled: enabled && access.hub.bulk,
    staleTime: 60_000,
  });
}

/**
 * One project's row of the bulk read: from the index, else, past the index's
 * end on a tenant with more projects than one read gives, asked for by name.
 */
export function usePortfolioRow(projectId: string, name: string | undefined) {
  const { access } = useRole();
  const index = usePortfolioIndex();
  const inIndex = index.data?.projects.find((project) => project.id === projectId);
  const beyond =
    access.hub.bulk &&
    !inIndex &&
    index.data !== undefined &&
    index.data.total > index.data.projects.length &&
    Boolean(name);
  const asked = useQuery({
    queryKey: ["portfolio", "delivery", "row", projectId],
    queryFn: () => apiClient.portfolioDelivery({ q: name, limit: 50 }),
    enabled: beyond,
  });
  const row: PortfolioProjectDto | null =
    inIndex ?? asked.data?.projects.find((project) => project.id === projectId) ?? null;
  return { row, isLoading: index.isLoading || asked.isLoading };
}

/** The project of the directory, and its name from the person's tree when the directory has none. */
export function useHubProject(projectId: string) {
  const projects = useProjects();
  const tree = useOwnTree();
  const project = projects.data?.find((item) => item.id === projectId);
  const own = tree.data ? ownProjects(tree.data).find((item) => item.id === projectId) : undefined;
  return {
    project,
    own,
    name: project?.name ?? own?.name ?? null,
    isLoading: projects.isLoading || tree.isLoading,
  };
}

/**
 * Whether a pod's own level opens for the person, by id: for every reader but
 * one who sees counts only (an executive), whose pods under five are counted
 * with their project: the server leaves them out of the bulk read and refuses
 * their pod-level reads, so no link, chip or tile offers them.
 */
export function usePodOpens(): (podId: string) => boolean {
  const { access } = useRole();
  const index = usePortfolioIndex(access.hub.podMinPeople > 0);
  const pods = usePods();
  const min = access.hub.podMinPeople;
  return useMemo(() => {
    if (min <= 0) return () => true;
    const listed = new Set(
      (index.data?.projects ?? []).flatMap((project) => project.pods.map((pod) => pod.id)),
    );
    const people = new Map((pods.data ?? []).map((pod) => [pod.id, pod.member_ids.length]));
    return (podId: string) =>
      opensPodLevel(min, { listed: listed.has(podId), people: people.get(podId) ?? null });
  }, [min, index.data, pods.data]);
}

/**
 * The pods the pod bar lists for one project, and which of them the person
 * may open. A reader of the bulk reads gets the pods the read gives this
 * project, with each one's verdict, and the project's other pods by name; a
 * counts-only reader only the former (their pods under five are counted with
 * the project). A developer gets the pods of their own tree that open (their
 * own), with no verdict.
 */
export function usePodChoices(projectId: string, name: string | undefined) {
  const { access } = useRole();
  const memberId = useMemberId();
  const pods = usePods();
  const tree = useOwnTree();
  const projects = useProjects();
  const { row, isLoading } = usePortfolioRow(projectId, name);
  const opens = usePodOpens();
  const choices = useMemo<PodChoice[]>(() => {
    const ownTree = tree.data ? ownProjects(tree.data) : [];
    const ownPods = new Set(
      ownTree.flatMap((project) => project.pods.filter((pod) => pod.own).map((pod) => pod.id)),
    );
    for (const pod of pods.data ?? []) {
      if (memberId && pod.member_ids.includes(memberId)) ownPods.add(pod.id);
    }
    if (!access.hub.bulk) {
      const listed = ownTree.find((project) => project.id === projectId)?.pods ?? [];
      return podChoices(
        null,
        listed.filter((pod) => pod.opens),
        ownPods,
        false,
      );
    }
    const project = projects.data?.find((item) => item.id === projectId);
    const directory = (pods.data ?? []).filter(
      (pod) => project?.pod_ids.includes(pod.id) || pod.project_ids.includes(projectId),
    );
    const rows = row ? row.pods.filter((pod) => opens(pod.id)) : null;
    return podChoices(rows, directory, ownPods, access.hub.countsOnly);
  }, [
    access.hub.bulk,
    access.hub.countsOnly,
    memberId,
    opens,
    pods.data,
    projectId,
    projects.data,
    row,
    tree.data,
  ]);
  return {
    choices,
    isLoading:
      pods.isLoading ||
      tree.isLoading ||
      (access.hub.bulk && isLoading) ||
      (!access.hub.bulk && !tree.data && !tree.error),
  };
}

/** A pod of the directory, by id. */
export function useDirectoryPod(podId: string): DirectoryItemResponse | undefined {
  return usePods().data?.find((pod) => pod.id === podId);
}

/**
 * What the person reads of a pod: `full`, its detail (check-ins, blockers,
 * tasks, why its colour: the readers of every pod's, and a scrum master's or a
 * developer's own pods by their tree); `ownDates`, its dates by their own tree
 * whatever the role. Without either, its dates as the role reads them.
 */
export function usePodAccess(podId: string) {
  const { access, canReadPodDetail } = useRole();
  const pods = usePods();
  const tree = useOwnTree();
  const pod = pods.data?.find((item) => item.id === podId);
  const node = tree.data?.pods.find((item) => item.id === podId);
  const own = access.deliveryScope === "own";
  return {
    pod,
    full: own ? node?.access === "panel" : canReadPodDetail,
    ownDates: own && node !== undefined && node.access !== "name",
    isLoading: pods.isLoading || (own && tree.isLoading),
  };
}

/**
 * The release a project's tabs cover (`?release=`), kept on every tab: the one
 * in the link, unless it is gone (Overall's rule; a reader who cannot list
 * releases learns it from the gate board). `onChange` writes the pick, and
 * drops the row picked, which belongs to the old scope.
 */
export function useReleaseChoice(projectId: string) {
  const [search, setSearch] = useSearchParams();
  const asked = search.get("release") ?? "";
  const releases = useReleases(projectId);
  const probe = useGateBoard(projectId, !releases.readable && asked ? asked : undefined);
  const goneForReader =
    !releases.readable && probe.query.error instanceof ApiError && probe.query.error.status === 404;
  const list = releases.query.data;
  const gone =
    asked !== "" && ((list && !list.some((r) => r.release_id === asked)) || goneForReader);
  const releaseId = gone ? "" : asked;
  const onChange = (next: string) =>
    setSearch(
      (current) => {
        const params = new URLSearchParams(current);
        params.delete("row");
        if (next) params.set("release", next);
        else params.delete("release");
        return params;
      },
      { replace: true },
    );
  return {
    asked,
    releaseId,
    gone: asked !== "" && !releaseId,
    readable: releases.readable,
    onChange,
  };
}

/**
 * Each tab's badge at a place, each from one bulk read narrowed to it: the open
 * warnings (`/portfolio/warnings?project_id=&pod_id=`), what joined after
 * planning and the open hygiene findings (`/portfolio/scope?project_id=&pod_id=`),
 * and when today's day report went out (`/reports?project_id=`). At a pod the
 * two reads are the pod's part, cached as its tabs read them, and made only for
 * a reader of that tab there.
 */
export function useHubBadges(place: HubPlace): Partial<Record<HubTab, Badge | null>> {
  const { access, lens } = useRole();
  const day = useShownDay();
  const { projectId, podId } = badgeScope(place);
  const bulk = access.hub.bulk;
  const atPod = podId !== null;
  const podAccess = usePodAccess(podId ?? "");
  const reads = podReads(lens, podAccess.full);
  const warnings = useQuery({
    queryKey: ["portfolio", "warnings", projectId ?? ""],
    queryFn: () => apiClient.portfolioWarnings({ projectId }),
    enabled: bulk && !atPod,
  });
  const scope = useQuery({
    queryKey: ["portfolio", "scope"],
    queryFn: () => apiClient.portfolioScope(),
    enabled: bulk && !atPod,
  });
  const podWarnings = useWarnings({ projectId, podId }, atPod && reads.warnings);
  const podScope = useScopeRead({ projectId, podId }, atPod && bulk && reads.scope);
  const reports = useQuery({
    queryKey: ["day-reports", "project", projectId ?? ""],
    queryFn: () => apiClient.dayReports(projectId ?? undefined),
    enabled: access.reports.daily && place.level === "project",
  });
  return {
    warnings: warningsBadge((atPod ? podWarnings : warnings).data?.total),
    scope: scopeBadgeAt(projectId, (atPod ? podScope : scope).data),
    reports: place.level === "project" && day ? reportsBadge(reports.data, day, formatTime) : null,
  };
}
