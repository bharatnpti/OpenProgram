// The Projects hub's addresses, in one place. Every delivery question is asked
// at one of three levels (All projects, one project, one of its pods) and on one
// of four tabs, and the URL holds both:
//
//   /portfolio/:tab?                          All projects
//   /projects/:projectId/:tab?                one project
//   /projects/:projectId/pods/:podId/:tab?    one pod, its part of that project
//
// The level and the tab are independent: a pick of one keeps the other. No
// imports, so `node --test` runs it as written.

export type HubLevel = "all" | "project" | "pod";

export type HubTab = "date" | "warnings" | "scope" | "reports";

/** The tab bar's order, at every level. */
export const HUB_TABS: readonly HubTab[] = ["date", "warnings", "scope", "reports"];

/** Each tab named by the question it answers. */
export const HUB_TAB_LABELS: Record<HubTab, string> = {
  date: "Will we make the date?",
  warnings: "Early warnings",
  scope: "Scope control",
  reports: "Reports",
};

/** What the top of the hub is called at the All level, the first step of every trail. */
export const ALL_PROJECTS = "All projects";

export type HubPlace =
  | { level: "all" }
  | { level: "project"; projectId: string }
  | { level: "pod"; projectId: string; podId: string };

export function isHubTab(value: string | null | undefined): value is HubTab {
  return value === "date" || value === "warnings" || value === "scope" || value === "reports";
}

const enc = encodeURIComponent;

/** A place's address, at a tab; no tab leaves the choice to the role's first one there. */
export function placePath(place: HubPlace, tab?: HubTab | null): string {
  const base =
    place.level === "all"
      ? "/portfolio"
      : place.level === "project"
        ? `/projects/${enc(place.projectId)}`
        : `/projects/${enc(place.projectId)}/pods/${enc(place.podId)}`;
  return tab ? `${base}/${tab}` : base;
}

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/**
 * Where a path sits in the hub: its place, and the segment after it, which is
 * a tab when it names one (`tab`), or anything else a link carried (`rest`,
 * an old `/projects/<id>/daily`). Null for a path outside the hub, and for the
 * bare `/projects`, the landing that resolves by role.
 */
export function parseHub(
  pathname: string,
): { place: HubPlace; tab: HubTab | null; rest: string | null } | null {
  const parts = pathname.split("/").filter(Boolean);
  const after = (index: number) => {
    const segment = parts[index];
    if (segment === undefined) return { tab: null, rest: null };
    const word = decode(segment);
    return isHubTab(word) && parts.length === index + 1
      ? { tab: word, rest: null }
      : { tab: null, rest: parts.slice(index).map(decode).join("/") };
  };
  if (parts[0] === "portfolio") return { place: { level: "all" }, ...after(1) };
  if (parts[0] !== "projects" || !parts[1]) return null;
  const projectId = decode(parts[1]);
  if (parts[2] === "pods" && parts[3]) {
    return { place: { level: "pod", projectId, podId: decode(parts[3]) }, ...after(4) };
  }
  return { place: { level: "project", projectId }, ...after(2) };
}

/**
 * The tab a section's anchor lives on now, for the links sent before the tabs:
 * a project's `#timeline`, `#stages` and `#work` are on the date, `#warnings`
 * and `#questions` on Early warnings, `#scope`, `#gates`, `#readiness` and
 * `#hygiene` on Scope control; a pod's `#checkins`, `#blockers`, `#status` and
 * `#people` (its morning) on Early warnings, `#dates` on the date. Null for an
 * anchor no tab owns.
 */
export function tabOfAnchor(level: "project" | "pod", hash: string): HubTab | null {
  const id = decode(hash.replace(/^#/, ""));
  if (!id) return null;
  const tabs: Record<string, HubTab> =
    level === "project"
      ? {
          timeline: "date",
          stages: "date",
          work: "date",
          answer: "date",
          warnings: "warnings",
          questions: "warnings",
          scope: "scope",
          gates: "scope",
          readiness: "scope",
          hygiene: "scope",
          baseline: "scope",
        }
      : {
          dates: "date",
          timeline: "date",
          checkins: "warnings",
          blockers: "warnings",
          status: "warnings",
          people: "warnings",
        };
  return Object.prototype.hasOwnProperty.call(tabs, id) ? tabs[id] : null;
}

/** A place and a tab together: a spot in the hub. */
export type HubSpot = { place: HubPlace; tab: HubTab | null };

function projectOf(place: HubPlace): string | null {
  return place.level === "all" ? null : place.projectId;
}

/**
 * The query a move in the hub carries, as a `?…` string or "": the viewing
 * day always; the release picked (`?release=`) while the project stays the
 * same, at the project or a pod of it, on every tab; the programme
 * (`?program=`) between the All level's tabs. Everything else belongs to one
 * tab of one place (the row picked, the report, the brief kind, a search) and
 * is left behind.
 */
export function carriedQuery(search: string, from: HubSpot, to: HubSpot): string {
  const params = new URLSearchParams(search);
  const next = new URLSearchParams();
  const asOf = params.get("asOf");
  if (asOf) next.set("asOf", asOf);
  const sameProject =
    projectOf(from.place) !== null && projectOf(from.place) === projectOf(to.place);
  if (sameProject) {
    const release = params.get("release");
    if (release) next.set("release", release);
  }
  if (from.place.level === "all" && to.place.level === "all") {
    const program = params.get("program");
    if (program) next.set("program", program);
  }
  const query = next.toString();
  return query ? `?${query}` : "";
}
