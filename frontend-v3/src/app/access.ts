// What each role is offered, in one place: the sidebar's pages, the
// Projects hub's levels and tabs, the sections inside them, where the ⌘K
// palette takes a row, where an address from before the hub goes now, and
// where a link to a page the role is not offered goes instead. A page or section the role is not offered is not drawn
// at all: no struck-through item, no line saying which role would open it. The
// flags only forecast the backend, which still decides; a 403 that comes back
// anyway shows the server's reason.
//
// Type imports, and runtime imports by their .ts path, so `node --test` runs it.
import type { DeliveryTreeResponse } from "../api/schema";
import { ASK_LABEL } from "../features/assistant/persona.ts";
import type { OwnLinks } from "../features/delivery/ownTree";
import {
  HUB_TABS,
  parseHub,
  placePath,
  tabOfAnchor,
  type HubLevel,
  type HubPlace,
  type HubTab,
} from "../features/hub/hubPaths.ts";
import { podPath, programPath, projectPath, workstreamPath } from "./links.ts";
import type { AppRole } from "./roleWords";

/**
 * The backend's read capabilities for the roles the API is told
 * (core/application/authorization.py), on every node. A scrum master's own
 * projects and a developer's own pod are read beyond these, node by node
 * (core/application/delivery_scope.py): `GET /me/delivery-tree` says which.
 */
export type Capabilities = {
  /** `READ_PROJECT_PROGRESS`: product owner, manager, executive, admin. */
  canReadProjectProgress: boolean;
  /** `READ_TEAM_AGGREGATE` or `READ_EXEC_AGGREGATE`: everyone but the developer. */
  canReadAggregate: boolean;
  /** `MANAGE_CONFIG`: admin. */
  canManageConfig: boolean;
  /** `READ_POD_CHECKINS` + `READ_POD_BLOCKERS`: scrum master, manager, admin. */
  canReadPodDetail: boolean;
  /** Program rollups and the portfolio heat map: manager, executive, admin. */
  canReadPortfolio: boolean;
  /** `SET_UP_DAY_REPORTS`: scrum master (own projects), manager, admin. */
  canSetUpDayReports: boolean;
  /**
   * `SET_POD_PROFILES`: admin everywhere, a scrum master in the pods they run
   * (the server's `can_edit` on each pod says which). A profile grants no rights.
   */
  canSetPodProfiles: boolean;
};

export function capabilitiesFor(lens: readonly AppRole[]): Capabilities {
  const has = (...roles: AppRole[]) =>
    lens.includes("admin") || roles.some((role) => lens.includes(role));
  return {
    canReadProjectProgress: has("po", "mgr", "exec"),
    canReadAggregate: has("sm", "po", "mgr", "exec"),
    canManageConfig: has(),
    canReadPodDetail: has("sm", "mgr"),
    canReadPortfolio: has("mgr", "exec"),
    canSetUpDayReports: has("sm", "mgr"),
    canSetPodProfiles: has("sm"),
  };
}

/**
 * The sidebar's pages, in its order: Daily (Today, Check-in, Requests),
 * Delivery (Projects, the hub every delivery question is asked in), Insight
 * (Review flow, Ask Ora) and Setup (People & roles, Admin).
 */
export type Page =
  "today" | "checkin" | "requests" | "projects" | "flow" | "ora" | "people" | "admin";
export type NodeKind = "program" | "project" | "workstream" | "pod";
export type SignalsView = "risks" | "flow";
export type BriefKind = "exec" | "weekly_project" | "daily_pod";
/** Whose work a list opens on: the viewer's pods, the projects of their pods, or everything. */
export type Scope = "pods" | "projects" | "all";
/**
 * What Delivery lists: every program, project, workstream and pod (`all`), or
 * the person's own part of the tree (`own`, from `GET /me/delivery-tree`): the
 * projects of their pods and the projects they own, each with all its pods,
 * the program only as their heading.
 */
