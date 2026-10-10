import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "../../api/client";
import { stampLabel } from "./adminWords";

/** One query for the members, shared by every admin tab. Admins only. */
export const MEMBERS_KEY = ["config", "members"] as const;

export function useMembers() {
  return useQuery({ queryKey: MEMBERS_KEY, queryFn: () => apiClient.configMembers() });
}

/**
 * Member names by id, from the admin member list. An id nobody names stays an
 * id: never a guessed name.
 */
export function useMemberNames(): (id: string | null | undefined) => string {
  const members = useMembers();
  const names = useMemo(
    () => new Map((members.data ?? []).map((member) => [member.id, member.name])),
    [members.data],
  );
  return (id) => (id ? (names.get(id) ?? id) : "—");
}

/** "Saved Wed 7 Oct 00:07 by Asha Rao.", or null when it was never saved. */
export function savedLine(
  updatedAt: string | null | undefined,
  updatedBy: string | null | undefined,
  nameOf: (id: string | null | undefined) => string,
): string | null {
  if (!updatedAt) return null;
  const when = stampLabel(updatedAt);
  return updatedBy ? `Saved ${when} by ${nameOf(updatedBy)}.` : `Saved ${when}.`;
}
