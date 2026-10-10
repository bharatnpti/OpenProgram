// Only type imports here: this module runs under `node --test` as written.
import type {
  PullRequestFlowItemDto,
  PullRequestFlowResponse,
  PullRequestFlowStageDto,
  RequestType,
  ReviewStage,
  TypeSource,
} from "../../api/schema";

/*
 * Flow through review, in words and numbers the chart draws from. The backend
 * times each stage from the requests' own history; this decides how a stage
 * reads ("16 h"), how thick the band is where work jams, and where each dot
 * sits at a moment of the animation. No React, no DOM.
 */

export type Percentile = "p50" | "p75";

export const STAGES: readonly ReviewStage[] = [
  "coding",
  "awaiting_review",
  "in_review",
  "awaiting_merge",
];

export const STAGE_LABELS: Record<ReviewStage, string> = {
  coding: "Coding",
  awaiting_review: "Awaiting review",
  in_review: "In review",
  awaiting_merge: "Awaiting merge",
};

/** What each stage measures, from which timestamps: said once, under the chart. */
export const STAGE_DEFINITIONS: Record<ReviewStage, string> = {
  coding: "first commit to ready for review (to opened, if it was never a draft)",
  awaiting_review: "ready to the first note or approval by someone other than the author",
  in_review: "that first review to the last approval (or the last review note)",
  awaiting_merge: "that approval to the merge",
};

/**
 * The types in their fixed order. The order is the colour order: slot 1 to 8
 * of the validated categorical palette, and grey for unclassified. A type keeps
 * its colour whatever is filtered or counted (the colour follows the type).
 */
export const TYPES: readonly { type: RequestType; label: string; slot: number | null }[] = [
  { type: "feature", label: "Feature", slot: 1 },
  { type: "dependency_update", label: "Dependency update", slot: 2 },
  { type: "bug_fix", label: "Bug fix", slot: 3 },
  { type: "refactor", label: "Refactor", slot: 4 },
  { type: "chore", label: "Chore or tooling", slot: 5 },
  { type: "documentation", label: "Documentation", slot: 6 },
  { type: "test", label: "Test only", slot: 7 },
  { type: "performance", label: "Performance", slot: 8 },
  { type: "unclassified", label: "Unclassified", slot: null },
];

export function typeLabel(type: RequestType): string {
  return TYPES.find((entry) => entry.type === type)?.label ?? "Unclassified";
}

/** The CSS custom property a type's marks are painted with (light and dark set in CSS). */
export function typeColorVar(type: RequestType): string {
  const slot = TYPES.find((entry) => entry.type === type)?.slot ?? null;
  return slot === null ? "--op-flow-type-none" : `--op-flow-type-${slot}`;
}

/** "45 m", "3.2 h", "16 h", "2.4 d"; "—" when not known. */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return "—";
  if (hours < 1) {
    const minutes = Math.round(hours * 60);
    return minutes < 1 ? "under 1 m" : `${minutes} m`;
  }
  if (hours < 48) return hours < 10 ? `${trim(hours)} h` : `${Math.round(hours)} h`;
  const days = hours / 24;
  return days < 10 ? `${trim(days)} d` : `${Math.round(days)} d`;
}

function trim(value: number): string {
  const rounded = value.toFixed(1);
  return rounded.endsWith(".0") ? rounded.slice(0, -2) : rounded;
}

export function stageValue(stage: PullRequestFlowStageDto, pct: Percentile): number | null {
  return pct === "p50" ? stage.p50_hours : stage.p75_hours;
}

export function worstJam(flow: PullRequestFlowResponse, pct: Percentile): ReviewStage | null {
  return flow.worst_jam[pct];
}

/**
 * How thick the band is in each stage (1 is wide open) and how jammed it reads
 * (0 flows freely, 1 is the worst stage), from the stage times at `pct`.
 * Log-scaled: 45 minutes against 16 hours is a jam, not a hair-thin band. A
 * stage with no time known is drawn open and neutral.
 */
export function bandProfile(
  stages: readonly PullRequestFlowStageDto[],
  pct: Percentile,
): { stage: ReviewStage; thickness: number; jam: number; hours: number | null }[] {
  const byStage = new Map(stages.map((stage) => [stage.stage, stage]));
  const values = STAGES.map((stage) => {
    const view = byStage.get(stage);
    return view ? stageValue(view, pct) : null;
  });
  const top = Math.max(0, ...values.map((value) => (value === null ? 0 : Math.log1p(value))));
  return STAGES.map((stage, i) => {
    const value = values[i];
    // Raised to 1.5 so the longest stage stands out: 4 h beside 23 h is not a jam.
    const jam = value === null || top === 0 ? 0 : (Math.log1p(value) / top) ** 1.5;
    return { stage, hours: value, jam, thickness: 1 - 0.62 * jam };
  });
}