export type DeliveryScope = "all" | "own";

/**
 * The Projects hub as a role reads it. Every delivery question is asked at one
 * of three levels (All projects, one project, one of its pods) on one of four
 * tabs; a tab or a level the role cannot read is not drawn. The server still
 * decides each section.
 */
export type HubAccess = {
  /** The tabs at each level, in the tab bar's order; a level with none is not offered. */
  tabs: Record<HubLevel, HubTab[]>;
  /**
   * What the All level lists: every project (`every`), or the projects of the
   * reader's own pods (`own`, a scrum master's), as the server serves it.
   */
  allScope: "every" | "own";
  /** Names are never drawn: counts and teams instead (an executive). */
  countsOnly: boolean;
  /**
   * The fewest people a pod needs before its own level opens: five for a
   * counts-only reader, whose pods under five are counted with the project.
   */
  podMinPeople: number;
  /** Programme health and the heat view on All › Early warnings: manager, executive, admin. */
  portfolioHealth: boolean;
  /**
   * The bulk reads (`/portfolio/delivery`, `/portfolio/scope`,
   * `/portfolio/warnings`), and so the switcher's verdict dots and the tab
   * badges: everyone but a developer.
   */
  bulk: boolean;
};

export type Access = {
  /** The sidebar's pages the role is offered. */
  pages: Record<Page, boolean>;
  /** The Projects hub's levels and tabs. */
  hub: HubAccess;
  /** Check-in: the built-in chat's thread beside the form, on a local tenant only. */
  checkin: { chat: boolean };
  /**
   * Reports: `daily` is each project's day report, for every role but an
   * executive on their own (it is an action list that names each owner;
   * they read the Project page, and Reports opens on the briefs for them).
   */
  reports: { daily: boolean };
  /**
   * The assistant (POST /ask), floating on every tab: the aggregate readers
   * the endpoint answers (scrum master, product owner, manager, executive,
   * admin). Not a developer.
   */
  assistant: boolean;
  /**
   * The kinds Delivery's navigator lists, and so where Delivery links may point.
   * Under the `own` scope a listed pod may still be a name only: the tree says.
   */
  delivery: Record<NodeKind, boolean>;
  deliveryScope: DeliveryScope;
  signals: { views: SignalsView[]; defaultView: SignalsView; flowScope: Scope };
  coordination: {
    /** The requests board: everyone who may act on some card (not an executive). */
    board: boolean;
    boardScope: Scope;
    briefs: boolean;
    defaultBrief: BriefKind;
  };
  overall: {
    /** Delivery date and forecast, and the release picker: the project's progress readers. */
    forecast: boolean;
    /**
     * The Timeline and, under it, why a row's date moved: the forecast's readers
     * (every row) and whoever sets a pod's date (a scrum master: the pods, their
     * own whole, the others' dates only). The server words each row; "why it
     * moved" is drawn for a row the reader has whole. Not a developer.
     */
    timeline: boolean;
    requirements: boolean;
    risks: boolean;
    /** A link to Admin › Escalation, where the matrix is kept. */
    escalationLink: boolean;
  };
  /**
   * Release readiness on Overall: the people who decide on a release read it
   * (a scrum master their pods' rows, an executive without drafts); a developer
   * has nothing to act on, so it is not drawn. Each row's buttons follow the
   * server's `can`.
   */
  readiness: {
    board: boolean;
    /**
     * Every project's board: an admin, manager or product owner, and an
     * executive (who reads project progress). A scrum master reads only a project
     * one of their own pods works on: `readsReadiness` says which.
     */
    everyProject: boolean;
    /** `ACT_ON_READINESS`: Run check now and the row actions. */
    act: boolean;
  };
  /**
   * Jira hygiene on Overall: the release readiness board's own readers
   * (core/application/readiness_access.py). An executive reads each rule's
   * counts and never who, by the server's `counts_only`; a developer has no
   * section. Each finding's buttons follow the server's `can`.
   */
  hygiene: {
    board: boolean;
    everyProject: boolean;
    /** `ACT_ON_READINESS`: Check now, waive, re-check, reopen. */
    act: boolean;
  };
};

