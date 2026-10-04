import type {
  DriftFindingResponse,
  ProgramTreeResponse,
  Rag,
  RollupFactorDto,
} from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/** A heat-map tile: one project, workstream or pod as the directory lists it. */
type Tile = { id: string; rag: Rag | null };

/** The parts of a rollup factor this line reads. */
type Factor = Pick<RollupFactorDto, "kind" | "contributes" | "description" | "blocker_id"> & {
  source_ref: { id: string };
  work_item_ref: { id: string } | null;
};

type TreeNode = Pick<ProgramTreeResponse["nodes"][number], "id" | "kind" | "name" | "source"> & {
  factors: Factor[];
};

/** The program's rollup tree, as far as it has been read. */
export type RollupTree =
  { status: "loading" } | { status: "failed" } | { status: "ready"; nodes: TreeNode[] };

export type HeroDetailInput = {
  /** The headline's colour: the worst tile among projects, workstreams and pods. */
  worstRag: Rag;
  heatLoading: boolean;
  heatFailed: boolean;
  risksLoading: boolean;
  risksFailed: boolean;
  /** The reason of the risk record that leads, when there is one. */
  topRiskReason: string | undefined;
  drift: Pick<DriftFindingResponse, "kind">[];
  tiles: { pods: Tile[]; projects: Tile[]; workstreams: Tile[] };
  tree: RollupTree;
};

export const ALL_CLEAR =
  "No material risks detected across projects, workstreams, or pods right now.";

/**
 * The line under the Today hero's headline: what is behind its colour.
 *
 * It used to come from the risk list alone. Tiles turned amber by partial or
 * inferred statuses, blockers or drift carry no risk record, so the hero read
 * "needs attention" over "No material risks detected". Now a risk record, when
 * there is one, still leads; with none, an amber or red headline names what
 * drives it -- open blockers with their key and owner, how many tiles are amber
 * or red and their top rollup reason, and open drift signals -- and the
 * all-clear is said only under a green headline. Silence is never green: while
 * the tiles load, fail or report nothing, the line says nothing has reported.
 */
export function heroDetailLine(input: HeroDetailInput): string {
  if (input.risksFailed) return "Risks could not be loaded, so this is not an all-clear.";
  if (input.topRiskReason) return input.topRiskReason;
  if (input.risksLoading) return "Checking for open risks…";
  if (input.heatFailed || input.heatLoading || input.worstRag === "unknown") {
    return "Nothing has reported a status yet.";
  }
  const drift = driftParts(input.drift);
  if (input.worstRag === "green") {
    return drift.length > 0 ? `${ALL_CLEAR} ${sentence(drift)}` : ALL_CLEAR;
  }
  return driversLine(input.tiles, input.tree, drift);
}