/** The band's thickness at `x` (0..1 across the four stages), eased between stage centres. */
export function thicknessAt(profile: readonly { thickness: number }[], x: number): number {
  const n = profile.length;
  if (n === 0) return 1;
  const position = Math.min(Math.max(x, 0), 1) * n - 0.5;
  if (position <= 0) return profile[0].thickness;
  if (position >= n - 1) return profile[n - 1].thickness;
  const i = Math.floor(position);
  const t = position - i;
  const eased = t * t * (3 - 2 * t);
  return profile[i].thickness + (profile[i + 1].thickness - profile[i].thickness) * eased;
}

/** A stable 0..1 number from a string: the same request lands in the same lane every time. */
export function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

export interface Dot {
  key: string;
  item: PullRequestFlowItemDto;
  /** -1..1 across the band. */
  lane: number;
  open: boolean;
  /** Merged: the share of its loop spent in each stage. */
  shares: number[];
  /** Merged: seconds per trip, and where in the trip it starts. */
  period: number;
  phase: number;
  /** Open: where it stands, 0..1 across the whole band. */
  rest: number;
}

const TRIP_SECONDS = 16;

/**
 * One dot per request the chart can place: a merged request with its history
 * read travels the band, lingering in each stage as long as it spent there
 * (log-scaled); an open one stands in its stage, further along the longer it
 * has waited. Requests with no history read are not drawn (they are counted).
 */
export function planDots(flow: PullRequestFlowResponse, pct: Percentile): Dot[] {
  const fallback = STAGES.map((stage) => {
    const view = flow.stages.find((entry) => entry.stage === stage);
    return (view ? stageValue(view, pct) : null) ?? 1;
  });
  const dots: Dot[] = [];
  for (const item of flow.items) {
    if (!item.timed) continue;
    const key = `${item.repo}!${item.number}`;
    const lane = hash01(`${key}:lane`) * 1.7 - 0.85;
    if (item.state === "merged") {
      // Merged with no review: it skipped the review stages, so it passes them
      // at once rather than at a typical wait it never had. Coding not known
      // (no commit read) takes the typical time.
      const skipped = item.stage_hours.awaiting_review === null;
      const hours = STAGES.map(
        (stage, i) => item.stage_hours[stage] ?? (skipped && i > 0 ? 0 : fallback[i]),
      );
      const weights = hours.map((h) => (0.12 + Math.log1p(Math.max(0, h))) ** 1.5);
      const total = weights.reduce((sum, w) => sum + w, 0);
      dots.push({
        key,
        item,
        lane,
        open: false,
        shares: weights.map((w) => w / total),
        period: TRIP_SECONDS * (0.8 + hash01(`${key}:pace`) * 0.4),
        phase: hash01(`${key}:phase`),
        rest: 0,
      });
    } else if (item.stage) {
      const index = STAGES.indexOf(item.stage);
      const typical = fallback[index] || 1;
      const age = item.stage_age_hours ?? 0;
      const along = 0.12 + 0.76 * (age / (age + typical));
      dots.push({
        key,
        item,
        lane,
        open: true,
        shares: [],
        period: 0,
        phase: 0,
        rest: (index + along) / STAGES.length,
      });
    }
  }
  return dots;
}

/** Where a dot is at `seconds` into the animation: 0..1 across the band. */
export function dotX(dot: Dot, seconds: number): number {
  if (dot.open) return dot.rest;
  const f = (((seconds / dot.period + dot.phase) % 1) + 1) % 1;
  let start = 0;
  for (let i = 0; i < dot.shares.length; i++) {
    const share = dot.shares[i];
    if (f < start + share || i === dot.shares.length - 1) {
      const within = share > 0 ? Math.min(1, (f - start) / share) : 0;
      return (i + within) / dot.shares.length;
    }
    start += share;
  }
  return 1;
}

export function typeSourceWords(source: TypeSource, evidence: string | null | undefined): string {
  const what = evidence ? ` ${evidence}` : "";
  switch (source) {
    case "author":
      return `written by a dependency bot${what ? ` (${evidence})` : ""}`;
    case "issue":
      return `from the Jira issue${what}`;
    case "label":
      return `from the label${what}`;
    case "title":
      return `from the title prefix${what}`;
    case "branch":
      return `from the branch prefix${what}`;
    default:
      return "no rule named a type";
  }
}

/** The request's reference as people say it: "storefront-web !12" or "api #4". */
export function requestReference(
  item: Pick<PullRequestFlowItemDto, "repo" | "number" | "web_url">,
) {
  const sigil = item.web_url && item.web_url.includes("/merge_requests/") ? "!" : "#";
  const short = item.repo.replace(/\/+$/, "").split("/").pop() || item.repo;
  return `${short} ${sigil}${item.number}`;
}

