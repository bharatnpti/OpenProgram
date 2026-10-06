import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";

/*
 * One hook per endpoint the Overall view reads, keyed so two sections asking
 * for the same data share one request. `enabled` skips a read the viewing role
 * is known not to have, so the panel can say who opens it without a 403.
 */

export const PROJECT_PROGRESS_READERS = "a product owner, manager, executive or admin";
export const AGGREGATE_READERS = "a scrum master, product owner, manager, executive or admin";
export const CONFIG_READERS = "an admin";

export function useRequirements(projectId: string) {
  const { canReadProjectProgress } = useRole();
  return {
    locked: !canReadProjectProgress,
    query: useQuery({
      queryKey: ["requirements", projectId],
      queryFn: () => apiClient.projectRequirements(projectId),
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

/** Gates open to project-progress readers and to everyone who may edit gates (all but exec). */
export function useGateBoard(projectId: string) {
  return {
    locked: false,
    query: useQuery({
      queryKey: ["gates", projectId],
      queryFn: () => apiClient.gateBoard(projectId),
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
