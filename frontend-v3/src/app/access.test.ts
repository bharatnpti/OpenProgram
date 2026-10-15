import assert from "node:assert/strict";
import { test } from "node:test";

import {
  accessOf,
  capabilitiesFor,
  guardNeeds,
  legacyPath,
  pageOf,
  paletteTargets,
  podProjectFor,
  readsHygiene,
  readsReadiness,
  redirectFor,
  redirectToast,
  roleOfferingPage,
  type Page,
} from "./access.ts";
import { developerTree, scrumMasterTree } from "../features/delivery/ownTree.fixture.ts";
import type { OwnLinks } from "../features/delivery/ownTree.ts";
import { shownGroups } from "./nav.ts";
import { rolePriority, type AppRole } from "./roleWords.ts";

const access = (role: AppRole, chatEnabled = false) => accessOf({ lens: [role], chatEnabled });
const pages = (role: AppRole) =>
  (Object.entries(access(role).pages) as [Page, boolean][])
    .filter(([, on]) => on)
    .map(([page]) => page);

test("each role is offered the sidebar's pages, in the sidebar's order", () => {
  assert.deepEqual(pages("dev"), ["today", "checkin", "projects"]);
  for (const role of ["sm", "po", "mgr"] as const) {
    assert.deepEqual(
      pages(role),
      ["today", "checkin", "requests", "projects", "flow", "ora", "people"],
      role,
    );
  }
  // No names for an executive: Requests as counts, no People.
  assert.deepEqual(pages("exec"), ["today", "checkin", "requests", "projects", "flow", "ora"]);
  assert.equal(pages("admin").length, 8);
  // The built-in chat is a column of Check-in, never a page of its own.
  assert.deepEqual(access("dev", true).pages, access("dev").pages);
  assert.equal(access("dev", true).checkin.chat, true);
  assert.equal(access("exec").checkin.chat, false);
});

test("the sidebar's groups hold only offered pages, and Delivery is the one item Projects", () => {
  const groups = (role: AppRole) =>
    shownGroups(access(role)).map((group) => [group.label, group.items.map((item) => item.label)]);
  assert.deepEqual(groups("dev"), [
    ["Daily", ["Today", "Check-in"]],
    ["Delivery", ["Projects"]],
  ]);
  assert.deepEqual(groups("exec"), [
    ["Daily", ["Today", "Check-in", "Requests"]],
    ["Delivery", ["Projects"]],
    ["Insight", ["Review flow", "Ask Ora"]],
  ]);
  assert.deepEqual(groups("admin"), [
    ["Daily", ["Today", "Check-in", "Requests"]],
    ["Delivery", ["Projects"]],
    ["Insight", ["Review flow", "Ask Ora"]],
    ["Setup", ["People & roles", "Admin"]],
  ]);
  // The nav is built from the access map alone: it never grows with the projects.
  const lengths = (["dev", "sm", "po", "mgr", "exec", "admin"] as const).map(
    (role) => shownGroups(access(role)).flatMap((group) => group.items).length,
  );
  assert.deepEqual(lengths, [3, 7, 7, 7, 6, 8]);
});

test("the hub: every tab at every level for the readers of project lists", () => {
  const every = { all: [...TABS], project: [...TABS], pod: [...TABS] };
  for (const role of ["sm", "po", "mgr", "exec", "admin"] as const) {
    assert.deepEqual(access(role).hub.tabs, every, role);
    assert.equal(access(role).hub.bulk, true, role);
  }
  // A developer: their own projects' day reports; a project's questions, gates and
  // day report; their own pod's dates and morning. No project's dates.
  assert.deepEqual(access("dev").hub.tabs, {
    all: ["reports"],
    project: ["warnings", "scope", "reports"],
    pod: ["date", "warnings"],
  });
  assert.equal(access("dev").hub.bulk, false);
  // Roles combine: a developer who is also a scrum master reads every tab.
  assert.deepEqual(accessOf({ lens: ["dev", "sm"], chatEnabled: false }).hub.tabs, every);
});

test("the hub: a scrum master's All level is their pods' projects; names are kept from an executive", () => {
  assert.deepEqual(
    (["dev", "sm", "po", "mgr", "exec", "admin"] as const).map((role) => access(role).hub.allScope),
    ["own", "own", "every", "every", "every", "every"],
  );
  assert.deepEqual(
    (["dev", "sm", "po", "mgr", "exec", "admin"] as const).map((role) => [
      access(role).hub.countsOnly,
      access(role).hub.podMinPeople,
      access(role).hub.portfolioHealth,
    ]),
    [
      [false, 0, false],
      [false, 0, false],
      [false, 0, false],
      [false, 0, true],
      // An executive opens only the pods of five people or more.
      [true, 5, true],
      [false, 0, true],
    ],
  );
  // An executive who also manages reads names.
  assert.equal(accessOf({ lens: ["exec", "mgr"], chatEnabled: false }).hub.countsOnly, false);
});

