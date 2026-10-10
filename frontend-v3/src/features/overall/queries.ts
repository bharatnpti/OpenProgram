import { useQueries, useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { readsReadiness } from "../../app/access";
import { useOwnTree } from "../../app/directory";
import { sameOnEveryDay } from "../../app/queryCache";
import { useRole } from "../../app/role";
import { useReportAccess } from "../reports/useReportAccess";

/*
 * One hook per endpoint the Overall view reads, keyed so two sections asking
 * for the same data share one request. `enabled` skips a read the viewing role
 * is known not to have (`readable` is false), and the section is then not
 * drawn at all. A release scope is part of the key: the whole project and each
 * release are cached apart.
 */

/** How many days of the forecast's own history "How the date moved" asks for. */
export const SLIP_DAYS = 30;

/**
 * The requirements read leaves its days to the server: 30, or the forecast's
 * window when the tenant's minimum needs more, so the flow can reach that
 * minimum whenever the forecast can (`timeline_days` says which).
 */
export function useRequirements(projectId: string, releaseId?: string) {
  const { canReadProjectProgress } = useRole();
  return {
    readable: canReadProjectProgress,
    query: useQuery({
      queryKey: ["requirements", projectId, releaseId ?? ""],
      queryFn: () => apiClient.projectRequirements(projectId, undefined, undefined, releaseId),
      enabled: canReadProjectProgress && projectId !== "",
    }),
  };
}

/**
 * The project's delivery. `readable` says the reader may read this one project
 * where the role does not read every project's (a scrum master's own, from the
 * person's part of the delivery tree); left out, the role decides.
 */
export function useDelivery(projectId: string, readable?: boolean) {
  const { canReadProjectProgress } = useRole();
  const can = readable ?? canReadProjectProgress;
  return {
    readable: can,
    query: useQuery({
      queryKey: ["delivery", projectId],
      queryFn: () => apiClient.projectDelivery(projectId),
      enabled: can && projectId !== "",
    }),
  };
}

/**
 * The history forecast as it stood each day (O1), for the project or one
 * release: its own query, so the replay of a month of forecasts never holds up
 * the strip that reads the delivery. A date change does not change it (history
 * forecasts from the requirements, not the date), so nothing invalidates it.
 */
export function useForecastHistory(projectId: string, releaseId?: string) {
  const { canReadProjectProgress } = useRole();
  return {
    readable: canReadProjectProgress,
    query: useQuery({
      queryKey: ["delivery-history", projectId, releaseId ?? ""],
      queryFn: () => apiClient.projectForecastHistory(projectId, undefined, releaseId, SLIP_DAYS),
      enabled: canReadProjectProgress && projectId !== "",
    }),
  };
}

/** Several projects' delivery reads at once (a portfolio's), each the same query as `useDelivery`. */
export function useProjectDeliveries(projectIds: string[]) {
  const { canReadProjectProgress } = useRole();
  return useQueries({
    queries: projectIds.map((projectId) => ({
      queryKey: ["delivery", projectId],
      queryFn: () => apiClient.projectDelivery(projectId),
      enabled: canReadProjectProgress,
    })),
  });
}

/** The project's releases: read with its progress, so a scrum master or developer gets none. */
export function useReleases(projectId: string) {
  const { readProjectProgress } = useReportAccess();
  return {
    readable: readProjectProgress,
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
    readable: true,
    query: useQuery({
      queryKey: ["gates", projectId, releaseId ?? ""],
      queryFn: () => apiClient.gateBoard(projectId, undefined, releaseId),
      enabled: projectId !== "",
    }),
  };
}

/**
 * Whether this person reads the project's release readiness (app/access.ts
 * `readsReadiness`): a scrum master only a project one of their own pods works on,
 * known from their part of the tree, the one query Delivery and the palette read.
 * The roles that read every project's never ask for the tree here.
 */
export function useReadsReadiness(projectId: string): boolean {
  const { access } = useRole();
  const tree = useOwnTree(access.readiness.board && !access.readiness.everyProject);
  return readsReadiness(access, projectId, tree.data);
}

/**
 * Release readiness: read as it stands now whatever day is viewed (it is not kept
 * per day), by the roles that decide on a release, and only where the server
 * answers this person (`useReadsReadiness`). A release scope is its own entry.
 */
export function useReadiness(projectId: string, releaseId?: string) {
  const readable = useReadsReadiness(projectId);
  return {
    readable,
    query: useQuery({
      queryKey: ["readiness", projectId, releaseId ?? ""],
      queryFn: () => apiClient.projectReadiness(projectId, releaseId),
      enabled: readable && projectId !== "",
      ...sameOnEveryDay,
    }),
  };
}

export function useRisks(projectId: string) {
  const { canReadAggregate } = useRole();
  return {
    readable: canReadAggregate,
    query: useQuery({
      queryKey: ["risks", projectId],
      queryFn: () => apiClient.projectRisks(projectId),
      enabled: canReadAggregate && projectId !== "",
    }),
  };
}