export type Lens = { lens: readonly AppRole[]; chatEnabled: boolean };

/** Every tab at every level: the readers of projects' lists. */
const EVERY_TAB: Record<HubLevel, readonly HubTab[]> = {
  all: HUB_TABS,
  project: HUB_TABS,
  pod: HUB_TABS,
};

/**
 * A developer's hub: the day reports of their own projects at the All level;
 * a project's questions, its gates and its day report; their own pod's dates
 * and its morning. No project's dates.
 */
const DEVELOPER_TABS: Record<HubLevel, readonly HubTab[]> = {
  all: ["reports"],
  project: ["warnings", "scope", "reports"],
  pod: ["date", "warnings"],
};

function hubAccess(lens: readonly AppRole[], can: Capabilities): HubAccess {
  const has = (...roles: AppRole[]) =>
    lens.includes("admin") || roles.some((role) => lens.includes(role));
  // Everyone but a developer reads every tab at every level, a scrum master
  // and a product owner the All level too, for their own projects.
  const reader = has("mgr", "exec", "po", "sm");
  const tabs = reader ? EVERY_TAB : DEVELOPER_TABS;
  const countsOnly = can.canReadPortfolio && !can.canReadPodDetail;
  return {
    tabs: { all: [...tabs.all], project: [...tabs.project], pod: [...tabs.pod] },
    allScope: has("mgr", "exec", "po") ? "every" : "own",
    countsOnly,
    podMinPeople: countsOnly ? 5 : 0,
    portfolioHealth: can.canReadPortfolio,
    bulk: reader,
  };
}

/**
 * The access map for a lens: the one role the API is told under local dev
 * sign-in, every held role under a real one. Roles combine: a product owner
 * who is also a scrum master is offered what either is.
 */
export function accessOf({ lens, chatEnabled }: Lens): Access {
  const can = capabilitiesFor(lens);
  const has = (...roles: AppRole[]) => roles.some((role) => lens.includes(role));
  const portfolio = can.canReadPortfolio;
  const scope: Scope = has("admin", "mgr")
    ? "all"
    : has("po")
      ? "projects"
      : has("sm")
        ? "pods"
        : "all";
  return {
    pages: {
      today: true,
      // Everyone's own check-in, read and corrected beside the conversation.
      checkin: true,
      // Whoever may act on some ask, and an executive, who reads the waits as counts.
      requests: has("admin", "mgr", "po", "sm", "exec"),
      // Everyone: the hub, at the levels and tabs `hub` gives the role.
      projects: true,
      flow: can.canReadAggregate,
      ora: can.canReadAggregate,
      // The pods' people and profiles, for whoever reads a pod's people. Not an
      // executive (no names) and not a developer (their pod's page has them).
      people: has("admin", "mgr", "po", "sm"),
      admin: can.canManageConfig,
    },
    hub: hubAccess(lens, can),
    checkin: { chat: chatEnabled },
    reports: { daily: has("admin", "mgr", "po", "sm", "dev") },
    assistant: can.canReadAggregate,
    delivery: portfolio
      ? {
          program: true,
          project: true,
          workstream: true,
          // An executive's pod panel would be closed apart from its date, which Overall shows.
          pod: can.canReadPodDetail,
        }
      : // Their projects and those projects' pods; the program is only a heading, and
        // workstreams are optional, so the pods carry the work.
        { program: false, project: true, workstream: false, pod: true },
    deliveryScope: portfolio ? "all" : "own",
    signals: {
      views: portfolio ? ["risks", "flow"] : can.canReadAggregate ? ["flow"] : [],
      defaultView: portfolio ? "risks" : "flow",
      flowScope: portfolio ? "all" : scope,
    },
    coordination: {
      board: has("admin", "mgr", "po", "sm"),
      boardScope: scope,
      briefs: can.canReadAggregate,
      defaultBrief: has("admin", "exec", "mgr")
        ? "exec"
        : has("po")
          ? "weekly_project"
          : "daily_pod",
    },
    overall: {
      forecast: can.canReadProjectProgress,
      timeline: can.canReadProjectProgress || has("admin", "mgr", "sm"),
      requirements: can.canReadProjectProgress,
      risks: can.canReadAggregate,
      escalationLink: can.canManageConfig,
    },
    readiness: {
      board: has("admin", "mgr", "po", "sm", "exec"),
      everyProject: has("admin", "mgr", "po") || can.canReadProjectProgress,
      act: has("admin", "mgr", "po", "sm"),
    },
    hygiene: {
      board: has("admin", "mgr", "po", "sm", "exec"),
      everyProject: has("admin", "mgr", "po") || can.canReadProjectProgress,
      act: has("admin", "mgr", "po", "sm"),
    },
  };
}