function driversLine(tiles: HeroDetailInput["tiles"], tree: RollupTree, drift: string[]): string {
  if (tree.status === "loading") return "Checking what is behind this status…";
  const nodes = tree.status === "ready" ? tree.nodes : [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const blockers = openBlockers(nodes);
  const parts = [
    blockersPart(blockers),
    tilesPart(tiles, byId, blockers.length > 0),
    ...drift,
  ].filter((part): part is string => part !== null);
  if (parts.length === 0) return "What drives this status is not recorded yet.";
  const line = sentence(parts);
  // Without the tree, blockers and reasons are unknown, not absent.
  return tree.status === "failed" ? `${line} Blockers and reasons could not be loaded.` : line;
}

type Blocker = { key: string | null; owner: string };

/**
 * Each open blocker once, with its work item key and the person it is from.
 * A blocker is a developer's factor and travels up to every pod and project it
 * touches, so it is read off the developer and counted by its id.
 */
function openBlockers(nodes: TreeNode[]): Blocker[] {
  const blockers = new Map<string, Blocker>();
  nodes
    .filter((node) => node.kind === "developer")
    .forEach((node) => {
      node.factors
        .filter((factor) => factor.kind === "blocker" && factor.contributes !== "green")
        .forEach((factor) => {
          const id = factor.blocker_id ?? `${node.id}:${factor.description}`;
          if (!blockers.has(id)) {
            blockers.set(id, { key: factor.work_item_ref?.id ?? null, owner: node.name });
          }
        });
    });
  return [...blockers.values()];
}

/** Two blockers by name at most, those with a work item key first. */
function blockersPart(blockers: Blocker[]): string | null {
  if (blockers.length === 0) return null;
  const labels = [...blockers]
    .sort((a, b) => Number(a.key === null) - Number(b.key === null))
    .map(({ key, owner }) => (key ? `${owner} on ${key}` : owner));
  return `${count(blockers.length, "open blocker")} (${joinAnd(labels, 2)})`;
}

/**
 * How many tiles of each kind are amber or red, and the reason most of them
 * share. Pods come first: their people's updates are where the colour starts.
 */
function tilesPart(
  tiles: HeroDetailInput["tiles"],
  byId: Map<string, TreeNode>,
  blockersNamed: boolean,
): string | null {
  const groups = [
    { noun: "pod", items: tiles.pods },
    { noun: "project", items: tiles.projects },
    { noun: "workstream", items: tiles.workstreams },
  ]
    .map(({ noun, items }) => ({ noun, total: items.length, hot: items.filter(isAmberOrRed) }))
    .filter((group) => group.hot.length > 0);
  if (groups.length === 0) return null;
  const colours = groups.map((group) => colourOf(group.hot));
  const shares = groups.map((group) => share(group.hot.length, group.total, group.noun));
  const counted = colours.every((colour) => colour === colours[0])
    ? `${joinAnd(shares)} ${colours[0]}`
    : joinAnd(shares.map((text, index) => `${text} ${colours[index]}`));
  const reason = topReason(
    groups.flatMap((group) => group.hot),
    byId,
    blockersNamed,
  );
  return reason ? `${counted}, mostly from ${reason}` : counted;
}

function isAmberOrRed(tile: Tile): boolean {
  return tile.rag === "amber" || tile.rag === "red";
}

function colourOf(tiles: Tile[]): string {
  const red = tiles.some((tile) => tile.rag === "red");
  const amber = tiles.some((tile) => tile.rag === "amber");
  return red && amber ? "amber or red" : red ? "red" : "amber";
}

function share(hot: number, total: number, noun: string): string {
  if (hot === total) return total === 1 ? `the only ${noun}` : `all ${total} ${noun}s`;
  return `${hot} of ${total} ${noun}s`;
}

type Reason =
  | "blocker"
  | "drift"
  | "partial"
  | "inferred"
  | "stale"
  | "missing"
  | "blocked_task"
  | "task"
  | "target_date";

/** Short names for the rollup's reasons, in tie-break order. */
const REASON_TEXT: Record<Reason, string> = {
  blocker: "open blockers",
  drift: "signals that disagree",
  partial: "partial updates",
  inferred: "inferred statuses",
  stale: "stale updates",
  missing: "missing updates",
  blocked_task: "blocked tasks",
  task: "tasks needing attention",
  target_date: "approaching target dates",
};
const REASON_ORDER = Object.keys(REASON_TEXT) as Reason[];

/**
 * The reason a factor stands for, from its typed kind -- never its wording. A
 * status factor's own text names no source, so the person it comes from says
 * whether their update was partial, inferred or stale.
 */
function reasonOf(factor: Factor, byId: Map<string, TreeNode>): Reason | null {
  if (factor.contributes === "green") return null;
  switch (factor.kind) {
    case "blocker":
      return "blocker";
    // Signals that disagree with an owner's issue turn tiles amber (N3).
    case "drift":
      return "drift";
    case "task":
      return factor.contributes === "red" ? "blocked_task" : "task";
    case "target_date":
      return "target_date";
    case "status":
    case "aggregate": {
      if (factor.contributes === "unknown") return "missing";
      const source = byId.get(factor.source_ref.id)?.source;
      return source === "partial" || source === "inferred" || source === "stale" ? source : null;
    }
    default:
      return null;
  }
}

/**
 * The reason behind the most amber or red tiles. Blockers are already named
 * when there are any, so a tie goes to the other reason, and blockers leading
 * add nothing the line has not said.
 */
function topReason(
  tiles: Tile[],
  byId: Map<string, TreeNode>,
  blockersNamed: boolean,
): string | null {
  const counts = new Map<Reason, number>();
  tiles.forEach((tile) => {
    const reasons = new Set(
      (byId.get(tile.id)?.factors ?? [])
        .map((factor) => reasonOf(factor, byId))
        .filter((reason): reason is Reason => reason !== null),
    );
    reasons.forEach((reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1));
  });
  const named = (reason: Reason) => Number(blockersNamed && reason === "blocker");
  const [top] = [...counts.entries()].sort(
    ([a, countA], [b, countB]) =>
      countB - countA || named(a) - named(b) || REASON_ORDER.indexOf(a) - REASON_ORDER.indexOf(b),
  );
  if (!top || named(top[0])) return null;
  return REASON_TEXT[top[0]];
}

/** Drift kinds as a reader would say them, singular and plural. */
const DRIFT_TEXT = new Map<string, [string, string]>([
  [
    "said_done_no_pr",
    ["item marked done with no pull request", "items marked done with no pull request"],
  ],
  [
    "green_over_red",
    ["workstream green over a red critical item", "workstreams green over a red critical item"],
  ],
  [
    "claimed_progress_no_activity",
    [
      "item reported in progress with no Git activity",
      "items reported in progress with no Git activity",
    ],
  ],
  [
    "merged_issue_open",
    ["ticket merged but still open in Jira", "tickets merged but still open in Jira"],
  ],
]);

/** One part per kind of open drift signal, e.g. "2 tickets merged but still open in Jira". */
function driftParts(drift: Pick<DriftFindingResponse, "kind">[]): string[] {
  const counts = new Map<string, number>();
  drift.forEach(({ kind }) => {
    const key = DRIFT_TEXT.has(kind) ? kind : "";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  const known = [...DRIFT_TEXT.keys()].filter((kind) => counts.has(kind));
  const parts = known.map((kind) => {
    const [one, many] = DRIFT_TEXT.get(kind) ?? ["", ""];
    const n = counts.get(kind) ?? 0;
    return `${n} ${n === 1 ? one : many}`;
  });
  const other = counts.get("") ?? 0;
  if (other > 0) {
    parts.push(count(other, known.length > 0 ? "other drift signal" : "drift signal"));
  }
  return parts;
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function joinAnd(items: string[], max = Number.POSITIVE_INFINITY): string {
  const shown = items.length > max ? [...items.slice(0, max), `${items.length - max} more`] : items;
  if (shown.length <= 1) return shown.join("");
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

/** Parts joined into one sentence, capitalised and closed. */
function sentence(parts: string[]): string {
  const text = parts.join("; ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
