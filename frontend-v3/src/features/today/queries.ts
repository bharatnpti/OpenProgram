import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, Rag } from "../../api/schema";
import { podsOfPerson, useMemberId, usePods } from "../../app/directory";
import { useRole } from "../../app/role";
import { defaultProject, worstFirst, type ProjectOption } from "../project/projectChoice";
import { useProjectOptions } from "../project/useProjectOptions";

/** The person's own Jira changes; one cache entry, read again after every answer. */
export const JIRA_CHANGES_KEY = ["me", "jira-changes"] as const;

/** The changes waiting for the person's yes: a check-in's proposals, suggestions, due dates. */
export function useMyJiraChanges(enabled = true) {
  return useQuery({
    queryKey: JIRA_CHANGES_KEY,
    queryFn: () => apiClient.myJiraChanges(),
    enabled,
  });
}

/** Today's counts over the person's part; one read for both count cards. */
export function useTodayCounts(enabled: boolean) {
  return useQuery({
    queryKey: ["me", "today-counts"],
    queryFn: () => apiClient.myTodayCounts(),
    enabled,
  });
}

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/** What Today leads with, per role. */
export type TodaySubject =
  | { kind: "project"; projectId: string; name: string; scope: "portfolio" | "own" }
  | { kind: "pod"; pod: DirectoryItemResponse }
  | { kind: "self" }
  | { kind: "none"; loading: boolean };

/**
 * The first block's subject: for an executive, a manager and an admin the
 * worst project of the portfolio; for a product owner the worst of their own
 * (else the worst of all); for a scrum master the worst pod they run; for a
 * developer their own check-in. Worst by today's colour, as every switcher
 * ranks: red, amber, unknown (silence is no clean bill of health), green.
 */
export function useTodaySubject(): TodaySubject {
  const { role } = useRole();
  const memberId = useMemberId();
  const options = useProjectOptions();
  const pods = usePods();
  if (role === "dev") return { kind: "self" };
  if (role === "sm") {
    if (pods.isLoading) return { kind: "none", loading: true };
    const { pods: mine, own } = podsOfPerson(pods.data ?? [], memberId);
    const worst = own
      ? [...mine].sort(
          (a, b) =>
            SEVERITY[b.rag ?? "unknown"] - SEVERITY[a.rag ?? "unknown"] ||
            a.name.localeCompare(b.name),
        )[0]
      : undefined;
    return worst ? { kind: "pod", pod: worst } : { kind: "none", loading: false };
  }
  if (options.loading) return { kind: "none", loading: true };
  const pick =
    role === "po"
      ? defaultProject(options.options, [])
      : ([...options.options].sort(worstFirst)[0]?.id ?? null);
  const project = options.options.find((option: ProjectOption) => option.id === pick);
  if (!project) return { kind: "none", loading: false };
  return {
    kind: "project",
    projectId: project.id,
    name: project.name,
    scope: role === "po" && project.mine ? "own" : "portfolio",
  };
}