test("an executive's Delivery lists no pods; a manager's and an admin's list every kind", () => {
  assert.deepEqual(access("exec").delivery, {
    program: true,
    project: true,
    workstream: true,
    pod: false,
  });
  for (const role of ["mgr", "admin"] as const) {
    assert.ok(Object.values(access(role).delivery).every(Boolean), role);
  }
  for (const role of ["mgr", "exec", "admin"] as const) {
    assert.equal(access(role).deliveryScope, "all", role);
  }
});

test("everyone else's Delivery lists their own projects and those projects' pods", () => {
  for (const role of ["dev", "sm", "po"] as const) {
    assert.equal(access(role).deliveryScope, "own", role);
    // The program is only a heading; workstreams are optional, so pods carry the work.
    assert.deepEqual(
      access(role).delivery,
      { program: false, project: true, workstream: false, pod: true },
      role,
    );
  }
  // Roles combine: a scrum master who is also a manager walks all of it.
  assert.equal(accessOf({ lens: ["sm", "mgr"], chatEnabled: false }).deliveryScope, "all");
  // The capabilities stay the role's everywhere; a scrum master's own projects
  // and a developer's own pod come from the person's tree, node by node.
  assert.equal(capabilitiesFor(["sm"]).canReadProjectProgress, false);
  assert.equal(capabilitiesFor(["dev"]).canReadPodDetail, false);
});

test("Signals is Flow for a scrum master and a product owner, Risks first for the portfolio", () => {
  assert.deepEqual(access("sm").signals, {
    views: ["flow"],
    defaultView: "flow",
    flowScope: "pods",
  });
  assert.deepEqual(access("po").signals, {
    views: ["flow"],
    defaultView: "flow",
    flowScope: "projects",
  });
  for (const role of ["mgr", "exec", "admin"] as const) {
    assert.deepEqual(access(role).signals, {
      views: ["risks", "flow"],
      defaultView: "risks",
      flowScope: "all",
    });
  }
  assert.deepEqual(access("dev").signals.views, []);
});

test("Coordination: no board for an executive, and each role's own scope and brief", () => {
  const c = (role: AppRole) => access(role).coordination;
  assert.equal(c("exec").board, false);
  assert.equal(c("exec").briefs, true);
  assert.deepEqual(
    (["sm", "po", "mgr", "admin"] as const).map((role) => [
      c(role).boardScope,
      c(role).defaultBrief,
    ]),
    [
      ["pods", "daily_pod"],
      ["projects", "weekly_project"],
      ["all", "exec"],
      ["all", "exec"],
    ],
  );
  assert.equal(c("exec").defaultBrief, "exec");
});

test("Overall: a developer has only gates and questions; escalation is a link for an admin", () => {
  assert.deepEqual(access("dev").overall, {
    forecast: false,
    timeline: false,
    requirements: false,
    risks: false,
    escalationLink: false,
  });
  assert.deepEqual(access("sm").overall, {
    forecast: false,
    timeline: true,
    requirements: false,
    risks: true,
    escalationLink: false,
  });
  assert.equal(access("exec").overall.forecast, true);
  assert.equal(access("admin").overall.escalationLink, true);
  assert.equal(access("mgr").overall.escalationLink, false);
});

test("held roles combine under a real sign-in: a product owner and scrum master gets both", () => {
  const both = accessOf({ lens: ["po", "sm"], chatEnabled: false });
  assert.equal(both.coordination.boardScope, "projects");
  assert.equal(both.overall.timeline, true);
  assert.equal(both.hub.allScope, "every");
  assert.equal(capabilitiesFor(["po", "sm"]).canReadPodDetail, true);
});

test("pages by path: All projects is Projects; an old address belongs to no page", () => {
  assert.equal(pageOf("/today"), "today");
  assert.equal(pageOf("/check-in"), "checkin");
  assert.equal(pageOf("/projects"), "projects");
  assert.equal(pageOf("/projects/project-checkout/warnings"), "projects");
  assert.equal(pageOf("/portfolio/date"), "projects");
  assert.equal(pageOf("/admin"), "admin");
  for (const old of [
    "/pods/pod-payments",
    "/warnings",
    "/scope",
    "/reports",
    "/reports/briefs",
    "/delivery/pod/pod-payments",
    "/signals",
    "/coordination",
    "/chat",
    "/",
  ]) {
    assert.equal(pageOf(old), null, old);
  }
  assert.equal(pageOf("/constructor"), null, "no inherited key is a page");
});

