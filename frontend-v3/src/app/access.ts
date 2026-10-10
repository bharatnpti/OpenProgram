// What each role is offered, in one place: the tabs, what Delivery lists, the
// sections inside the screens, where the ⌘K palette takes a row, and where a
// link to a page the role is not offered goes instead. A screen or section the
// role is not offered is not drawn at all: no struck-through tab, no line
// saying which role would open it. The flags only forecast the backend, which
// still decides; a 403 that comes back anyway shows the server's reason.
//
// Type imports only, so `node --test` runs it as written.
import type { OwnLinks } from "../features/delivery/ownTree";
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
  };
}

export type Page = "today" | "delivery" | "signals" | "coordination" | "reports" | "chat" | "admin";
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
  /** The tabs the role is offered. */
  pages: Record<Page, boolean>;
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
    /** Dates by pod: anyone who reads or sets a pod's date. */
    podDates: boolean;
    requirements: boolean;
    risks: boolean;
    /** A link to Admin › Escalation, where the matrix is kept. */
    escalationLink: boolean;
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
      // Everyone: the portfolio's readers walk all of it, everyone else their own part.
      delivery: true,
      signals: can.canReadAggregate,
      coordination: can.canReadAggregate,
      reports: true,
      chat: chatEnabled,
      admin: can.canManageConfig,
    },
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
      podDates: can.canReadProjectProgress || has("admin", "mgr", "sm"),
      requirements: can.canReadProjectProgress,
      risks: can.canReadAggregate,
      escalationLink: can.canManageConfig,
    },
  };
}

/** The page a path belongs to; null for a page every role has (Today, Reports). */
export function pageOf(pathname: string): Page | null {
  const first = pathname.split("/")[1] ?? "";
  if (first === "delivery") return "delivery";
  if (first === "signals") return "signals";
  if (first === "coordination") return "coordination";
  if (first === "chat") return "chat";
  if (first === "admin") return "admin";
  return null;
}

export const PAGE_LABELS: Record<Page, string> = {
  today: "Today",
  delivery: "Delivery",
  signals: "Signals",
  coordination: "Coordination",
  reports: "Reports",
  chat: "Chat",
  admin: "Admin",
};

