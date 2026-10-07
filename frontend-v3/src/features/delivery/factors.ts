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

/**
 * The blockers a node's reasons hold, one per blocker, counted as the backend
 * counts them: by id, and by wording when a reason has none.
 */
export function distinctBlockers(factors: RollupFactorDto[]): RollupFactorDto[] {
  const seen = new Map<string, RollupFactorDto>();
  for (const factor of factors) {
    if (factor.kind !== "blocker") continue;
    const key = factor.blocker_id || factor.description;
    if (!seen.has(key)) seen.set(key, factor);
  }
  return [...seen.values()];
}

/**
 * Whether a red is the rule "more than one different blocker" and no reason of
 * its own. The backend turns a node red when it holds two blockers
 * (rollup_service `_rag_from_factors` and `_aggregate_rag`) though each one
 * alone is amber, so no factor is red; the worst factor is then some other
 * amber one, and naming it as the cause of a red misleads.
 */
export function redFromBlockerCount(
  rag: Rag | null | undefined,
  factors: RollupFactorDto[],
): boolean {
  return (
    rag === "red" &&
    !factors.some((factor) => factor.contributes === "red") &&
    distinctBlockers(factors).length > 1
  );
}

const BLOCKERS_NAMED = 3;

/** "2 different blockers are open at once: Waiting on review; Waiting for a reviewer." */
function blockerCountText(factors: RollupFactorDto[]): string {
  const blockers = distinctBlockers(factors);
  const words = blockers.map((factor) =>
    factor.description.replace(/^Blocker:\s*/i, "").replace(/\.$/, ""),
  );
  const named = words.slice(0, BLOCKERS_NAMED).join("; ");
  const more = words.length > BLOCKERS_NAMED ? ` and ${words.length - BLOCKERS_NAMED} more` : "";
  return `${blockers.length} different blockers are open at once: ${named}${more}.`;
}

export type SetBy = {
  text: string;
  /** The reason the text is, so its source can be named; null for the blocker-count rule. */
  factor: RollupFactorDto | null;
};

/**
 * What sets a node's colour, in one sentence: its worst reason, or for a red
 * that no single reason makes, the rule that did.
 */
export function setBy(rag: Rag | null | undefined, factors: RollupFactorDto[]): SetBy | null {
  if (redFromBlockerCount(rag, factors)) return { text: blockerCountText(factors), factor: null };
  const worst = worstFirst(factors)[0];
  return worst ? { text: worst.description, factor: worst } : null;
}

/**
 * "Payments Pod: 2 blockers past 7 days (from Kai Thompson)". Pass the node's
 * colour so a red made by the blocker count says so (see `setBy`).
 */
export function reasonLine(
  factors: RollupFactorDto[],
  names: Record<string, string>,
  rag?: Rag | null,
): string | null {
  const by = setBy(rag, factors);
  if (!by) return null;
  const who = by.factor ? names[by.factor.source_ref.id] : undefined;
  return who ? `${by.text} (from ${who})` : by.text;
}

const FACTOR_WORDS: Record<string, string> = {
  aggregate: "rollup",
  target_date: "target date",
  no_pod: "in no pod",
  // The reasons themselves read "Signals disagree: CHK-11 has a merge request open 5 days".
  // Not "drift": Signals' Drift list counts only what was reported against what happened,
  // while this also counts a merge request open too long that its owner never mentioned.
  drift: "signals disagree",
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

/**
 * The count above the reasons: "2 reasons", "1 reason from 6 places". Merged
 * rows say how many places they came from, in the singular too.
 */
export function reasonsNote(rows: number, places: number): string {
  const reasons = `${rows} ${rows === 1 ? "reason" : "reasons"}`;
  return rows === places
    ? reasons
    : `${reasons} from ${places} ${places === 1 ? "place" : "places"}`;
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