const TABS = ["date", "warnings", "scope", "reports"] as const;

const directory = {
  podProjects: (id: string) =>
    id === "pod-payments" || id === "pod-storefront"
      ? ["project-checkout"]
      : id === "pod-shared"
        ? ["project-insights", "project-identity"]
        : id === "pod-data"
          ? ["project-insights"]
          : [],
  workstreamProjects: (id: string) =>
    id === "ws-payments" ? ["project-checkout"] : id === "ws-datalake" ? ["project-insights"] : [],
};

/**
 * Each person's own part of the tree, as `GET /me/delivery-tree` gives it on the
 * demo seed: Kai (developer) and Mina (product owner) in the Payments Pod, Ira
 * (scrum master) running Payments and Identity. A pod that only lists its name
 * (Storefront for Kai) does not open.
 */
const pod = (opens: boolean, ...projectIds: string[]) => ({ opens, projectIds });
const OWN: Partial<Record<AppRole, OwnLinks>> = {
  dev: {
    projects: ["project-checkout"],
    pod: (id) =>
      ({
        "pod-payments": pod(true, "project-checkout"),
        "pod-storefront": pod(false, "project-checkout"),
      })[id] ?? null,
  },
  sm: {
    projects: ["project-checkout", "project-identity"],
    pod: (id) =>
      ({
        "pod-payments": pod(true, "project-checkout"),
        "pod-storefront": pod(true, "project-checkout"),
        "pod-identity": pod(true, "project-identity"),
      })[id] ?? null,
  },
  po: {
    projects: ["project-checkout"],
    pod: (id) =>
      ({
        "pod-payments": pod(true, "project-checkout"),
        "pod-storefront": pod(true, "project-checkout"),
      })[id] ?? null,
  },
};
const where = (link: string) => {
  const [beforeHash, hash] = link.split("#");
  const [pathname, search = ""] = beforeHash.split("?");
  return { pathname, search: search ? `?${search}` : "", hash: hash ? `#${hash}` : "" };
};
const go = (role: AppRole, link: string) =>
  redirectFor(where(link), access(role), capabilitiesFor([role]), {
    ...directory,
    own: OWN[role] ?? null,
  });
const to = (role: AppRole, link: string) => go(role, link)?.to ?? "(shown)";
const ROLES: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];

