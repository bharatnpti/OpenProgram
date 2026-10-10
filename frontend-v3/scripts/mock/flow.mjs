// Mock handler for flow through review (GET /portfolio/pr-flow), used by
// scripts/mock-api.mjs before the console mock. NOT real data: about 640 merge
// requests over 100 days and three dozen open ones, made up from a seeded
// random sequence so every run (and every screenshot) is the same. Shapes
// follow src/api/generated.ts; the stage and type rules follow the backend's
// (core/domain/review_flow.py), simplified.
import * as consoleData from "../mock-console.mjs";

const NOW = Date.parse("2026-10-06T15:00:00Z");
const HOUR = 3600 * 1000;
const TODAY = "2026-10-06";

// Repositories, the projects that list them and the pod that works in each.
const REPOS = [
  { repo: "acme/checkout-api", project: "project-checkout", pod: "pod-payments", weight: 5 },
  { repo: "acme/storefront-web", project: "project-checkout", pod: "pod-storefront", weight: 4 },
  { repo: "acme/platform-libs", project: "project-checkout", pod: "pod-payments", weight: 1 },
  { repo: "acme/identity-service", project: "project-identity", pod: "pod-identity", weight: 2 },
  { repo: "acme/sso-gateway", project: "project-identity", pod: "pod-identity", weight: 1 },
  { repo: "acme/insights-pipeline", project: "project-insights", pod: "pod-data", weight: 2 },
];

const TYPE_LABELS = {
  feature: "Feature",
  dependency_update: "Dependency update",
  bug_fix: "Bug fix",
  refactor: "Refactor",
  chore: "Chore or tooling",
  documentation: "Documentation",
  test: "Test only",
  performance: "Performance",
  unclassified: "Unclassified",
};
const TYPE_ORDER = Object.keys(TYPE_LABELS);
const TYPE_WEIGHTS = [
  ["feature", 30],
  ["dependency_update", 19],
  ["bug_fix", 17],
  ["refactor", 9],
  ["chore", 8],
  ["documentation", 6],
  ["test", 5],
  ["performance", 2],
  ["unclassified", 4],
];
const STAGES = ["coding", "awaiting_review", "in_review", "awaiting_merge"];
const STAGE_LABELS = {
  coding: "Coding",
  awaiting_review: "Awaiting review",
  in_review: "In review",
  awaiting_merge: "Awaiting merge",
};

const SUBJECTS = {
  feature: [
    "promo codes",
    "saved cards",
    "passkey sign-in",
    "refund webhooks",
    "cart sharing",
    "order history export",
    "SAML metadata refresh",
    "event replay",
  ],
  dependency_update: [
    "httpx to 0.28",
    "react to 19.2",
    "fastapi to 0.118",
    "vite to 8.1",
    "psycopg to 3.3",
    "eslint to 9.38",
  ],
  bug_fix: [
    "login loop on expired refresh token",
    "rounding in cart totals",
    "duplicate events in hourly rollup",
    "declined card copy",
    "timezone on receipts",
    "retry storm on 502",
  ],
  refactor: [
    "split the payment service",
    "extract price rules",
    "one HTTP client",
    "typed settings",
  ],
  chore: ["CI cache for uv", "bump version", "lint config", "release script"],
  documentation: [
    "sandbox setup",
    "runbook for refunds",
    "API errors table",
    "ADR: idempotency keys",
  ],
  test: ["guest checkout", "expired codes", "idempotency replay", "SSO edge cases"],
  performance: ["cache price lookups", "batch event writes", "lazy-load the cart"],
  unclassified: ["Cart totals", "Payment form tweaks", "Follow-up from review", "Misc fixes"],
};
const PREFIX = {
  feature: "feat",
  bug_fix: "fix",
  refactor: "refactor",
  chore: "chore",
  documentation: "docs",
  test: "test",
  performance: "perf",
};