/**
 * Whether the person reads a project's release readiness board, by the backend's
 * rule (core/application/readiness_access.py `project_viewer`): the roles that read every
 * project's, and a scrum master for a project one of their own pods (one they
 * belong to or run) works on, as `ForecastService.runs_project_pod` decides it.
 * Their pods come from their own part of the tree; until it answers (`tree`
 * undefined) the board is not theirs, so a request the server refuses is never sent.
 */
export function readsReadiness(
  access: Pick<Access, "readiness">,
  projectId: string,
  tree: DeliveryTreeResponse | undefined,
): boolean {
  const { board, everyProject } = access.readiness;
  if (!board || projectId === "") return false;
  if (everyProject) return true;
  return (tree?.pods ?? []).some((pod) => pod.own && pod.parent_ids.includes(projectId));
}

/**
 * Whether the person reads a project's Jira hygiene board: the readiness board's
 * rule (`readsReadiness`), since the server keeps that rule for both.
 */
export function readsHygiene(
  access: Pick<Access, "hygiene">,
  projectId: string,
  tree: DeliveryTreeResponse | undefined,
): boolean {
  return readsReadiness({ readiness: access.hygiene }, projectId, tree);
}

/** Each page's first path segment: All projects (`/portfolio`) is Projects too. */
const PAGE_PATHS: Record<string, Page> = {
  today: "today",
  "check-in": "checkin",
  requests: "requests",
  projects: "projects",
  portfolio: "projects",
  flow: "flow",
  ora: "ora",
  people: "people",
  admin: "admin",
};

/** The sidebar page a path belongs to; null for an address no page owns (an old one). */
export function pageOf(pathname: string): Page | null {
  const first = pathname.split("/")[1] ?? "";
  return Object.prototype.hasOwnProperty.call(PAGE_PATHS, first) ? PAGE_PATHS[first] : null;
}

/** Each page's name in a sentence: the toast, the tab title. */
export const PAGE_LABELS: Record<Page, string> = {
  today: "Today",
  checkin: "Check-in",
  requests: "Requests",
  projects: "Projects",
  flow: "Review flow",
  ora: ASK_LABEL,
  people: "People & roles",
  admin: "Admin",
};

/** What a redirect needs of the directory: the projects of a pod, and of a workstream. */
export type DirectoryLinks = {
  podProjects: (podId: string) => string[];
  workstreamProjects: (workstreamId: string) => string[];
  /**
   * The person's own part of the tree: the projects they are on, for the pod a
   * link opens on, and for the roles whose pages list only it (`own`).
   */
  own?: OwnLinks | null;
};

export type Redirect = {
  /** Where to go instead, with `replace`. */
  to: string;
  /**
   * Set only when nothing on the role's own pages stands in for the link and it
   * lands on plain Today: the page the link asked for, for the one toast.
   */
  missing: Page | null;
};