/** Where a request stands, in words: "Awaiting review for 16 h", "Merged". */
export function standingWords(item: PullRequestFlowItemDto): string {
  if (item.state === "merged") return "Merged";
  if (!item.timed || !item.stage) return "Open, stage not known";
  const age = item.stage_age_hours === null ? "" : ` for ${formatHours(item.stage_age_hours)}`;
  return `${STAGE_LABELS[item.stage]}${age}${item.draft ? " (draft)" : ""}`;
}

/** Hours from the first commit to the merge, when every stage it went through is known. */
export function leadHours(item: PullRequestFlowItemDto): number | null {
  if (item.state !== "merged" || !item.timed) return null;
  const known = STAGES.map((stage) => item.stage_hours[stage]).filter(
    (value): value is number => value !== null && value !== undefined,
  );
  return known.length ? known.reduce((sum, h) => sum + h, 0) : null;
}

/** The scope in words: "all repositories", "project Checkout (2 repositories)". */
export function scopeWords(scope: PullRequestFlowResponse["scope"]): string {
  if (scope.kind === "tenant") return "Every synced repository";
  const repos = scope.repos;
  const repoWords =
    repos === null || repos === undefined
      ? "any repository"
      : repos.length === 1
        ? "1 repository"
        : `${repos.length} repositories`;
  if (scope.kind === "pod") {
    const members =
      scope.member_count === null || scope.member_count === undefined
        ? ""
        : ` by its ${scope.member_count === 1 ? "1 member" : `${scope.member_count} members`}`;
    return `Pod ${scope.name ?? scope.id}: requests${members} in ${repoWords}`;
  }
  const kind = scope.kind === "program" ? "Program" : "Project";
  return `${kind} ${scope.name ?? scope.id}: ${repoWords}`;
}

/** The screen reader's summary of the band. */
export function bandSummary(flow: PullRequestFlowResponse, pct: Percentile): string {
  const jam = worstJam(flow, pct);
  const parts = flow.stages.map((stage) => {
    const value = formatHours(stageValue(stage, pct));
    return `${stage.label} ${value}${stage.stage === jam ? " (the worst jam)" : ""}`;
  });
  return `Time in each stage at ${pct}, from ${flow.timed_merged_count} timed merged requests: ${parts.join(", ")}.`;
}

export interface InvestmentRow {
  type: RequestType;
  label: string;
  merged: number;
  open: number;
  share: number;
}

/**
 * Each type's share of the merged requests: the bars, largest first (a tie
 * keeps the fixed type order), and the types with none, in the fixed order, for
 * one muted line instead of empty bars. A bar keeps its type's colour wherever
 * the sort puts it.
 */
export function investment(flow: PullRequestFlowResponse): {
  bars: InvestmentRow[];
  none: InvestmentRow[];
} {
  const total = flow.type_counts.reduce((sum, row) => sum + row.merged_count, 0);
  const rows = TYPES.map(({ type, label }, order) => {
    const row = flow.type_counts.find((entry) => entry.request_type === type);
    const merged = row?.merged_count ?? 0;
    const share = total ? merged / total : 0;
    return { order, row: { type, label, merged, open: row?.open_count ?? 0, share } };
  });
  return {
    bars: rows
      .filter(({ row }) => row.merged > 0)
      .sort((a, b) => b.row.merged - a.row.merged || a.order - b.order)
      .map(({ row }) => row),
    none: rows.filter(({ row }) => row.merged === 0).map(({ row }) => row),
  };
}

/** "None in this window: Refactor, Documentation and Performance", or null when every type has some. */
export function noneWords(none: readonly { label: string }[]): string | null {
  if (none.length === 0) return null;
  const labels = none.map((row) => row.label);
  const list =
    labels.length === 1
      ? labels[0]
      : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  return `None in this window: ${list}.`;
}

/** "12%" or "<1%" for a share above zero; "0%" for none. */
export function formatShare(share: number): string {
  if (share <= 0) return "0%";
  const percent = Math.round(share * 100);
  return percent < 1 ? "<1%" : `${percent}%`;
}

/** The scope a URL value names: "all", "program:ID", "project:ID" or "pod:ID". */
export function parseScope(value: string | null): {
  kind: "all" | "program" | "project" | "pod";
  id: string | null;
} {
  const match = /^(program|project|pod):(.+)$/.exec(value ?? "");
  if (!match) return { kind: "all", id: null };
  return { kind: match[1] as "program" | "project" | "pod", id: match[2] };
}
