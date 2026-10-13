// What each role is offered, in one place: the sidebar's pages (D85), the
// sections inside them, where the ⌘K palette takes a row, where an address from
// before the sidebar goes now, and where a link to a page the role is not
// offered goes instead. A page or section the role is not offered is not drawn
// at all: no struck-through item, no line saying which role would open it. The
// flags only forecast the backend, which still decides; a 403 that comes back
// anyway shows the server's reason.
//
// Type imports, and runtime imports by their .ts path, so `node --test` runs it.
import type { DeliveryTreeResponse } from "../api/schema";
import { ASK_LABEL } from "../features/assistant/persona.ts";
import type { OwnLinks } from "../features/delivery/ownTree";
import { dailyPath, podPath, programPath, projectPath, workstreamPath } from "./links.ts";
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
 * The sidebar's pages (D85), in its order: Daily (Today, Check-in, Requests),
 * Delivery (the Portfolio, Projects, Pods, Early warnings, Scope control),
 * Insight (Reports, Review flow, Ask Ora) and Setup (People & roles, Admin).
 */
export type Page =
  | "today"
  | "checkin"
  | "requests"
  | "portfolio"
  | "projects"
  | "pods"
  | "warnings"
  | "scope"
  | "reports"
  | "flow"
  | "ora"
  | "people"
  | "admin";
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