type Where = { pathname: string; search: string; hash?: string };

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function split(path: string): { pathname: string; search: string; hash: string } {
  const [beforeHash, hash = ""] = path.split("#");
  const [pathname, query = ""] = beforeHash.split("?");
  return { pathname, search: query ? `?${query}` : "", hash: hash ? `#${hash}` : "" };
}

/** A query pair's key, decoded: `row=project%3Ax` is `row`. */
function keyOf(pair: string): string {
  const raw = pair.split("=")[0] ?? "";
  try {
    return decodeURIComponent(raw.replace(/\+/g, " "));
  } catch {
    return raw;
  }
}

/**
 * `target` with the link's other parameters and its hash kept, as written (no
 * re-encoding): the target's own parameters win, `drop` names the ones the old
 * address alone used, and the link's hash wins over the target's (an old
 * `…/overall?row=…#timeline` lands on the timeline, at the same row).
 */
function carried(
  target: string,
  search: string,
  hash: string | undefined,
  drop: readonly string[] = [],
): string {
  const own = split(target);
  const ownPairs = own.search.slice(1).split("&").filter(Boolean);
  const ownKeys = new Set(ownPairs.map(keyOf));
  const kept = search
    .replace(/^\?/, "")
    .split("&")
    .filter(Boolean)
    .filter((pair) => !ownKeys.has(keyOf(pair)) && !drop.includes(keyOf(pair)));
  const query = [...kept, ...ownPairs].join("&");
  const tail = hash && hash !== "#" ? hash : own.hash;
  return `${own.pathname}${query ? `?${query}` : ""}${tail}`;
}

/**
 * The project a pod's link opens on for this person: the first of the person's
 * own projects the pod works on, else the pod's first project. Null when the
 * directory names none.
 */
export function podProjectFor(podId: string, directory: DirectoryLinks): string | null {
  const projects = directory.podProjects(podId);
  const listed = directory.own?.pod(podId)?.projectIds ?? [];
  const own = directory.own?.projects ?? [];
  const all = [...projects, ...listed.filter((id) => !projects.includes(id))];
  return all.find((id) => own.includes(id)) ?? all[0] ?? null;
}

/** A project's day report for this role: its Reports, or for an executive its date. */
function dayReportOf(projectId: string, access: Pick<Access, "reports">): [string, string[]] {
  const place: HubPlace = { level: "project", projectId };
  return access.reports.daily
    ? [placePath(place, "reports"), []]
    : [placePath(place, "date"), ["report"]];
}

/**
 * An address from before the hub, where it lives now, with its query and
 * hash kept, since links in messages already sent carry them (a day report's
 * `/reports/<p>/daily?report=…`, the impact row's `/reports/<p>/overall?row=…#timeline`).
 * Null for an address that is current. One old address can lead to another,
 * which is followed until a current one is reached. The role's own rules then
 * apply to the result (`redirectFor`).
 *
 * | old                                  | new                                              |
 * | ------------------------------------ | ------------------------------------------------ |
 * | /portfolio/<not a tab>               | /portfolio (the role's first tab)                |
 * | /warnings, ?project=:p               | /portfolio/warnings, /projects/:p/warnings       |
 * | /scope, ?project=:p                  | /portfolio/scope, /projects/:p/scope             |
 * | /reports, /reports/briefs?brief=     | /portfolio/reports, /portfolio/reports?brief=    |
 * | /reports/:p/daily?report=            | /projects/:p/reports?report= (executive: /date)  |
 * | /reports/:p, /projects/:p/daily      | the same                                         |
 * | /reports/:p/overall, /projects/:p/overall | /projects/:p                                |
 * | /projects/:p#timeline, #stages, #work, ?row= | /projects/:p/date#…                      |
 * | /projects/:p#warnings, #questions    | /projects/:p/warnings#…                          |
 * | /projects/:p#scope, #gates           | /projects/:p/scope#…                             |
 * | /pods                                | /projects                                        |
 * | /pods/:x#checkins, #blockers, #status | /projects/<the viewer's of x, else its first>/pods/:x/warnings#… |
 * | /pods/:x                             | /projects/<the same>/pods/:x/date                |
 * | /delivery                            | /projects                                        |
 * | /delivery/program/:g                 | /portfolio/date?program=:g                       |
 * | /delivery/project/:p, /pod/:x        | /projects/:p, /pods/:x                           |
 * | /delivery/workstream/:w              | /projects/<its first>/date?workstream=:w#work    |
 * | /signals?view=risks (drift), flow    | /warnings, /flow                                 |
 * | /signals                             | /warnings, else /flow                            |
 * | /coordination?brief=                 | /reports/briefs?brief=                           |
 * | /coordination                        | /requests, else /reports/briefs                  |
 * | /chat                                | /check-in                                        |
 * | /today?pod=:x, /today?project=:p     | /pods/:x, /projects/:p                           |
 * | /risks, /ask                         | /warnings, /ora                                  |
 */