/** What a redirect needs of the directory: the projects of a pod, and of a workstream. */
export type DirectoryLinks = {
  podProjects: (podId: string) => string[];
  workstreamProjects: (workstreamId: string) => string[];
  /** The person's own part of the tree, for a Delivery that lists only it (`own`). */
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

/**
 * Where a link goes for a role the page is not offered to: the closest page the
 * role has (PROPOSAL §7.2), or null when the role is offered it as it is. The
 * viewing day (`asOf`) goes along.
 */
export function redirectFor(
  where: Where,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
): Redirect | null {
  const page = pageOf(where.pathname);
  const params = new URLSearchParams(where.search);
  const asOf = params.get("asOf");
  const go = (to: string, missing: Page | null = null): Redirect => ({
    to: withAsOf(to, asOf),
    missing,
  });
  const today = (missing: Page) => go("/today", missing);

  if (page === "delivery") return deliveryRedirect(where.pathname, access, can, directory, go);
  if (page === "signals") {
    if (!access.pages.signals) return today("signals");
    const view = params.get("view");
    if (view === null || (access.signals.views as string[]).includes(view)) return null;
    // Drift joined the risks list; the other views are gone.
    const next =
      view === "drift" && access.signals.views.includes("risks")
        ? "risks"
        : view === "risks" || view === "drift"
          ? access.signals.defaultView
          : null;
    if (next === null) params.delete("view");
    else params.set("view", next);
    params.delete("asOf");
    const query = params.toString();
    return go(`/signals${query ? `?${query}` : ""}`);
  }
  if (page === "coordination") {
    // Your asks on Today hold what a developer did here.
    return access.pages.coordination ? null : go("/today#asks");
  }
  if (page === "admin") return access.pages.admin ? null : today("admin");
  if (page === "chat") return access.pages.chat ? null : today("chat");
  return null;
}

function deliveryRedirect(
  pathname: string,
  access: Access,
  can: Capabilities,
  directory: DirectoryLinks,
  go: (to: string, missing?: Page | null) => Redirect,
): Redirect | null {
  const [, , kind, rawId] = pathname.split("/");
  const id = rawId ? decodeURIComponent(rawId) : "";
  const known = kind === "program" || kind === "project" || kind === "workstream" || kind === "pod";
  if (!access.pages.delivery) return go("/today", "delivery");
  if (access.deliveryScope === "own") {
    // The guard reads the person's tree before it asks; without it, the page decides.
    if (!known || !id || !directory.own) return null;
    return ownRedirect(kind, id, can, directory, directory.own, go);
  }
  if (!known || !id || access.delivery[kind]) return null;
  // Delivery is offered, but not this kind (an executive and a pod): its project instead.
  const project =
    kind === "pod" ? directory.podProjects(id)[0] : directory.workstreamProjects(id)[0];
  return go(project ? `/delivery/project/${encodeURIComponent(project)}` : "/delivery");
}

/**
 * A Delivery link for a person whose Delivery lists their own part of the tree.
 * Inside it, the page; a pod listed by name only, its project; a program, their
 * Delivery; a workstream, its project when that is theirs. Outside it, the
 * closest page the role has elsewhere: a scrum master's Today on the pod, a
 * product owner's Today on the project, a project's reports, else their
 * Delivery. Never plain Today with a note: the role has Delivery.
 */
function ownRedirect(
  kind: NodeKind,
  id: string,
  can: Capabilities,
  directory: DirectoryLinks,
  own: OwnLinks,
  go: (to: string, missing?: Page | null) => Redirect,
): Redirect | null {
  const enc = encodeURIComponent;
  const ownProject = (ids: string[]) => ids.find((projectId) => own.projects.includes(projectId));
  if (kind === "project") {
    if (own.projects.includes(id)) return null;
    if (can.canReadProjectProgress) return go(`/today?project=${enc(id)}`);
    return go(`/reports/${enc(id)}/${can.canReadAggregate ? "overall" : "daily"}`);
  }
  if (kind === "pod") {
    const pod = own.pod(id);
    if (pod?.opens) return null;
    const project = pod ? ownProject(pod.projectIds) : undefined;
    if (project) return go(`/delivery/project/${enc(project)}`);
    if (can.canReadPodDetail) return go(`/today?pod=${enc(id)}`);
    const theirs = directory.podProjects(id)[0];
    if (can.canReadProjectProgress && theirs) return go(`/today?project=${enc(theirs)}`);
    return go("/delivery");
  }
  if (kind === "workstream") {
    const projects = directory.workstreamProjects(id);
    const project = ownProject(projects);
    if (project) return go(`/delivery/project/${enc(project)}`);
    if (can.canReadProjectProgress && projects[0]) return go(`/today?project=${enc(projects[0])}`);
  }
  return go("/delivery");
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

/** "Opened Today: Signals isn't part of the developer view." */
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
 * gets no such rows: the Delivery panel of each kind its Delivery lists. Under
 * the `own` scope the palette lists only the person's part of the tree, and of
 * it only what opens (features/delivery/ownTree.ts).
 */
export type PaletteTargets = {
  program: ((id: string) => string) | null;
  project: ((id: string) => string) | null;
  workstream: ((id: string) => string) | null;
  pod: ((id: string) => string) | null;
  /** A person opens one of their pods: the pod's target, when people are offered at all. */
  person: ((podId: string) => string) | null;
};

export function paletteTargets(access: Access): PaletteTargets {
  const delivery = (kind: NodeKind) =>
    access.pages.delivery && access.delivery[kind]
      ? (id: string) => `/delivery/${kind}/${encodeURIComponent(id)}`
      : null;
  const pod = delivery("pod");
  return {
    program: delivery("program"),
    project: delivery("project"),
    workstream: delivery("workstream"),
    pod,
    person: pod,
  };
}
