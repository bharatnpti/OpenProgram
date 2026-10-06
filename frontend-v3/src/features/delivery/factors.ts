// Pure helpers for Delivery panels, type imports only so `node --test` can run them.
import type { DirectoryItemResponse, RollupFactorDto } from "../../api/schema";
import { ragSeverity } from "../../lib/status.ts";

export const KINDS = ["program", "project", "workstream", "pod"] as const;
export type Kind = (typeof KINDS)[number];
export type Finder = (kind: Kind, id: string) => DirectoryItemResponse | undefined;

/** Factors worst first, so the reason a reader sees first is the one that sets the colour. */
export function worstFirst(factors: RollupFactorDto[]): RollupFactorDto[] {
  return [...factors].sort((a, b) => ragSeverity(b.contributes) - ragSeverity(a.contributes));
}

/** "Payments Pod: 2 blockers past 7 days (from Kai Thompson)". */
export function reasonLine(
  factors: RollupFactorDto[],
  names: Record<string, string>,
): string | null {
  const worst = worstFirst(factors)[0];
  if (!worst) return null;
  const who = names[worst.source_ref.id];
  return who ? `${worst.description} (from ${who})` : worst.description;
}