export function legacyPath(
  where: Where,
  access: Pick<Access, "pages" | "coordination" | "hub" | "reports">,
  directory: DirectoryLinks,
): string | null {
  let moved: string | null = null;
  let at: Where = where;
  // Each step moves an address one row of the table; a handful reaches any current one.
  for (let step = 0; step < 6; step += 1) {
    const next = legacyStep(at, access, directory);
    if (next === null) break;
    moved = next;
    at = split(next);
  }
  return moved;
}

function legacyStep(
  where: Where,
  access: Pick<Access, "pages" | "coordination" | "hub" | "reports">,
  directory: DirectoryLinks,
): string | null {
  const [first = "", second = "", third = ""] = where.pathname.split("/").slice(1);
  const params = new URLSearchParams(where.search);
  const to = (target: string, ...drop: string[]) => carried(target, where.search, where.hash, drop);
  const id = decode(third);

  switch (first) {
    case "portfolio": {
      const hub = parseHub(where.pathname);
      return hub && hub.rest !== null ? to(placePath({ level: "all" })) : null;
    }
    case "projects": {
      const hub = parseHub(where.pathname);
      if (!hub || hub.place.level === "all") return null;
      const { place } = hub;
      if (hub.rest !== null) {
        // The project's old day report and Overall, and anything else under it.
        if (place.level === "project" && hub.rest === "daily") {
          const [target, drop] = dayReportOf(place.projectId, access);
          return to(target, ...drop);
        }
        return to(placePath(place));
      }
      if (hub.tab !== null) return null;
      // No tab: an anchor sent before the tabs opens the tab it lives on, a picked row the date.
      const tab =
        tabOfAnchor(place.level, where.hash ?? "") ??
        (place.level === "project" && params.has("row") ? "date" : null);
      return tab ? to(placePath(place, tab)) : null;
    }
    case "pods": {
      if (!second) return to("/projects");
      const podId = decode(second);
      const projectId = podProjectFor(podId, directory);
      if (!projectId) return to("/projects");
      const tab = tabOfAnchor("pod", where.hash ?? "") ?? "date";
      return to(placePath({ level: "pod", projectId, podId }, tab));
    }
    case "warnings":
    case "scope": {
      const project = params.get("project");
      return project
        ? to(placePath({ level: "project", projectId: project }, first), "project")
        : to(placePath({ level: "all" }, first));
    }
    case "reports": {
      if (!second || second === "briefs") return to(placePath({ level: "all" }, "reports"));
      const projectId = decode(second);
      if (third === "overall") return to(projectPath(projectId));
      if (!third || third === "daily") {
        const [target, drop] = dayReportOf(projectId, access);
        return to(target, ...drop);
      }
      return null;
    }
    case "delivery": {
      if (second === "project" && id) return to(projectPath(id));
      if (second === "pod" && id) return to(podPath(id));
      if (second === "program" && id) return to(programPath(id));
      if (second === "workstream" && id) {
        const project = directory.workstreamProjects(id)[0];
        return to(project ? workstreamPath(id, [project]) : "/projects");
      }
      return to("/projects");
    }
    case "signals": {
      const view = params.get("view");
      if (view === "risks" || view === "drift") return to("/warnings", "view");
      if (view === "flow") return to("/flow", "view");
      return to(access.hub.tabs.all.includes("warnings") ? "/warnings" : "/flow", "view");
    }
    case "coordination": {
      if (params.get("brief") !== null) return to("/reports/briefs", "requests");
      if (access.pages.requests) return to("/requests");
      return to(access.coordination.briefs ? "/reports/briefs" : "/today#asks", "requests");
    }
    case "chat":
      return to("/check-in");
    case "today": {
      const pod = params.get("pod");
      const project = params.get("project");
      if (!pod && !project) return null;
      return to(pod ? podPath(pod) : projectPath(project ?? ""), "pod", "project");
    }
    case "risks":
      return to("/warnings");
    case "ask":
      return to("/ora");
    default:
      return null;
  }
}