test("the sidebar's own items, and the old ones it left, every one with a home (manager)", () => {
  const rows: [string, string][] = [
    // Will we make the date?, Pods, Early warnings, Scope control and Reports left the sidebar.
    ["/portfolio", "/portfolio/date"],
    ["/portfolio?program=program-digital&q=pay", "/portfolio/date?program=program-digital&q=pay"],
    ["/portfolio/nowhere", "/portfolio/date"],
    ["/warnings", "/portfolio/warnings"],
    ["/warnings?project=project-checkout", "/projects/project-checkout/warnings"],
    ["/scope", "/portfolio/scope"],
    ["/scope?project=project-checkout", "/projects/project-checkout/scope"],
    ["/reports", "/portfolio/reports"],
    ["/reports/briefs", "/portfolio/reports"],
    ["/reports/briefs?brief=exec", "/portfolio/reports?brief=exec"],
    ["/pods", "/projects"],
    ["/pods/pod-payments", "/projects/project-checkout/pods/pod-payments/date"],
    [
      "/pods/pod-payments#checkins",
      "/projects/project-checkout/pods/pod-payments/warnings#checkins",
    ],
    [
      "/pods/pod-payments#blockers",
      "/projects/project-checkout/pods/pod-payments/warnings#blockers",
    ],
    ["/pods/pod-payments#status", "/projects/project-checkout/pods/pod-payments/warnings#status"],
    ["/pods/pod-nowhere", "/projects"],
    // A project page's sections before the tabs.
    ["/projects/project-checkout", "/projects/project-checkout/date"],
    ["/projects/project-checkout#timeline", "/projects/project-checkout/date#timeline"],
    ["/projects/project-checkout#stages", "/projects/project-checkout/date#stages"],
    [
      "/projects/project-checkout?row=pod:pod-payments",
      "/projects/project-checkout/date?row=pod:pod-payments",
    ],
    ["/projects/project-checkout#work", "/projects/project-checkout/date#work"],
    ["/projects/project-checkout#warnings", "/projects/project-checkout/warnings#warnings"],
    ["/projects/project-checkout#questions", "/projects/project-checkout/warnings#questions"],
    ["/projects/project-checkout#scope", "/projects/project-checkout/scope#scope"],
    ["/projects/project-checkout#gates", "/projects/project-checkout/scope#gates"],
    ["/projects/project-checkout/daily", "/projects/project-checkout/reports"],
    ["/projects/project-checkout/overall", "/projects/project-checkout/date"],
    ["/projects/project-checkout/nowhere", "/projects/project-checkout/date"],
    [
      "/projects/project-checkout/pods/pod-payments",
      "/projects/project-checkout/pods/pod-payments/date",
    ],
    [
      "/projects/project-checkout/pods/pod-payments#blockers",
      "/projects/project-checkout/pods/pod-payments/warnings#blockers",
    ],
    // From before the sidebar.
    ["/delivery", "/projects"],
    ["/delivery/program/program-digital", "/portfolio/date?program=program-digital"],
    ["/delivery/project/project-checkout", "/projects/project-checkout/date"],
    ["/delivery/pod/pod-payments", "/projects/project-checkout/pods/pod-payments/date"],
    [
      "/delivery/workstream/ws-payments",
      "/projects/project-checkout/date?workstream=ws-payments#work",
    ],
    ["/delivery/workstream/ws-lonely", "/projects"],
    ["/reports/project-checkout/overall", "/projects/project-checkout/date"],
    [
      "/reports/project-checkout/overall?release=rel-1",
      "/projects/project-checkout/date?release=rel-1",
    ],
    ["/reports/project-checkout", "/projects/project-checkout/reports"],
    ["/signals?view=risks", "/portfolio/warnings"],
    ["/signals?view=drift", "/portfolio/warnings"],
    ["/signals?view=flow&scope=pod:pod-payments&days=90", "/flow?scope=pod:pod-payments&days=90"],
    ["/signals", "/portfolio/warnings"],
    ["/coordination", "/requests"],
    ["/coordination?requests=own", "/requests?requests=own"],
    ["/coordination?brief=exec", "/portfolio/reports?brief=exec"],
    ["/coordination?brief=all&requests=own", "/portfolio/reports?brief=all"],
    ["/chat", "/check-in"],
    ["/today?pod=pod-payments", "/projects/project-checkout/pods/pod-payments/date"],
    ["/today?project=project-checkout", "/projects/project-checkout/date"],
    ["/risks", "/portfolio/warnings"],
    ["/ask", "/ora"],
  ];
  for (const [link, next] of rows) assert.equal(to("mgr", link), next, link);
});

test("the hub's own addresses are shown as they are, for every role that has the tab", () => {
  for (const role of ROLES) {
    const tabs = access(role).hub.tabs;
    for (const tab of tabs.all)
      assert.equal(to(role, `/portfolio/${tab}`), "(shown)", `${role} ${tab}`);
    for (const tab of tabs.project) {
      assert.equal(to(role, `/projects/project-checkout/${tab}`), "(shown)", `${role} ${tab}`);
    }
    for (const tab of tabs.pod) {
      assert.equal(
        to(role, `/projects/project-checkout/pods/pod-payments/${tab}`),
        "(shown)",
        `${role} ${tab}`,
      );
    }
    assert.equal(to(role, "/projects"), "(shown)", `${role}: the landing resolves by role`);
  }
});

test("a sent day report's link lands on its project's Reports, an executive's on its date", () => {
  for (const link of [
    "/reports/project-checkout/daily?report=rep-1",
    "/reports/project-checkout/daily?report=rep-1&asOf=2026-10-02",
    "/reports/project-checkout/daily",
  ]) {
    const [, query = ""] = link.split("?");
    for (const role of ROLES.filter((one) => one !== "exec")) {
      assert.equal(
        to(role, link),
        `/projects/project-checkout/reports${query ? `?${query}` : ""}`,
        `${role} ${link}`,
      );
    }
  }
  // No day report for an executive: its project's date, the viewing day kept, the pick dropped.
  assert.equal(access("exec").reports.daily, false);
  assert.equal(
    to("exec", "/reports/project-checkout/daily?report=rep-1"),
    "/projects/project-checkout/date",
  );
  assert.equal(
    to("exec", "/reports/project-checkout/daily?report=rep-1&asOf=2026-10-02"),
    "/projects/project-checkout/date?asOf=2026-10-02",
  );
  assert.equal(to("exec", "/reports/project-checkout"), "/projects/project-checkout/date");
  assert.equal(to("exec", "/projects/project-checkout/daily"), "/projects/project-checkout/date");
  // Reports is still theirs: the briefs, at every level.
  assert.equal(to("exec", "/reports"), "/portfolio/reports");
  assert.equal(to("exec", "/reports/briefs?brief=exec"), "/portfolio/reports?brief=exec");
  assert.equal(to("exec", "/projects/project-checkout/reports"), "(shown)");
  // An executive who also holds another role reads the report as that role.
  assert.equal(accessOf({ lens: ["exec", "po"], chatEnabled: false }).reports.daily, true);
});