export type Access = {
  /** The sidebar's pages the role is offered (perspective-roles §1, D85). */
  pages: Record<Page, boolean>;
  /** Check-in: the built-in chat's thread beside the form, on a local tenant only. */
  checkin: { chat: boolean };
  /**
   * Reports: `daily` is each project's day report, for every role but an
   * executive on their own (D103: it is an action list that names each owner;
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
      // Whoever may act on some ask; an executive reads the waits as counts on Today.
      requests: has("admin", "mgr", "po", "sm"),
      // The dates of every project: the project's progress readers (`READ_PROJECT_PROGRESS`).
      portfolio: can.canReadProjectProgress,
      // Everyone: the portfolio's readers any project, everyone else their own part
      // (a developer the name-only form), the server deciding each section.
      projects: true,
      // An executive's pod would be closed apart from its dates, which the project shows.
      pods: portfolio ? can.canReadPodDetail : true,
      // Risks and drift across projects; a scrum master's and a product owner's are a
      // section of their project's page.
      warnings: portfolio,
      // The planning baseline and Jira hygiene: the readiness board's readers.
      scope: has("admin", "mgr", "po", "sm", "exec"),
      reports: true,
      flow: can.canReadAggregate,
      ora: can.canReadAggregate,
      // The pods' people and profiles, for whoever reads a pod's people. Not an
      // executive (D87: no names) and not a developer (their pod's page has them).
      people: has("admin", "mgr", "po", "sm"),
      admin: can.canManageConfig,
    },
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

/** Each page's first path segment. */
const PAGE_PATHS: Record<string, Page> = {
  today: "today",
  "check-in": "checkin",
  requests: "requests",
  portfolio: "portfolio",
  projects: "projects",
  pods: "pods",
  warnings: "warnings",
  scope: "scope",
  reports: "reports",
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
  portfolio: "Portfolio",
  projects: "Projects",
  pods: "Pods",
  warnings: "Early warnings",
  scope: "Scope control",
  reports: "Reports",
  flow: "Review flow",
  ora: ASK_LABEL,
  people: "People & roles",
  admin: "Admin",
};

/** What a redirect needs of the directory: the projects of a pod, and of a workstream. */
export type DirectoryLinks = {
  podProjects: (podId: string) => string[];
  workstreamProjects: (workstreamId: string) => string[];
  /** The person's own part of the tree, for the roles whose pages list only it (`own`). */
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
 * An address from before the sidebar (D85), where it lives now, with its query
 * and hash kept, since links in messages already sent carry them (a day
 * report's, a readiness line's, the impact row's `/reports/<p>/overall?row=…#timeline`).
 * Null for an address that is current. The role's own rules then apply to the
 * result (`redirectFor`).
 *
 * | old                              | new                                   |
 * | -------------------------------- | ------------------------------------- |
 * | /delivery                        | /portfolio, else /projects            |
 * | /delivery/program/:id            | /portfolio?program=:id, else /projects |
 * | /delivery/project/:id            | /projects/:id                         |
 * | /delivery/pod/:id                | /pods/:id                             |
 * | /delivery/workstream/:id         | /projects/<its first project>#work    |
 * | /reports/:id/overall             | /projects/:id                         |
 * | /reports/:id                     | /reports/:id/daily                    |
 * | /signals?view=risks (drift)      | /warnings                             |
 * | /signals?view=flow               | /flow                                 |
 * | /signals                         | /warnings, else /flow                 |
 * | /coordination?brief=             | /reports/briefs?brief=                |
 * | /coordination                    | /requests, else /reports/briefs       |
 * | /chat                            | /check-in                             |
 * | /today?pod=:id, /today?project=  | /pods/:id, /projects/:id              |
 * | /projects/:id/daily, …/overall   | /reports/:id/daily, /projects/:id     |
 * | /risks, /ask                     | /warnings, /ora                       |
 */
export function legacyPath(
  where: Where,
  access: Pick<Access, "pages" | "coordination">,
  directory: Pick<DirectoryLinks, "workstreamProjects">,
): string | null {
  const [first = "", second = "", third = ""] = where.pathname.split("/").slice(1);
  const params = new URLSearchParams(where.search);
  const to = (target: string, ...drop: string[]) => carried(target, where.search, where.hash, drop);
  const id = decode(third);

  switch (first) {
    case "delivery": {
      if (second === "project" && id) return to(projectPath(id));
      if (second === "pod" && id) return to(podPath(id));
      if (second === "program" && id) {
        return to(access.pages.portfolio ? programPath(id) : "/projects");
      }
      if (second === "workstream" && id) {
        const project = directory.workstreamProjects(id)[0];
        return to(project ? workstreamPath(id, [project]) : "/projects");
      }
      return to(access.pages.portfolio ? "/portfolio" : "/projects");
    }
    case "reports": {
      if (!second || second === "briefs") return null;
      const projectId = decode(second);
      if (third === "overall") return to(projectPath(projectId));
      if (!third) return to(dailyPath(projectId));
      return null;
    }
    case "signals": {
      const view = params.get("view");
      if (view === "risks" || view === "drift") return to("/warnings", "view");
      if (view === "flow") return to("/flow", "view");
      return to(access.pages.warnings ? "/warnings" : "/flow", "view");
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
    case "projects": {
      if (!second || !third) return null;
      const projectId = decode(second);
      return to(third === "daily" ? dailyPath(projectId) : projectPath(projectId));
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
 * then, for a role the page is not offered to, the closest page the role has,
 * or null when the role is offered it as it is. The viewing day (`asOf`) goes
 * along.
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
  const page = pageOf(where.pathname);
  if (page === null) return null;
  const asOf = new URLSearchParams(where.search).get("asOf");
  const go = (to: string, missing: Page | null = null): Redirect => ({
    to: withAsOf(to, asOf),
    missing,
  });
  const today = (missing: Page) => go("/today", missing);
  const id = decode(where.pathname.split("/")[2] ?? "");
  const offered = access.pages[page];

  switch (page) {
    case "today":
    case "checkin":
      return null;
    case "reports":
      if (!access.reports.daily) {
        // No day report for an executive (D103): a project's opens its Project page,
        // as a sent report's link did when it was Overall; Reports opens on the briefs.
        if (id === "briefs") return null;
        return go(id ? projectPath(id) : "/reports/briefs");
      }
      // Briefs are the aggregate readers'; a developer's Reports is the day reports.
      return id === "briefs" && !access.coordination.briefs ? go("/reports") : null;
    case "projects":
      return id ? projectRedirect(id, access, can, directory, go) : null;
    case "pods":
      return podRedirect(id, access, can, directory, go, today);
    case "requests":
      if (offered) return null;
      // Your asks on Today hold what a developer did here.
      return can.canReadAggregate ? today(page) : go("/today#asks");
    case "portfolio":
      if (offered) return null;
      // A scrum master's dates are their pods', on their project's page.
      return can.canReadAggregate ? go("/projects") : today(page);
    case "warnings":
      if (offered) return null;
      // A scrum master's and a product owner's warnings are a section of their project.
      return can.canReadAggregate ? go("/projects#warnings") : today(page);
    case "people":
      if (offered) return null;
      // A developer's pod's people are on their pod's page.
      return access.pages.pods && !can.canReadAggregate ? go("/pods") : today(page);
    default:
      return offered ? null : today(page);
  }
}

/**
 * A project's page outside the person's own part of the tree. A product owner
 * reads every project, a scrum master the sections the server gives them (as
 * Overall did): both are shown. A developer's own projects open on the
 * name-only form; another project goes to its day report, which they read.
 * Until the tree is read, the page decides.
 */
function projectRedirect(
  id: string,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
  go: (to: string) => Redirect,
): Redirect | null {
  if (access.deliveryScope !== "own" || can.canReadAggregate) return null;
  const own = directory.own;
  if (!own || own.projects.includes(id)) return null;
  return go(dailyPath(id));
}

/**
 * A pod's page. An executive has no Pods: a pod's dates are on its project's
 * timeline. Under the `own` scope: a pod that opens is shown; one listed by name
 * only (a developer's neighbours) opens its project; outside the tree a scrum
 * master's is the server's to decide, a product owner gets its project, and a
 * developer their own pod.
 */
function podRedirect(
  id: string,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
  go: (to: string, missing?: Page | null) => Redirect,
  today: (missing: Page) => Redirect,
): Redirect | null {
  if (!access.pages.pods) {
    if (!id) return today("pods");
    const project = directory.podProjects(id)[0];
    return go(project ? projectPath(project) : "/projects");
  }
  if (!id || access.deliveryScope !== "own") return null;
  const own = directory.own;
  if (!own) return null;
  const pod = own.pod(id);
  if (pod?.opens) return null;
  const ownProject = pod?.projectIds.find((projectId) => own.projects.includes(projectId));
  if (ownProject) return go(projectPath(ownProject));
  if (can.canReadPodDetail) return null;
  const theirs = directory.podProjects(id)[0];
  if (can.canReadProjectProgress && theirs) return go(projectPath(theirs));
  return go("/pods");
}

/**
 * What the guard must read before it can say where a link goes: the person's
 * tree (a project or pod link under the `own` scope), the pods (a pod link for
 * an executive, or outside the tree), the workstreams (an old workstream link).
 */
export function guardNeeds(
  where: Where,
  access: Pick<Access, "deliveryScope" | "pages">,
): { tree: boolean; pods: boolean; workstreams: boolean } {
  const [first = "", second = "", third = ""] = where.pathname.split("/").slice(1);
  const params = new URLSearchParams(where.search);
  const pod =
    (first === "pods" && second !== "") ||
    (first === "delivery" && second === "pod" && third !== "") ||
    (first === "today" && params.get("pod") !== null);
  const project =
    (first === "projects" && second !== "") ||
    (first === "delivery" && second === "project" && third !== "") ||
    (first === "today" && params.get("project") !== null) ||
    (first === "reports" && third === "overall");
  const own = access.deliveryScope === "own";
  return {
    tree: own && (pod || project),
    pods: pod && (own || !access.pages.pods),
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
 * gets no such rows: a project its page, a pod its page for the roles with
 * Pods, a program its heading on the Portfolio, a workstream its first
 * project's Work. Under the `own` scope the palette lists only the person's
 * part of the tree, and of it only what opens (features/delivery/ownTree.ts).
 */
export type PaletteTargets = {
  program: ((id: string) => string) | null;
  project: ((id: string) => string) | null;
  workstream: ((id: string) => string) | null;
  pod: ((id: string) => string) | null;
  /** A person opens one of their pods: the pod's target, when people are offered at all. */
  person: ((podId: string) => string) | null;
};

export function paletteTargets(
  access: Access,
  workstreamProjects: (workstreamId: string) => string[] = () => [],
): PaletteTargets {
  const own = access.deliveryScope === "own";
  const pod = access.pages.pods ? (id: string) => podPath(id) : null;
  return {
    program: access.pages.portfolio && !own ? (id: string) => programPath(id) : null,
    project: (id: string) => projectPath(id),
    workstream: own ? null : (id: string) => workstreamPath(id, workstreamProjects(id)),
    pod,
    person: pod,
  };
}