// mulberry32: a small seeded generator, so the made-up data never changes.
function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function build() {
  const random = seeded(20261006);
  const pick = (list) => list[Math.floor(random() * list.length)];
  const weighted = (pairs) => {
    const total = pairs.reduce((sum, [, w]) => sum + w, 0);
    let roll = random() * total;
    for (const [value, w] of pairs) {
      roll -= w;
      if (roll <= 0) return value;
    }
    return pairs[pairs.length - 1][0];
  };
  // A log-normal wait around `median` hours.
  const wait = (median, spread) => {
    const u = Math.max(1e-6, random());
    const v = random();
    const normal = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    return median * Math.exp(spread * normal);
  };
  const members = (pod) =>
    consoleData.roster
      .filter((person) => person.pods.includes(pod) && person.roles.includes("dev"))
      .map((person) => person.id);
  const repoPicks = REPOS.map((entry) => [entry, entry.weight]);
  const numbers = new Map();
  const requests = [];

  const make = (open) => {
    const where = weighted(repoPicks);
    const type = weighted(TYPE_WEIGHTS);
    const bot = type === "dependency_update" && random() < 0.85;
    const author = bot ? null : pick(members(where.pod));
    const number = (numbers.get(where.repo) ?? 40) + 1;
    numbers.set(where.repo, number);
    const subject = pick(SUBJECTS[type]);
    const key = `CHK-${100 + Math.floor(random() * 90)}`;
    const capital = `${subject[0].toUpperCase()}${subject.slice(1)}`;
    // How each kind of request names itself, and so which rule types it: a bot;
    // features and fixes often by their Jira issue; the rest by a title prefix
    // or a label; unclassified by nothing at all.
    const style = bot
      ? "author"
      : type === "dependency_update"
        ? "build"
        : type === "unclassified"
          ? "none"
          : (type === "feature" || type === "bug_fix") && random() < 0.5
            ? "issue"
            : random() < 0.85
              ? "title"
              : "label";
    const title =
      style === "author"
        ? `chore(deps): update ${subject}`
        : style === "build"
          ? `build: bump ${subject}`
          : style === "title"
            ? `${PREFIX[type]}: ${subject}`
            : style === "issue"
              ? `${key} ${capital}`
              : capital;
    const source = {
      author: ["author", "renovate[bot]"],
      build: ["title", "build:"],
      title: ["title", `${PREFIX[type]}:`],
      issue: ["issue", `${type === "bug_fix" ? "Bug" : "Story"} ${key}`],
      label: ["label", `type::${type.replace("_", "-")}`],
      none: ["none", null],
    }[style];
    // Storefront waits longest for a reviewer; identity reviews fast.
    const reviewPace =
      where.repo === "acme/storefront-web" ? 1.6 : where.pod === "pod-identity" ? 0.6 : 1;
    const skipsReview = bot ? random() < 0.55 : random() < 0.04;
    const hours = {
      coding: bot ? wait(0.05, 0.5) : wait(type === "documentation" ? 1.2 : 3.2, 0.95),
      awaiting_review: skipsReview ? null : wait(14 * reviewPace, 0.85),
      in_review: skipsReview ? null : wait(type === "feature" ? 2.6 : 1.4, 0.9),
      awaiting_merge: skipsReview ? null : wait(0.7, 0.85),
    };
    const lead =
      hours.coding +
      (hours.awaiting_review ?? 0) +
      (hours.in_review ?? 0) +
      (hours.awaiting_merge ?? 0);
    const request = {
      repo: where.repo,
      project: where.project,
      pod: where.pod,
      number: String(number),
      title,
      author,
      author_name: author ? consoleData.roster.find((p) => p.id === author)?.name : "Renovate Bot",
      request_type: source[0] === "none" ? "unclassified" : type,
      type_source: source[0],
      type_evidence: source[1],
      hours,
      timed: true,
      draft: false,
    };
    if (!open) {
      // Merged at some point in the last 100 days. Performance work all landed
      // more than a month ago, so the 30-day view has a type with none (the
      // "None in this window" line) and the 90-day view has every type.
      const merged =
        type === "performance"
          ? NOW - (32 + random() * 66) * 24 * HOUR
          : NOW - random() * 100 * 24 * HOUR;
      request.merged_at = merged;
      request.opened_at = merged - (lead - hours.coding) * HOUR;
      request.state = "merged";
    } else {
      // Open now: which stage it has reached, and how long it has been there.
      const reached = weighted([
        ["coding", 9],
        ["awaiting_review", 15],
        ["in_review", 7],
        ["awaiting_merge", 4],
      ]);
      const index = STAGES.indexOf(reached);
      for (let i = index; i < STAGES.length; i++) hours[STAGES[i]] = null;
      if (reached !== "coding") hours.coding = hours.coding ?? wait(3, 0.9);
      if (index > 1) hours.awaiting_review = wait(14 * reviewPace, 0.85);
      if (index > 2) hours.in_review = wait(2, 0.9);
      const age = reached === "awaiting_review" ? wait(20 * reviewPace, 1.1) : wait(5, 1);
      request.state = "open";
      request.stage = reached;
      request.stage_age_hours = age;
      request.stage_since = NOW - age * HOUR;
      request.draft = reached === "coding" && random() < 0.6;
      request.opened_at = NOW - (age + 2) * HOUR;
      request.merged_at = null;
    }
    requests.push(request);
  };

  for (let i = 0; i < 640; i++) make(false);
  for (let i = 0; i < 36; i++) make(true);
  // Four merged long ago, synced before histories were read: counted, not timed.
  for (const request of requests.filter((r) => r.state === "merged").slice(0, 4)) {
    request.timed = false;
    request.hours = { coding: null, awaiting_review: null, in_review: null, awaiting_merge: null };
  }
  return requests;
}