test("a sent impact link lands on the date tab at the same row and anchor, for every role", () => {
  // impact_service wrote `/reports/<p>/overall?row=<kind>:<id>#timeline` into sent messages.
  const sent = "/reports/project-checkout/overall?row=pod:pod-payments#timeline";
  const landed = "/projects/project-checkout/date?row=pod:pod-payments#timeline";
  for (const role of ["sm", "po", "mgr", "exec", "admin"] as const) {
    assert.equal(to(role, sent), landed, role);
  }
  // A developer reads no project dates: their own project's first tab, the link's rest kept.
  assert.equal(
    to("dev", sent),
    "/projects/project-checkout/warnings?row=pod:pod-payments#timeline",
  );
  // Encoded parameters are kept as written, and the viewing day goes along.
  assert.equal(
    to(
      "mgr",
      "/reports/project-checkout/overall?row=project%3Aproject-checkout&asOf=2026-10-02#timeline",
    ),
    "/projects/project-checkout/date?row=project%3Aproject-checkout&asOf=2026-10-02#timeline",
  );
});

test("a pod's link opens on the viewer's own project of it, else its first", () => {
  const links = { ...directory, own: OWN.sm ?? null };
  assert.equal(podProjectFor("pod-payments", links), "project-checkout");
  // A pod on two projects: the one the person is on comes first.
  assert.equal(podProjectFor("pod-shared", links), "project-identity");
  assert.equal(podProjectFor("pod-shared", { ...directory, own: null }), "project-insights");
  assert.equal(podProjectFor("pod-nowhere", links), null);
  assert.equal(
    to("sm", "/pods/pod-shared#blockers"),
    "/projects/project-identity/pods/pod-shared/warnings#blockers",
  );
  assert.equal(to("mgr", "/pods/pod-shared"), "/projects/project-insights/pods/pod-shared/date");
});

test("each role's links land on the closest level and tab it has", () => {
  const rows: [string, string, string, string, string][] = [
    // link, developer, scrum master, product owner, executive
    ["/portfolio", "/portfolio/reports", "/portfolio/date", "/portfolio/date", "/portfolio/date"],
    ["/portfolio/date", "/portfolio/reports", "(shown)", "(shown)", "(shown)"],
    [
      "/warnings",
      "/portfolio/reports",
      "/portfolio/warnings",
      "/portfolio/warnings",
      "/portfolio/warnings",
    ],
    [
      // A developer's own project: no dates, their first tab there.
      "/projects/project-checkout",
      "/projects/project-checkout/warnings",
      "/projects/project-checkout/date",
      "/projects/project-checkout/date",
      "/projects/project-checkout/date",
    ],
    [
      "/projects/project-checkout/date",
      "/projects/project-checkout/warnings",
      "(shown)",
      "(shown)",
      "(shown)",
    ],
    [
      // Another project: a developer reads its day report.
      "/projects/project-insights/warnings",
      "/projects/project-insights/reports",
      "(shown)",
      "(shown)",
      "(shown)",
    ],
    [
      "/delivery/project/project-insights",
      "/projects/project-insights/reports",
      "/projects/project-insights/date",
      "/projects/project-insights/date",
      "/projects/project-insights/date",
    ],
    [
      "/pods/pod-payments",
      "/projects/project-checkout/pods/pod-payments/date",
      "/projects/project-checkout/pods/pod-payments/date",
      "/projects/project-checkout/pods/pod-payments/date",
      "/projects/project-checkout/pods/pod-payments/date",
    ],
    [
      // A developer's neighbouring pod is a name in their tree: its project instead.
      "/pods/pod-storefront",
      "/projects/project-checkout/warnings",
      "/projects/project-checkout/pods/pod-storefront/date",
      "/projects/project-checkout/pods/pod-storefront/date",
      "/projects/project-checkout/pods/pod-storefront/date",
    ],
    [
      // Outside their part of the tree.
      "/pods/pod-data",
      "/projects/project-insights/reports",
      "/projects/project-insights/pods/pod-data/date",
      "/projects/project-insights/pods/pod-data/date",
      "/projects/project-insights/pods/pod-data/date",
    ],
    [
      "/projects/project-checkout/pods/pod-payments/scope",
      "/projects/project-checkout/pods/pod-payments/date",
      "(shown)",
      "(shown)",
      "(shown)",
    ],
    [
      "/signals?view=risks",
      "/portfolio/reports",
      "/portfolio/warnings",
      "/portfolio/warnings",
      "/portfolio/warnings",
    ],
    ["/signals", "/today", "/portfolio/warnings", "/portfolio/warnings", "/portfolio/warnings"],
    ["/coordination", "/today#asks", "/requests", "/requests", "/requests"],
    ["/chat", "/check-in", "/check-in", "/check-in", "/check-in"],
  ];
  for (const [link, dev, sm, po, exec] of rows) {
    assert.deepEqual(
      [to("dev", link), to("sm", link), to("po", link), to("exec", link)],
      [dev, sm, po, exec],
      link,
    );
  }
});

