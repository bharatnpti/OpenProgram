import type { Rag, RollupFactorDto } from "../../api/schema";
import { ragSeverity } from "../../lib/status";

/** Factors that say the same thing, gathered so each reason is one line. */
export type Reason = {
  key: string;
  description: string;
  contributes: Rag;
  /** The factor kind: blocker, status, task, target_date or aggregate. */
  kind: string;
  sources: { id: string; kind: string; name: string }[];
};

/** What a panel knows about the reasons behind its colour. */
export type ReasonsState =
  | { status: "denied" }
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; reasons: Reason[] };

/**
 * A node's rollup reasons, worst first, green ones only when nothing is worse.
 * The rollup keeps one factor per source, so six people with no status are six
 * factors; they read as one reason naming the six. A source with no name is
 * shown by its id, never guessed.
 */
export function rollupReasons(
  factors: RollupFactorDto[],
  nameOf: (id: string) => string | undefined,
): Reason[] {
  const notGreen = factors.filter((factor) => factor.contributes !== "green");
  const ranked = [...(notGreen.length > 0 ? notGreen : factors)].sort(
    (a, b) => ragSeverity(b.contributes) - ragSeverity(a.contributes),
  );
  const reasons = new Map<string, Reason>();
  ranked.forEach((factor) => {
    const key = `${factor.contributes}:${factor.description}`;
    const source = {
      id: factor.source_ref.id,
      kind: factor.source_ref.kind.replace(/_/g, " "),
      name: nameOf(factor.source_ref.id) ?? factor.source_ref.id,
    };
    const reason = reasons.get(key);
    if (reason) {
      reason.sources.push(source);
    } else {
      reasons.set(key, {
        key,
        description: factor.description,
        contributes: factor.contributes,
        kind: factor.kind,
        sources: [source],
      });
    }
  });
  return [...reasons.values()];
}

/**
 * The one line under a node's name: what gives it its colour.
 *
 * It names the worst reason and who it comes from. A node turns red when two
 * blockers stack up even if neither is red alone, and says so rather than
 * naming an amber reason for a red chip. Silence is never green: no reasons
 * reads as "nothing recorded", never as on track.
 */
export function leadReason({
  noun,
  selfId,
  rag,
  state,
  deniedRole,
}: {
  noun: string;
  selfId: string;
  rag: Rag;
  state: ReasonsState;
  deniedRole: string;
}): string {
  if (state.status === "denied") {
    return rag === "unknown"
      ? `No status is recorded for this ${noun} yet.`
      : `The reasons behind this status need ${deniedRole}.`;
  }
  if (state.status === "failed") return "The reasons behind this status could not be loaded.";
  if (state.status === "loading") return "Loading the reasons…";
  const [top] = state.reasons;
  if (!top) {
    return rag === "unknown"
      ? `No status is recorded for this ${noun} yet.`
      : `No reasons are recorded for this ${noun} yet.`;
  }
  if (rag === "red" && top.contributes !== "red") {
    const blockers = state.reasons
      .filter((reason) => reason.kind === "blocker")
      .reduce((count, reason) => count + reason.sources.length, 0);
    return blockers > 1
      ? `${blockers} open blockers together make this ${noun} red.`
      : "Nothing here is red on its own; together it adds up to red.";
  }
  // Name who it comes from, unless that is this node or the reason names it.
  const others = top.sources
    .filter((source) => source.id !== selfId && !top.description.includes(source.name))
    .map((source) => source.name);
  const sentence = top.description.replace(/\.$/, "");
  return others.length > 0 ? `${sentence} (${joinNames(others)}).` : `${sentence}.`;
}

/**
 * Whether the reasons list says more than `leadReason` already does. A single
 * reason is the lead line itself, unless the lead had to sum blockers up.
 */
export function reasonsAddToLead(rag: Rag, reasons: Reason[]): boolean {
  if (reasons.length > 1) return true;
  const [only] = reasons;
  return only !== undefined && rag === "red" && only.contributes !== "red";
}

export function sourcesLabel(sources: Reason["sources"]): string {
  const names = joinNames(sources.map((source) => source.name));
  const kinds = new Set(sources.map((source) => source.kind));
  if (kinds.size > 1) return names;
  const kind = sources[0]?.kind ?? "";
  return sources.length === 1 ? `${kind} · ${names}` : `${sources.length} ${kind}s · ${names}`;
}

export function whyTitle(rag: Rag): string {
  return rag === "unknown" ? "Why it has no status" : `Why it's ${rag}`;
}

export function joinNames(names: string[]): string {
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}