const REQUESTS = build();

function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = fraction * (sorted.length - 1);
  const low = Math.floor(rank);
  const high = Math.min(low + 1, sorted.length - 1);
  return sorted[low] + (sorted[high] - sorted[low]) * (rank - low);
}

// The four stages over these requests: one rule for every figure, all types
// and each type alone, as the backend's _stage_views.
function stageViews(merged, open) {
  return STAGES.map((stage) => {
    const values = merged.map((r) => r.hours[stage]).filter((v) => v !== null);
    return {
      stage,
      label: STAGE_LABELS[stage],
      p50_hours: percentile(values, 0.5),
      p75_hours: percentile(values, 0.75),
      measured_count: values.length,
      open_count: open.filter((r) => r.stage === stage).length,
    };
  });
}

function worstJam(stages, field) {
  const timed = stages.filter((s) => s[field]);
  // As the backend: a jam needs two timed stages or more to be the longest of.
  return timed.length > 1 ? timed.reduce((a, b) => (b[field] > a[field] ? b : a)).stage : null;
}

function scopeOf(url) {
  const programId = url.searchParams.get("program_id");
  const projectId = url.searchParams.get("project_id");
  const podId = url.searchParams.get("pod_id");
  const asked = [programId, projectId, podId].filter(Boolean);
  if (asked.length > 1)
    return { error: [422, "ask for one of program_id, project_id or pod_id, not several"] };
  if (podId) {
    const pod = consoleData.pods.find((p) => p.id === podId);
    if (!pod) return { error: [404, `pod ${podId} not found`] };
    const repos = REPOS.filter((r) => r.pod === podId).map((r) => r.repo);
    return {
      view: { kind: "pod", id: pod.id, name: pod.name, repos, member_count: pod.member_ids.length },
      test: (r) =>
        r.pod === podId && (r.author === null ? false : pod.member_ids.includes(r.author)),
    };
  }
  if (projectId) {
    const project = consoleData.projects.find((p) => p.id === projectId);
    if (!project) return { error: [404, `project ${projectId} not found`] };
    const repos = REPOS.filter((r) => r.project === projectId).map((r) => r.repo);
    return {
      view: { kind: "project", id: project.id, name: project.name, repos, member_count: null },
      test: (r) => r.project === projectId,
    };
  }
  if (programId) {
    const program = consoleData.programs.find((p) => p.id === programId);
    if (!program) return { error: [404, `program ${programId} not found`] };
    return {
      view: {
        kind: "program",
        id: program.id,
        name: program.name,
        repos: REPOS.map((r) => r.repo).sort(),
        member_count: null,
      },
      test: () => true,
    };
  }
  return {
    view: { kind: "tenant", id: null, name: null, repos: null, member_count: null },
    test: () => true,
  };
}

const iso = (ms) => (ms === null || ms === undefined ? null : new Date(ms).toISOString());