test("a page the role is not offered opens the closest one it has", () => {
  const rows: [string, string, string, string, string][] = [
    // link, developer, scrum master, product owner, executive
    ["/requests", "/today#asks", "(shown)", "(shown)", "(shown)"],
    ["/flow", "/today", "(shown)", "(shown)", "(shown)"],
    ["/ora", "/today", "(shown)", "(shown)", "(shown)"],
    ["/people", "/projects", "(shown)", "(shown)", "/today"],
    ["/admin", "/today", "/today", "/today", "/today"],
    ["/projects", "(shown)", "(shown)", "(shown)", "(shown)"],
    ["/check-in", "(shown)", "(shown)", "(shown)", "(shown)"],
  ];
  for (const [link, dev, sm, po, exec] of rows) {
    assert.deepEqual(
      [to("dev", link), to("sm", link), to("po", link), to("exec", link)],
      [dev, sm, po, exec],
      link,
    );
  }
  for (const link of [
    "/requests",
    "/people",
    "/portfolio/warnings",
    "/projects/project-data/scope",
  ]) {
    assert.equal(to("mgr", link), "(shown)", link);
    assert.equal(to("admin", link), "(shown)", link);
  }
  assert.equal(to("mgr", "/admin"), "/today");
  assert.equal(to("admin", "/admin"), "(shown)");
});

test("until the person's tree is read, a project or pod link is left to the page", () => {
  const before = (role: AppRole, path: string) =>
    redirectFor(where(path), access(role), capabilitiesFor([role]), directory)?.to ?? "(shown)";
  // Only the tab is placed; whether the project is theirs waits for the tree.
  assert.equal(before("dev", "/projects/project-insights"), "/projects/project-insights/warnings");
  assert.equal(before("dev", "/projects/project-insights/warnings"), "(shown)");
  assert.equal(before("dev", "/projects/project-checkout/pods/pod-data/date"), "(shown)");
  // An old address still moves at once.
  assert.equal(
    before("dev", "/delivery/pod/pod-data"),
    "/projects/project-insights/pods/pod-data/date",
  );
});

test("only a link that lands on plain Today names the page it asked for", () => {
  assert.equal(go("dev", "/flow")?.missing, "flow");
  assert.equal(go("dev", "/signals")?.missing, "flow");
  assert.equal(go("sm", "/admin")?.missing, "admin");
  assert.equal(go("exec", "/people")?.missing, "people");
  // Something of the role's own stands in for these: no toast.
  assert.equal(go("dev", "/requests")?.missing, null, "Your asks on Today");
  assert.equal(go("dev", "/people")?.missing, null, "their pod's page has its people");
  assert.equal(go("dev", "/warnings")?.missing, null, "their day reports");
  assert.equal(go("exec", "/pods")?.missing, null);
  assert.equal(go("exec", "/pods/pod-payments")?.missing, null, "its project stands in");
  for (const role of ["dev", "sm", "po"] as const) {
    for (const link of ["/delivery/program/program-digital", "/delivery/pod/pod-data"]) {
      assert.equal(go(role, link)?.missing, null, `${role} ${link}`);
    }
  }
});