/**
 * Where a link goes: an old address first where it lives now (`legacyPath`),
 * then, for a role the page, level or tab is not offered to, the closest the
 * role has, or null when the role is offered it as it is. The viewing day
 * (`asOf`) goes along.
 */
export function redirectFor(
  where: Where,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
): Redirect | null {
  const legacy = legacyPath(where, access, directory);
  if (legacy === null) return roleRedirect(where, access, can, directory);
  return roleRedirect(split(legacy), access, can, directory) ?? { to: legacy, missing: null };
}

function roleRedirect(
  where: Where,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
): Redirect | null {
  const hub = parseHub(where.pathname);
  if (hub) return hubRedirect(hub.place, hub.tab, where, access, can, directory);
  const page = pageOf(where.pathname);
  if (page === null) return null;
  const asOf = new URLSearchParams(where.search).get("asOf");
  const go = (to: string, missing: Page | null = null): Redirect => ({
    to: withAsOf(to, asOf),
    missing,
  });
  if (access.pages[page]) return null;
  switch (page) {
    case "requests":
      // Your asks on Today hold what a developer did here.
      return go("/today#asks");
    case "people":
      // A developer's pod's people are on their own pod's page.
      return can.canReadAggregate ? go("/today", page) : go("/projects");
    default:
      return go("/today", page);
  }
}

/**
 * A place of the hub: the tab asked for when the role has it there, else the
 * role's first tab at that level, the link's query and hash kept. A developer
 * reads their own projects and their own pod: another project opens on its day
 * report, which they read, and a pod they see by name only opens its project.
 * Until the person's tree is read, the page decides.
 */
function hubRedirect(
  place: HubPlace,
  tab: HubTab | null,
  where: Where,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
): Redirect | null {
  const keep = (to: string): Redirect => ({
    to: carried(to, where.search, where.hash),
    missing: null,
  });
  const tabs = access.hub.tabs[place.level];
  if (place.level !== "all" && access.deliveryScope === "own" && !can.canReadAggregate) {
    const own = directory.own;
    const project: HubPlace = { level: "project", projectId: place.projectId };
    if (own && !own.projects.includes(place.projectId)) {
      if (place.level === "pod" || tab !== "reports") return keep(placePath(project, "reports"));
      return null;
    }
    if (own && place.level === "pod" && !own.pod(place.podId)?.opens) {
      const projectTabs = access.hub.tabs.project;
      return keep(placePath(project, tab && projectTabs.includes(tab) ? tab : projectTabs[0]));
    }
  }
  if (tabs.length === 0) return keep("/projects");
  if (tab !== null && tabs.includes(tab)) return null;
  return keep(placePath(place, tabs[0]));
}