function respond(url) {
  const days = Math.min(180, Math.max(1, Number(url.searchParams.get("days") ?? 30) || 30));
  const asOf = url.searchParams.get("as_of") ?? TODAY;
  const end = asOf >= TODAY ? NOW : Date.parse(`${asOf}T00:00:00Z`) + 24 * HOUR;
  const start = end - days * 24 * HOUR;
  const scope = scopeOf(url);
  if (scope.error) return scope;
  const inScope = REQUESTS.filter(scope.test);
  const merged = inScope
    .filter((r) => r.state === "merged" && r.merged_at >= start && r.merged_at <= end)
    .sort((a, b) => b.merged_at - a.merged_at);
  const open = inScope
    .filter((r) => r.state === "open" && r.opened_at <= end)
    .sort((a, b) => b.stage_age_hours - a.stage_age_hours);
  const stages = stageViews(merged, open);
  const untimed = merged.filter((r) => !r.timed).length;
  const unreviewed = merged.filter((r) => r.timed && r.hours.awaiting_review === null).length;
  const notes = [];
  if (scope.view.repos && scope.view.repos.length === 0)
    notes.push(`This ${scope.view.kind} lists no repositories, so no requests are in it.`);
  if (untimed)
    notes.push(
      `${untimed} request${untimed === 1 ? " was" : "s were"} synced before review histories were read, so ${untimed === 1 ? "its" : "their"} stages are not known: counted, not timed. The next Git sync reads them.`,
    );
  if (unreviewed)
    notes.push(
      `${unreviewed} merged request${unreviewed === 1 ? "" : "s"} had no review by anyone but the author: counted for coding only, not as waiting for review.`,
    );
  const item = (r) => ({
    repo: r.repo,
    number: r.number,
    title: r.title,
    web_url: `https://git.example.test/${r.repo}/-/merge_requests/${r.number}`,
    author_name: r.author_name ?? null,
    request_type: r.request_type,
    type_source: r.type_source,
    type_evidence: r.type_evidence,
    state: r.state,
    draft: r.draft,
    stage: r.state === "open" ? r.stage : null,
    stage_since: r.state === "open" ? iso(r.stage_since) : null,
    stage_age_hours: r.state === "open" ? r.stage_age_hours : null,
    stage_hours: r.hours,
    opened_at: iso(r.opened_at),
    merged_at: iso(r.merged_at),
    reviewer_count: r.hours.awaiting_review === null ? 0 : 1,
    timed: r.timed,
  });
  return {
    body: {
      as_of: asOf,
      window_days: days,
      window_start: iso(start),
      window_end: iso(end),
      scope: scope.view,
      merged_count: merged.length,
      open_count: open.length,
      timed_merged_count: merged.length - untimed,
      unreviewed_merged_count: unreviewed,
      untimed_count: untimed,
      stages,
      worst_jam: { p50: worstJam(stages, "p50_hours"), p75: worstJam(stages, "p75_hours") },
      type_counts: TYPE_ORDER.map((type) => ({
        request_type: type,
        label: TYPE_LABELS[type],
        merged_count: merged.filter((r) => r.request_type === type).length,
        open_count: open.filter((r) => r.request_type === type).length,
      })),
      // Every type, in the fixed order, timed from its own requests alone; a
      // type with none has its four stages with no time and a count of 0.
      stages_by_type: Object.fromEntries(
        TYPE_ORDER.map((type) => {
          const own = stageViews(
            merged.filter((r) => r.request_type === type),
            open.filter((r) => r.request_type === type),
          );
          return [
            type,
            {
              stages: own,
              worst_jam: { p50: worstJam(own, "p50_hours"), p75: worstJam(own, "p75_hours") },
            },
          ];
        }),
      ),
      items: [...merged.slice(0, 400), ...open.slice(0, 200)].map(item),
      items_truncated: merged.length > 400 || open.length > 200,
      notes,
    },
  };
}

const canAggregate = (roles) => roles.some((r) => ["sm", "po", "mgr", "exec", "admin"].includes(r));

/** Answers GET /portfolio/pr-flow; returns true when it handled the request. */
export function api(req, url, roles, _userId, send, deny) {
  if (url.pathname !== "/portfolio/pr-flow" || req.method !== "GET") return false;
  if (!canAggregate(roles)) {
    deny();
    return true;
  }
  const answer = respond(url);
  if (answer.error) send(answer.error[0], { detail: answer.error[1] });
  else send(200, answer.body);
  return true;
}
