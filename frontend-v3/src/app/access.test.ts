import assert from "node:assert/strict";
import { test } from "node:test";

import {
  accessOf,
  capabilitiesFor,
  guardNeeds,
  legacyPath,
  pageOf,
  paletteTargets,
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
  assert.deepEqual(pages("dev"), ["today", "checkin", "projects", "pods", "reports"]);
  assert.deepEqual(pages("sm"), [
    "today",
    "checkin",
    "requests",
    "projects",
    "pods",
    "scope",
    "reports",
    "flow",
    "ora",
    "people",
  ]);
  assert.deepEqual(pages("po"), [
    "today",
    "checkin",
    "requests",
    "portfolio",
    "projects",
    "pods",
    "scope",
    "reports",
    "flow",
    "ora",
    "people",
  ]);
  assert.deepEqual(pages("mgr"), [
    "today",
    "checkin",
    "requests",
    "portfolio",
    "projects",
    "pods",
    "warnings",
    "scope",
    "reports",
    "flow",
    "ora",
    "people",
  ]);
  // No names for an executive: no Requests board, no Pods, no People.
  assert.deepEqual(pages("exec"), [
    "today",
    "checkin",
    "portfolio",
    "projects",
    "warnings",
    "scope",
    "reports",
    "flow",
    "ora",
  ]);
  assert.equal(pages("admin").length, 13);
  // The built-in chat is a column of Check-in, never a page of its own.
  assert.deepEqual(access("dev", true).pages, access("dev").pages);
  assert.equal(access("dev", true).checkin.chat, true);
  assert.equal(access("exec").checkin.chat, false);
});

test("the sidebar's groups hold only offered pages, and a group with none is left out", () => {
  const groups = (role: AppRole) =>
    shownGroups(access(role)).map((group) => [group.label, group.items.map((item) => item.label)]);
  assert.deepEqual(groups("dev"), [
    ["Daily", ["Today", "Check-in"]],
    ["Delivery", ["Projects", "Pods"]],
    ["Insight", ["Reports"]],
  ]);
  assert.deepEqual(groups("exec"), [
    ["Daily", ["Today", "Check-in"]],
    ["Delivery", ["Will we make the date?", "Projects", "Early warnings", "Scope control"]],
    ["Insight", ["Reports", "Review flow", "Ask Ora"]],
  ]);
  assert.deepEqual(groups("admin"), [
    ["Daily", ["Today", "Check-in", "Requests"]],
    ["Delivery", ["Will we make the date?", "Projects", "Pods", "Early warnings", "Scope control"]],
    ["Insight", ["Reports", "Review flow", "Ask Ora"]],
    ["Setup", ["People & roles", "Admin"]],
  ]);
  // The nav is built from the access map alone: it never grows with the projects.
  for (const role of ["dev", "sm", "po", "mgr", "exec", "admin"] as const) {
    assert.ok(shownGroups(access(role)).flatMap((group) => group.items).length <= 13, role);
  }
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
  assert.equal(capabilitiesFor(["po", "sm"]).canReadPodDetail, true);
});

test("pages by path; an old address belongs to no page", () => {
  assert.equal(pageOf("/today"), "today");
  assert.equal(pageOf("/check-in"), "checkin");
  assert.equal(pageOf("/projects/project-checkout"), "projects");
  assert.equal(pageOf("/pods/pod-payments"), "pods");
  assert.equal(pageOf("/warnings"), "warnings");
  assert.equal(pageOf("/reports/project-checkout/daily"), "reports");
  assert.equal(pageOf("/reports/briefs"), "reports");
  assert.equal(pageOf("/admin"), "admin");
  for (const old of ["/delivery/pod/pod-payments", "/signals", "/coordination", "/chat", "/"]) {
    assert.equal(pageOf(old), null, old);
  }
  assert.equal(pageOf("/constructor"), null, "no inherited key is a page");
});