/**
 * What the guard must read before it can say where a link goes: the person's
 * tree (a hub link under the `own` scope, and any pod link, for the person's
 * own project of it), the pods (a pod link, for its projects), the workstreams
 * (an old workstream link).
 */
export function guardNeeds(
  where: Where,
  access: Pick<Access, "deliveryScope" | "pages">,
): { tree: boolean; pods: boolean; workstreams: boolean } {
  const [first = "", second = "", third = ""] = where.pathname.split("/").slice(1);
  const params = new URLSearchParams(where.search);
  const hub = parseHub(where.pathname);
  const podLink =
    (first === "pods" && second !== "") ||
    (first === "delivery" && second === "pod" && third !== "") ||
    (first === "today" && params.get("pod") !== null);
  const projectLink =
    (hub !== null && hub.place.level !== "all") ||
    (first === "delivery" && second === "project" && third !== "") ||
    (first === "today" && params.get("project") !== null) ||
    (first === "reports" && second !== "" && second !== "briefs");
  const own = access.deliveryScope === "own";
  return {
    tree: podLink || (own && projectLink),
    pods: podLink,
    workstreams: first === "delivery" && second === "workstream" && third !== "",
  };
}

/** `to` with the viewing day kept, before any `#`. */
function withAsOf(to: string, asOf: string | null): string {
  if (!asOf) return to;
  const [path, hash] = to.split("#");
  const [base, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  params.set("asOf", asOf);
  return `${base}?${params.toString()}${hash !== undefined ? `#${hash}` : ""}`;
}

/** "Opened Today: Review flow isn't part of the developer view." */
export function redirectToast(page: Page, roleLabel: string): string {
  return `Opened Today: ${PAGE_LABELS[page]} isn't part of the ${roleLabel.toLowerCase()} view.`;
}

/**
 * Another role the person holds that is offered the page, for the toast's
 * Switch action (local dev sign-in only, where one role at a time is told to
 * the API). Most senior first; null when none of them is.
 */
export function roleOfferingPage(
  page: Page,
  held: readonly AppRole[],
  current: AppRole,
  chatEnabled: boolean,
  priority: readonly AppRole[],
): AppRole | null {
  return (
    priority.find(
      (role) =>
        role !== current &&
        held.includes(role) &&
        accessOf({ lens: [role], chatEnabled }).pages[page],
    ) ?? null
  );
}

/**
 * Where a palette row of each kind goes for this role, or null when the role
 * gets no such rows: a project its hub page, a pod its part of a project (the
 * first project given), a program its heading on All projects' date, a
 * workstream its row in its first project's date. Under the `own` scope the
 * palette lists only the person's part of the tree, and of it only what opens
 * (features/delivery/ownTree.ts). A counts-only reader gets no pod rows: the
 * pods that open for them are the ones of five or more, which only the
 * project's pod bar knows.
 */
export type PaletteTargets = {
  program: ((id: string) => string) | null;
  project: ((id: string) => string) | null;
  workstream: ((id: string) => string) | null;
  pod: ((id: string, projectIds?: readonly string[]) => string) | null;
  /** A person opens one of their pods: the pod's target, when people are offered at all. */
  person: ((podId: string, projectIds?: readonly string[]) => string) | null;
};

export function paletteTargets(
  access: Access,
  workstreamProjects: (workstreamId: string) => string[] = () => [],
): PaletteTargets {
  const own = access.deliveryScope === "own";
  const pod = access.hub.countsOnly
    ? null
    : (id: string, projectIds: readonly string[] = []) => podPath(id, undefined, projectIds[0]);
  return {
    program: access.hub.tabs.all.includes("date") && !own ? (id: string) => programPath(id) : null,
    project: (id: string) => projectPath(id),
    workstream: own ? null : (id: string) => workstreamPath(id, workstreamProjects(id)),
    pod,
    person: pod,
  };
}
