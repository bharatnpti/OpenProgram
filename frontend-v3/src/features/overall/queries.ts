import { useQueries, useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useReportAccess } from "../reports/useReportAccess";

/*
 * One hook per endpoint the Overall view reads, keyed so two sections asking
 * for the same data share one request. `enabled` skips a read the viewing role
 * is known not to have, so the panel can say who opens it without a 403. A
 * release scope is part of the key: the whole project and each release are
 * cached apart.
 */

export const PROJECT_PROGRESS_READERS = "a product owner, manager, executive or admin";
export const AGGREGATE_READERS = "a scrum master, product owner, manager, executive or admin";
export const CONFIG_READERS = "an admin";

/** How many days of daily snapshots the requirements read asks for. */
export const REQUIREMENT_DAYS = 30;

export function useRequirements(projectId: string, releaseId?: string) {
  const { canReadProjectProgress } = useRole();
  return {
    locked: !canReadProjectProgress,
    query: useQuery({
      queryKey: ["requirements", projectId, releaseId ?? ""],
      queryFn: () =>
        apiClient.projectRequirements(projectId, undefined, REQUIREMENT_DAYS, releaseId),
      enabled: canReadProjectProgress && projectId !== "",
    }),
  };
}

export function useDelivery(projectId: string) {
  const { canReadProjectProgress } = useRole();
  return {
    locked: !canReadProjectProgress,
    query: useQuery({
      queryKey: ["delivery", projectId],
      queryFn: () => apiClient.projectDelivery(projectId),
      enabled: canReadProjectProgress && projectId !== "",
    }),
  };
}

/** The project's releases: read with its progress, so a scrum master or developer gets none. */
export function useReleases(projectId: string) {
  const { readProjectProgress } = useReportAccess();
  return {
    locked: !readProjectProgress,
    query: useQuery({
      queryKey: ["releases", projectId],
      queryFn: () => apiClient.releases(projectId),
      enabled: readProjectProgress && projectId !== "",
    }),
  };
}

/**
 * Each pod's part of its projects, for a scrum master who cannot read the
 * project's own forecast. `can_set_dates` says which pods are theirs.
 */
export function usePodDeliveries(podIds: string[]) {
  const { readPodDelivery } = useReportAccess();
  return useQueries({
    queries: podIds.map((podId) => ({
      queryKey: ["pod-delivery", podId],
      queryFn: () => apiClient.podDelivery(podId),
      enabled: readPodDelivery,
    })),
  });
}

/** Gates open to project-progress readers and to everyone who may edit gates (all but exec). */
export function useGateBoard(projectId: string, releaseId?: string) {
  return {
    locked: false,
    query: useQuery({
      queryKey: ["gates", projectId, releaseId ?? ""],
      queryFn: () => apiClient.gateBoard(projectId, undefined, releaseId),
      enabled: projectId !== "",
    }),
  };
}

export function useRisks(projectId: string) {
  const { canReadAggregate } = useRole();
  return {
    locked: !canReadAggregate,
    query: useQuery({
      queryKey: ["risks", projectId],
      queryFn: () => apiClient.projectRisks(projectId),
      enabled: canReadAggregate && projectId !== "",
    }),
  };
}

export function useEscalation(projectId: string) {
  const { canManageConfig } = useRole();
  return {
    locked: !canManageConfig,
    query: useQuery({
      queryKey: ["escalation", projectId],
      queryFn: () => apiClient.projectEscalation(projectId),
      enabled: canManageConfig && projectId !== "",
    }),
  };
}
