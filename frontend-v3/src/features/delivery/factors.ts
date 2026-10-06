// Pure helpers for Delivery panels, type imports only so `node --test` can run them.
import type {
  DirectoryItemResponse,
  PodCheckinsResponse,
  Rag,
  RollupFactorDto,
} from "../../api/schema";
import { formatDate, formatDay } from "../../lib/format.ts";
import { ragSeverity } from "../../lib/status.ts";

type CheckinDeveloper = PodCheckinsResponse["developers"][number];

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

const FACTOR_WORDS: Record<string, string> = {
  aggregate: "rollup",
  target_date: "target date",
  no_pod: "in no pod",
};

const NODE_WORDS: Record<string, string> = {
  developer: "person",
  work_item: "work item",
};

export type ReasonRow = {
  key: string;
  contributes: Rag;
  description: string;
  /** What kind of reason, in words: "blocker", "rollup", "target date". */
  kind: string;
  /** Where it comes from: names the server gives, else the kind and id. */
  sources: string[];
};

/**
 * Every reason, worst first, with the same reason from several places (the
 * program's "No child status data is available." from each of its repos)
 * merged into one row that names each place.
 */
export function reasonRows(factors: RollupFactorDto[], names: Record<string, string>): ReasonRow[] {
  const rows = new Map<string, ReasonRow>();
  for (const factor of worstFirst(factors)) {
    const ref = factor.source_ref;
    const source = names[ref.id] ?? `${NODE_WORDS[ref.kind] ?? ref.kind} ${ref.id}`;
    const key = `${factor.contributes}|${factor.kind}|${factor.description}`;
    const row = rows.get(key);
    if (row) {
      if (!row.sources.includes(source)) row.sources.push(source);
    } else {
      rows.set(key, {
        key,
        contributes: factor.contributes,
        description: factor.description,
        kind: FACTOR_WORDS[factor.kind] ?? factor.kind.replace(/_/g, " "),
        sources: [source],
      });
    }
  }
  return [...rows.values()];
}

/** "a, b and 4 more": a few sources by name, then a count. */
export function sourcesLine(sources: string[], shown = 3): string {
  if (sources.length <= shown) return sources.join(", ");
  return `${sources.slice(0, shown).join(", ")} and ${sources.length - shown} more`;
}

/**
 * The facts a node's metadata holds, as label and value, in a fixed order.
 * Admins set type, phase and target date; the tracker and code links come
 * from set-up. Empty values are left out, so a node with none shows none.
 */
export function metadataFacts(metadata: DirectoryItemResponse["metadata"]): [string, string][] {
  const text = (value: unknown) =>
    typeof value === "string" && value.trim() ? value.trim() : null;
  const repos = text(metadata.github_repos)
    ?.split(/[\s,]+/)
    .filter(Boolean)
    .join(", ");
  const target = text(metadata.target_date);
  const facts: [string, string | null | undefined][] = [
    ["Type", text(metadata.type)],
    ["Phase", text(metadata.phase)],
    ["Target date", target ? formatDate(target) : null],
    ["Jira project", text(metadata.jira_project_key)],
    ["Repositories", repos],
  ];
  return facts.filter((pair): pair is [string, string] => Boolean(pair[1]));
}

/**
 * A member's check-in on the day shown, in words. "Stale" means the status
 * isn't from that day: an earlier day's answer, or one inferred from activity.
 */
export function checkinStateWords(developer: CheckinDeveloper): string {
  if (developer.state === "confirmed") return "confirmed";
  if (developer.state === "partial") return "partly answered";
  if (developer.state === "missing") return "no reply";
  if (developer.source === "inferred") return "inferred";
  return developer.status_as_of ? `from ${formatDay(developer.status_as_of)}` : "not confirmed";
}