test("the viewing day goes along with every redirect, before the hash", () => {
  assert.equal(to("dev", "/coordination?asOf=2026-10-02"), "/today?asOf=2026-10-02#asks");
  assert.equal(
    to("sm", "/today?pod=pod-data&asOf=2026-10-02"),
    "/projects/project-insights/pods/pod-data/date?asOf=2026-10-02",
  );
  assert.equal(
    to("dev", "/delivery/pod/pod-storefront?asOf=2026-10-02"),
    "/projects/project-checkout/warnings?asOf=2026-10-02",
  );
  assert.equal(
    to("sm", "/signals?view=risks&asOf=2026-10-02"),
    "/portfolio/warnings?asOf=2026-10-02",
  );
  assert.equal(to("dev", "/admin?tab=gates&asOf=2026-10-02"), "/today?asOf=2026-10-02");
  assert.equal(
    to("mgr", "/delivery/workstream/ws-payments?asOf=2026-10-02"),
    "/projects/project-checkout/date?asOf=2026-10-02&workstream=ws-payments#work",
  );
  assert.equal(
    to("po", "/projects/project-checkout?asOf=2026-10-02&release=rel-1"),
    "/projects/project-checkout/date?asOf=2026-10-02&release=rel-1",
  );
});

test("legacy addresses alone: current ones are not moved, ids stay encoded", () => {
  const legacy = (link: string, role: AppRole = "mgr") =>
    legacyPath(where(link), access(role), directory);
  for (const current of [
    "/today",
    "/projects",
    "/projects/p-1",
    "/projects/p-1/date",
    "/projects/p-1/pods/d-1/warnings",
    "/portfolio",
    "/portfolio/reports",
  ]) {
    assert.equal(legacy(current), null, current);
  }
  assert.equal(legacy("/today?asOf=2026-10-02"), null);
  assert.equal(legacy("/delivery/project/project%2Finsights"), "/projects/project%2Finsights");
  assert.equal(legacy("/today?project=a%20b"), "/projects/a%20b");
  assert.equal(legacy("/reports/a%20b/daily"), "/projects/a%20b/reports");
});

test("what the guard reads before it places a link", () => {
  const needs = (role: AppRole, link: string) => guardNeeds(where(link), access(role));
  assert.deepEqual(needs("dev", "/projects/p-1"), { tree: true, pods: false, workstreams: false });
  assert.deepEqual(needs("dev", "/projects/p-1/pods/d-1/date"), {
    tree: true,
    pods: false,
    workstreams: false,
  });
  // A pod's old link opens on the viewer's own project of it: every role reads the tree.
  assert.deepEqual(needs("dev", "/pods/d-1"), { tree: true, pods: true, workstreams: false });
  assert.deepEqual(needs("mgr", "/pods/d-1"), { tree: true, pods: true, workstreams: false });
  assert.deepEqual(needs("mgr", "/projects/p-1/date"), {
    tree: false,
    pods: false,
    workstreams: false,
  });
  assert.deepEqual(needs("exec", "/delivery/workstream/w-1"), {
    tree: false,
    pods: false,
    workstreams: true,
  });
  assert.deepEqual(needs("po", "/reports/p-1/overall"), {
    tree: true,
    pods: false,
    workstreams: false,
  });
  assert.deepEqual(needs("sm", "/today"), { tree: false, pods: false, workstreams: false });
});

test("the toast says what was opened instead, and which other role of theirs has the page", () => {
  assert.equal(
    redirectToast("flow", "Developer"),
    "Opened Today: Review flow isn't part of the developer view.",
  );
  assert.equal(
    redirectToast("admin", "Scrum Master"),
    "Opened Today: Admin isn't part of the scrum master view.",
  );
  assert.equal(roleOfferingPage("admin", ["mgr", "admin"], "mgr", false, rolePriority), "admin");
  assert.equal(roleOfferingPage("admin", ["mgr"], "mgr", false, rolePriority), null);
  assert.equal(
    roleOfferingPage("flow", ["dev", "mgr"], "dev", false, rolePriority),
    "mgr",
    "the most senior role that has it",
  );
  assert.equal(roleOfferingPage("people", ["dev", "sm"], "dev", false, rolePriority), "sm");
  assert.equal(roleOfferingPage("admin", ["dev", "sm"], "dev", false, rolePriority), null);
});