const directory = {
  podProjects: (id: string) =>
    id === "pod-payments" || id === "pod-storefront"
      ? ["project-checkout"]
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

test("every old address opens where it lives now, its query and hash kept (manager)", () => {
  const rows: [string, string][] = [
    ["/delivery", "/portfolio"],
    ["/delivery/program/program-digital", "/portfolio?program=program-digital"],
    ["/delivery/project/project-checkout", "/projects/project-checkout"],
    ["/delivery/pod/pod-payments", "/pods/pod-payments"],
    ["/delivery/workstream/ws-payments", "/projects/project-checkout#work"],
    ["/delivery/workstream/ws-lonely", "/projects"],
    ["/reports/project-checkout/overall", "/projects/project-checkout"],
    ["/reports/project-checkout/overall?release=rel-1", "/projects/project-checkout?release=rel-1"],
    ["/reports/project-checkout", "/reports/project-checkout/daily"],
    ["/signals?view=risks", "/warnings"],
    ["/signals?view=drift", "/warnings"],
    ["/signals?view=flow&scope=pod:pod-payments&days=90", "/flow?scope=pod:pod-payments&days=90"],
    ["/signals", "/warnings"],
    ["/coordination", "/requests"],
    ["/coordination?requests=own", "/requests?requests=own"],
    ["/coordination?brief=exec", "/reports/briefs?brief=exec"],
    ["/coordination?brief=all&requests=own", "/reports/briefs?brief=all"],
    ["/chat", "/check-in"],
    ["/today?pod=pod-payments", "/pods/pod-payments"],
    ["/today?project=project-checkout", "/projects/project-checkout"],
    ["/projects/project-checkout/overall", "/projects/project-checkout"],
    ["/projects/project-checkout/daily", "/reports/project-checkout/daily"],
    ["/risks", "/warnings"],
    ["/ask", "/ora"],
  ];
  for (const [link, next] of rows) assert.equal(to("mgr", link), next, link);
});

test("a sent impact link lands on the Project page at the same row and anchor, for every role", () => {
  // impact_service wrote `/reports/<p>/overall?row=<kind>:<id>#timeline` into sent messages.
  const sent = "/reports/project-checkout/overall?row=pod:pod-payments#timeline";
  const landed = "/projects/project-checkout?row=pod:pod-payments#timeline";
  for (const role of ["sm", "po", "mgr", "exec", "admin"] as const) {
    assert.equal(to(role, sent), landed, role);
  }
  // A developer's own project opens too (the name-only form); the row stays in the link.
  assert.equal(to("dev", sent), landed);
  // Encoded parameters are kept as written, and the viewing day goes along.
  assert.equal(
    to(
      "mgr",
      "/reports/project-checkout/overall?row=project%3Aproject-checkout&asOf=2026-10-02#timeline",
    ),
    "/projects/project-checkout?row=project%3Aproject-checkout&asOf=2026-10-02#timeline",
  );
  // A release readiness line linked the project's Overall too.
  assert.equal(to("po", "/reports/project-checkout/overall"), "/projects/project-checkout");
});

test("the day report's link opens its own page for every role but the executive", () => {
  for (const link of [
    "/reports/project-checkout/daily?report=rep-1",
    "/reports/project-checkout/daily?report=rep-1&asOf=2026-10-02",
    "/reports/project-checkout/daily",
  ]) {
    for (const role of ROLES.filter((one) => one !== "exec")) {
      assert.equal(to(role, link), "(shown)", `${role} ${link}`);
    }
  }
});

test("an executive has no Daily view: a report's link opens its project, Reports the briefs", () => {
  assert.equal(access("exec").reports.daily, false);
  for (const role of ["dev", "sm", "po", "mgr", "admin"] as const) {
    assert.equal(access(role).reports.daily, true, role);
  }
  // An executive who also holds another role reads the report as that role.
  assert.equal(accessOf({ lens: ["exec", "po"], chatEnabled: false }).reports.daily, true);
  // A sent report's link: its project's page, the viewing day kept, the report's pick dropped.
  assert.equal(
    to("exec", "/reports/project-checkout/daily?report=rep-1"),
    "/projects/project-checkout",
  );
  assert.equal(
    to("exec", "/reports/project-checkout/daily?report=rep-1&asOf=2026-10-02"),
    "/projects/project-checkout?asOf=2026-10-02",
  );
  // The addresses from before the sidebar that led to a day report.
  assert.equal(to("exec", "/reports/project-checkout"), "/projects/project-checkout");
  assert.equal(to("exec", "/projects/project-checkout/daily"), "/projects/project-checkout");
  assert.equal(to("exec", "/reports"), "/reports/briefs");
  assert.equal(to("exec", "/reports/briefs?brief=exec"), "(shown)");
  // The sidebar's and the palette's Reports open on the briefs.
  const reports = shownGroups(access("exec"))
    .flatMap((group) => group.items)
    .find((item) => item.page === "reports");
  assert.equal(reports?.to, "/reports/briefs");
  const managers = shownGroups(access("mgr"))
    .flatMap((group) => group.items)
    .find((item) => item.page === "reports");
  assert.equal(managers?.to, "/reports");
});

test("an old address that lands on a page the role lacks goes on to the closest it has", () => {
  const rows: [string, string, string, string, string][] = [
    // link, developer, scrum master, product owner, executive
    ["/delivery", "/projects", "/projects", "/portfolio", "/portfolio"],
    [
      "/delivery/program/program-digital",
      "/projects",
      "/projects",
      "/portfolio?program=program-digital",
      "/portfolio?program=program-digital",
    ],
    [
      // An executive has no Pods: a pod's dates are on its project's timeline.
      "/delivery/pod/pod-payments",
      "/pods/pod-payments",
      "/pods/pod-payments",
      "/pods/pod-payments",
      "/projects/project-checkout",
    ],
    [
      // A developer's neighbouring pod is a name in their tree: its project instead.
      "/delivery/pod/pod-storefront",
      "/projects/project-checkout",
      "/pods/pod-storefront",
      "/pods/pod-storefront",
      "/projects/project-checkout",
    ],
    [
      // Outside their part of the tree.
      "/delivery/pod/pod-data",
      "/pods",
      "/pods/pod-data",
      "/projects/project-insights",
      "/projects/project-insights",
    ],
    [
      "/delivery/project/project-insights",
      "/reports/project-insights/daily",
      "/projects/project-insights",
      "/projects/project-insights",
      "/projects/project-insights",
    ],
    ["/signals?view=risks", "/today", "/projects#warnings", "/projects#warnings", "/warnings"],
    ["/signals", "/today", "/flow", "/flow", "/warnings"],
    ["/coordination", "/today#asks", "/requests", "/requests", "/reports/briefs"],
    [
      "/coordination?brief=exec",
      "/reports",
      "/reports/briefs?brief=exec",
      "/reports/briefs?brief=exec",
      "/reports/briefs?brief=exec",
    ],
    ["/chat", "/check-in", "/check-in", "/check-in", "/check-in"],
    [
      "/today?pod=pod-payments",
      "/pods/pod-payments",
      "/pods/pod-payments",
      "/pods/pod-payments",
      "/projects/project-checkout",
    ],
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
    ["/requests", "/today#asks", "(shown)", "(shown)", "/today"],
    ["/portfolio", "/today", "/projects", "(shown)", "(shown)"],
    ["/warnings", "/today", "/projects#warnings", "/projects#warnings", "(shown)"],
    ["/scope", "/today", "(shown)", "(shown)", "(shown)"],
    ["/flow", "/today", "(shown)", "(shown)", "(shown)"],
    ["/ora", "/today", "(shown)", "(shown)", "(shown)"],
    ["/people", "/pods", "(shown)", "(shown)", "/today"],
    ["/admin", "/today", "/today", "/today", "/today"],
    ["/pods", "(shown)", "(shown)", "(shown)", "/today"],
    ["/reports/briefs", "/reports", "(shown)", "(shown)", "(shown)"],
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
  for (const link of ["/requests", "/portfolio", "/warnings", "/people", "/pods/pod-data"]) {
    assert.equal(to("mgr", link), "(shown)", link);
    assert.equal(to("admin", link), "(shown)", link);
  }
  assert.equal(to("mgr", "/admin"), "/today");
  assert.equal(to("admin", "/admin"), "(shown)");
});

test("until the person's tree is read, a project or pod link is left to the page", () => {
  const before = (role: AppRole, path: string) =>
    redirectFor(where(path), access(role), capabilitiesFor([role]), directory);
  for (const role of ["dev", "sm", "po"] as const) {
    assert.equal(before(role, "/pods/pod-data"), null, role);
    assert.equal(before(role, "/projects/project-insights"), null, role);
  }
  // An old address still moves at once: the role's rule applies on the next pass.
  assert.equal(before("dev", "/delivery/pod/pod-data")?.to, "/pods/pod-data");
});

test("only a link that lands on plain Today names the page it asked for", () => {
  assert.equal(go("dev", "/flow")?.missing, "flow");
  assert.equal(go("dev", "/signals")?.missing, "flow");
  assert.equal(go("sm", "/admin")?.missing, "admin");
  assert.equal(go("exec", "/requests")?.missing, "requests");
  assert.equal(go("exec", "/pods")?.missing, "pods");
  // Something of the role's own stands in for these: no toast.
  assert.equal(go("dev", "/requests")?.missing, null, "Your asks on Today");
  assert.equal(go("dev", "/people")?.missing, null, "their pod's page has its people");
  assert.equal(go("sm", "/warnings")?.missing, null, "their project's warnings");
  assert.equal(go("sm", "/portfolio")?.missing, null);
  assert.equal(go("exec", "/pods/pod-payments")?.missing, null, "its project stands in");
  for (const role of ["dev", "sm", "po"] as const) {
    for (const link of ["/delivery/program/program-digital", "/delivery/pod/pod-data"]) {
      assert.equal(go(role, link)?.missing, null, `${role} ${link}`);
    }
  }
});

test("the viewing day goes along with every redirect, before the hash", () => {
  assert.equal(to("dev", "/coordination?asOf=2026-10-02"), "/today?asOf=2026-10-02#asks");
  assert.equal(to("sm", "/today?pod=pod-data&asOf=2026-10-02"), "/pods/pod-data?asOf=2026-10-02");
  assert.equal(
    to("dev", "/delivery/pod/pod-storefront?asOf=2026-10-02"),
    "/projects/project-checkout?asOf=2026-10-02",
  );
  assert.equal(
    to("sm", "/signals?view=risks&asOf=2026-10-02"),
    "/projects?asOf=2026-10-02#warnings",
  );
  assert.equal(to("dev", "/admin?tab=gates&asOf=2026-10-02"), "/today?asOf=2026-10-02");
  assert.equal(
    to("mgr", "/delivery/workstream/ws-payments?asOf=2026-10-02"),
    "/projects/project-checkout?asOf=2026-10-02#work",
  );
});

test("legacy addresses alone: current ones are not moved, ids stay encoded", () => {
  const legacy = (link: string, role: AppRole = "mgr") =>
    legacyPath(where(link), access(role), directory);
  for (const current of ["/today", "/projects/p-1", "/reports/p-1/daily", "/reports/briefs"]) {
    assert.equal(legacy(current), null, current);
  }
  assert.equal(legacy("/today?asOf=2026-10-02"), null);
  assert.equal(legacy("/delivery/project/project%2Finsights"), "/projects/project%2Finsights");
  assert.equal(legacy("/today?project=a%20b"), "/projects/a%20b");
});

test("what the guard reads before it places a link", () => {
  const needs = (role: AppRole, link: string) => guardNeeds(where(link), access(role));
  assert.deepEqual(needs("dev", "/projects/p-1"), { tree: true, pods: false, workstreams: false });
  assert.deepEqual(needs("dev", "/pods/d-1"), { tree: true, pods: true, workstreams: false });
  assert.deepEqual(needs("mgr", "/pods/d-1"), { tree: false, pods: false, workstreams: false });
  assert.deepEqual(needs("exec", "/pods/d-1"), { tree: false, pods: true, workstreams: false });
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
  assert.equal(roleOfferingPage("warnings", ["dev", "sm"], "dev", false, rolePriority), null);
});

test("palette rows open each thing on its own page, where the role has it", () => {
  const targets = (role: AppRole) => {
    const t = paletteTargets(access(role), directory.workstreamProjects);
    return {
      program: t.program?.("p1") ?? null,
      project: t.project?.("p2") ?? null,
      workstream: t.workstream?.("ws-payments") ?? null,
      pod: t.pod?.("d1") ?? null,
      person: t.person?.("d1") ?? null,
    };
  };
  assert.deepEqual(targets("mgr"), {
    program: "/portfolio?program=p1",
    project: "/projects/p2",
    workstream: "/projects/project-checkout#work",
    pod: "/pods/d1",
    person: "/pods/d1",
  });
  assert.deepEqual(targets("exec"), {
    program: "/portfolio?program=p1",
    project: "/projects/p2",
    workstream: "/projects/project-checkout#work",
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
        pod: "/pods/d1",
        person: "/pods/d1",
      },
      role,
    );
  }
  // A workstream no project holds keeps its old address, which the router places.
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