test("palette rows open each thing in the hub, where the role has it", () => {
  const targets = (role: AppRole) => {
    const t = paletteTargets(access(role), directory.workstreamProjects);
    return {
      program: t.program?.("p1") ?? null,
      project: t.project?.("p2") ?? null,
      workstream: t.workstream?.("ws-payments") ?? null,
      pod: t.pod?.("d1", ["p2"]) ?? null,
      person: t.person?.("d1", ["p2"]) ?? null,
    };
  };
  assert.deepEqual(targets("mgr"), {
    program: "/portfolio/date?program=p1",
    project: "/projects/p2",
    workstream: "/projects/project-checkout/date?workstream=ws-payments#work",
    pod: "/projects/p2/pods/d1/date",
    person: "/projects/p2/pods/d1/date",
  });
  // An executive's pods open only from five people up, which the pod bar knows: no pod rows.
  assert.deepEqual(targets("exec"), {
    program: "/portfolio/date?program=p1",
    project: "/projects/p2",
    workstream: "/projects/project-checkout/date?workstream=ws-payments#work",
    pod: null,
    person: null,
  });
  // Their own part only: which of these rows exist is the tree's (paletteRows.ownRows).
  for (const role of ["sm", "po", "dev"] as const) {
    assert.deepEqual(
      targets(role),
      {
        program: null,
        project: "/projects/p2",
        workstream: null,
        pod: "/projects/p2/pods/d1/date",
        person: "/projects/p2/pods/d1/date",
      },
      role,
    );
  }
  // A pod with no project known keeps its old address, which the router places.
  assert.equal(paletteTargets(access("mgr")).pod?.("d1"), "/pods/d1");
  assert.equal(
    paletteTargets(access("mgr")).workstream?.("ws-lonely"),
    "/delivery/workstream/ws-lonely",
  );
});

test("the assistant floats on every tab for the aggregate readers, never for a developer", () => {
  assert.deepEqual(
    (["dev", "sm", "po", "mgr", "exec", "admin"] as const).map((role) => access(role).assistant),
    [false, true, true, true, true, true],
  );
  // Roles combine: a developer who is also a scrum master has it.
  assert.equal(accessOf({ lens: ["dev", "sm"], chatEnabled: false }).assistant, true);
});

test("release readiness: read by those who decide on a release, acted on by all but an executive", () => {
  assert.deepEqual(access("dev").readiness, { board: false, everyProject: false, act: false });
  assert.deepEqual(access("exec").readiness, { board: true, everyProject: true, act: false });
  // A scrum master reads only the projects their own pods work on.
  assert.deepEqual(access("sm").readiness, { board: true, everyProject: false, act: true });
  for (const role of ["po", "mgr", "admin"] as const) {
    assert.deepEqual(access(role).readiness, { board: true, everyProject: true, act: true }, role);
  }
  // Roles combine: a scrum master who is also a product owner reads every project's.
  assert.equal(accessOf({ lens: ["sm", "po"], chatEnabled: false }).readiness.everyProject, true);
});

test("release readiness: a scrum master asks only for a project one of their pods works on", () => {
  const sm = access("sm");
  // Ira runs Payments (Checkout) and Identity; Storefront, on Checkout too, is not hers.
  assert.equal(readsReadiness(sm, "project-checkout", scrumMasterTree), true);
  assert.equal(readsReadiness(sm, "project-identity", scrumMasterTree), true);
  assert.equal(readsReadiness(sm, "project-insights", scrumMasterTree), false);
  // A project listed only through a pod that is not hers is not read either.
  const storefrontOnly = {
    ...scrumMasterTree,
    pods: scrumMasterTree.pods.filter((pod) => pod.id === "pod-storefront"),
  };
  assert.equal(readsReadiness(sm, "project-checkout", storefrontOnly), false);
  // Until the tree answers, or when it failed, nothing is asked.
  assert.equal(readsReadiness(sm, "project-checkout", undefined), false);
  // The roles that read every project's never need the tree.
  for (const role of ["po", "mgr", "admin", "exec"] as const) {
    assert.equal(readsReadiness(access(role), "project-insights", undefined), true, role);
  }
  assert.equal(readsReadiness(access("dev"), "project-checkout", developerTree), false);
  assert.equal(readsReadiness(access("po"), "", undefined), false);
});

test("jira hygiene: the readiness board's readers, a developer none, an executive no actions", () => {
  assert.deepEqual(access("dev").hygiene, { board: false, everyProject: false, act: false });
  assert.deepEqual(access("exec").hygiene, { board: true, everyProject: true, act: false });
  assert.deepEqual(access("sm").hygiene, { board: true, everyProject: false, act: true });
  for (const role of ["po", "mgr", "admin"] as const) {
    assert.deepEqual(access(role).hygiene, { board: true, everyProject: true, act: true }, role);
  }
  // A scrum master reads it only for a project one of their own pods works on.
  assert.equal(readsHygiene(access("sm"), "project-checkout", scrumMasterTree), true);
  assert.equal(readsHygiene(access("sm"), "project-insights", scrumMasterTree), false);
  assert.equal(readsHygiene(access("sm"), "project-checkout", undefined), false);
  assert.equal(readsHygiene(access("exec"), "project-insights", undefined), true);
  assert.equal(readsHygiene(access("dev"), "project-checkout", developerTree), false);
});
