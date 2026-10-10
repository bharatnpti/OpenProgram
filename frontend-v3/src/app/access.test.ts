import assert from "node:assert/strict";
import { test } from "node:test";

import {
  accessOf,
  capabilitiesFor,
  pageOf,
  paletteTargets,
  redirectFor,
  redirectToast,
  roleOfferingPage,
  type Page,
} from "./access.ts";
import type { OwnLinks } from "../features/delivery/ownTree.ts";
import { rolePriority, type AppRole } from "./roleWords.ts";

const access = (role: AppRole, chatEnabled = false) => accessOf({ lens: [role], chatEnabled });
const tabs = (role: AppRole, chatEnabled = false) =>
  (Object.entries(access(role, chatEnabled).pages) as [Page, boolean][])
    .filter(([, on]) => on)
    .map(([page]) => page);

test("each role is offered the tabs of PROPOSAL §2, Chat only where it is served", () => {
  // Delivery for everyone since 2026-10-10: each person drills down their own part.
  assert.deepEqual(tabs("dev"), ["today", "delivery", "reports"]);
  assert.deepEqual(tabs("sm"), ["today", "delivery", "signals", "coordination", "reports"]);
  assert.deepEqual(tabs("po"), ["today", "delivery", "signals", "coordination", "reports"]);
  assert.deepEqual(tabs("mgr"), ["today", "delivery", "signals", "coordination", "reports"]);
  assert.deepEqual(tabs("exec"), ["today", "delivery", "signals", "coordination", "reports"]);
  assert.deepEqual(tabs("admin"), [
    "today",
    "delivery",
    "signals",
    "coordination",
    "reports",
    "admin",
  ]);
  assert.deepEqual(tabs("dev", true), ["today", "delivery", "reports", "chat"]);
  assert.ok(tabs("exec", true).includes("chat"));
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
    podDates: false,
    requirements: false,
    risks: false,
    escalationLink: false,
  });
  assert.deepEqual(access("sm").overall, {
    forecast: false,
    podDates: true,
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
  assert.equal(both.overall.podDates, true);
  assert.equal(capabilitiesFor(["po", "sm"]).canReadPodDetail, true);
});

test("pages by path", () => {
  assert.equal(pageOf("/delivery/pod/pod-payments"), "delivery");
  assert.equal(pageOf("/delivery"), "delivery");
  assert.equal(pageOf("/signals"), "signals");
  assert.equal(pageOf("/coordination"), "coordination");
  assert.equal(pageOf("/admin"), "admin");
  assert.equal(pageOf("/chat"), "chat");
  assert.equal(pageOf("/today"), null);
  assert.equal(pageOf("/reports/project-checkout/daily"), null);
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
const go = (role: AppRole, path: string, chatEnabled = false) => {
  const [pathname, search = ""] = path.split("?");
  return redirectFor(
    { pathname, search: search ? `?${search}` : "" },
    access(role, chatEnabled),
    capabilitiesFor([role]),
    { ...directory, own: OWN[role] ?? null },
  );
};
const to = (role: AppRole, path: string, chatEnabled = false) =>
  go(role, path, chatEnabled)?.to ?? "(shown)";

test("the redirect table of PROPOSAL §7.2, row by row", () => {
  const rows: [string, string, string, string, string][] = [
    // link, developer, scrum master, product owner, executive
    [
      "/delivery/pod/pod-payments",
      "(shown)",
      "(shown)",
      "(shown)",
      "/delivery/project/project-checkout",
    ],
    [
      // A developer's neighbouring pod is a name in their tree: its project instead.
      "/delivery/pod/pod-storefront",
      "/delivery/project/project-checkout",
      "(shown)",
      "(shown)",
      "/delivery/project/project-checkout",
    ],
    [
      // Outside their part of the tree: where the role read it before it had Delivery.
      "/delivery/pod/pod-data",
      "/delivery",
      "/today?pod=pod-data",
      "/today?project=project-insights",
      "/delivery/project/project-insights",
    ],
    ["/delivery/project/project-checkout", "(shown)", "(shown)", "(shown)", "(shown)"],
    [
      "/delivery/project/project-insights",
      "/reports/project-insights/daily",
      "/reports/project-insights/overall",
      "/today?project=project-insights",
      "(shown)",
    ],
    ["/delivery/program/program-digital", "/delivery", "/delivery", "/delivery", "(shown)"],
    [
      "/delivery/workstream/ws-payments",
      "/delivery/project/project-checkout",
      "/delivery/project/project-checkout",
      "/delivery/project/project-checkout",
      "(shown)",
    ],
    [
      "/delivery/workstream/ws-datalake",
      "/delivery",
      "/delivery",
      "/today?project=project-insights",
      "(shown)",
    ],
    ["/delivery", "(shown)", "(shown)", "(shown)", "(shown)"],
    ["/signals?view=risks", "/today", "/signals?view=flow", "/signals?view=flow", "(shown)"],
    ["/signals", "/today", "(shown)", "(shown)", "(shown)"],
    ["/coordination", "/today#asks", "(shown)", "(shown)", "(shown)"],
    ["/admin", "/today", "/today", "/today", "/today"],
    ["/chat", "/today", "/today", "/today", "/today"],
  ];
  for (const [link, dev, sm, po, exec] of rows) {
    assert.deepEqual(
      [to("dev", link), to("sm", link), to("po", link), to("exec", link)],
      [dev, sm, po, exec],
      link,
    );
  }
});

test("until the person's tree is read, a Delivery link is left to the page", () => {
  const before = (role: AppRole, path: string) =>
    redirectFor({ pathname: path, search: "" }, access(role), capabilitiesFor([role]), directory);
  for (const role of ["dev", "sm", "po"] as const) {
    assert.equal(before(role, "/delivery/pod/pod-data"), null, role);
    assert.equal(before(role, "/delivery/project/project-insights"), null, role);
  }
});

test("the day report's link opens its own page for every role, so none is redirected", () => {
  const roles: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];
  for (const link of [
    "/reports/project-checkout/daily?report=rep-1",
    "/reports/project-checkout/daily?report=rep-1&asOf=2026-10-02",
    "/reports/project-checkout/daily",
  ]) {
    for (const role of roles) assert.equal(to(role, link), "(shown)", `${role} ${link}`);
  }
});

test("a day report sent with the old link (the project's Delivery page) reaches a page the role has", () => {
  // Those reports linked `<console>/delivery/project/<id>`. A project of the person's
  // opens in their Delivery; another is sent to the report or the project they may read.
  const roles: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];
  assert.deepEqual(
    roles.map((role) => to(role, "/delivery/project/project-checkout")),
    ["(shown)", "(shown)", "(shown)", "(shown)", "(shown)", "(shown)"],
  );
  const other = "/delivery/project/project-insights";
  assert.deepEqual(
    roles.map((role) => to(role, other)),
    [
      "/reports/project-insights/daily",
      "/reports/project-insights/overall",
      "/today?project=project-insights",
      "(shown)",
      "(shown)",
      "(shown)",
    ],
  );
  assert.equal(go("dev", other)?.missing, null, "the report stands in for it: no toast");
  assert.equal(go("sm", other)?.missing, null);
  assert.equal(
    to("dev", "/delivery/project/project%2Finsights?asOf=2026-10-02"),
    "/reports/project%2Finsights/daily?asOf=2026-10-02",
    "an id that was encoded in the link stays encoded, and the viewing day goes along",
  );
});

test("a manager is shown every page but Admin; an admin every page", () => {
  for (const link of [
    "/delivery/pod/pod-payments",
    "/delivery/program/program-digital",
    "/signals?view=risks",
    "/coordination",
  ]) {
    assert.equal(to("mgr", link), "(shown)", link);
    assert.equal(to("admin", link), "(shown)", link);
  }
  assert.equal(to("mgr", "/admin"), "/today");
  assert.equal(to("admin", "/admin"), "(shown)");
  assert.equal(to("dev", "/chat", true), "(shown)");
});

test("only a link that lands on plain Today names the page it asked for", () => {
  assert.equal(go("dev", "/signals")?.missing, "signals");
  assert.equal(go("sm", "/admin")?.missing, "admin");
  assert.equal(go("dev", "/coordination")?.missing, null, "Your asks stands in for it");
  // Every role has Delivery: a Delivery link never lands on Today with a note.
  for (const role of ["dev", "sm", "po"] as const) {
    for (const link of [
      "/delivery/program/program-digital",
      "/delivery/pod/pod-data",
      "/delivery/pod/pod-lonely",
    ]) {
      assert.equal(go(role, link)?.missing, null, `${role} ${link}`);
    }
  }
  assert.equal(go("sm", "/signals?view=risks")?.missing, null);
  assert.equal(
    go("exec", "/delivery/pod/pod-payments")?.missing,
    null,
    "its project stands in for it",
  );
});

test("a pod or workstream no project holds goes to the closest page that is left", () => {
  assert.equal(to("po", "/delivery/pod/pod-lonely"), "/delivery");
  assert.equal(to("po", "/delivery/workstream/ws-lonely"), "/delivery");
  assert.equal(to("sm", "/delivery/pod/pod-lonely"), "/today?pod=pod-lonely");
  assert.equal(to("exec", "/delivery/pod/pod-lonely"), "/delivery");
  assert.equal(to("po", "/delivery"), "(shown)");
  assert.equal(to("mgr", "/delivery"), "(shown)");
});

test("Signals views that were dropped open the role's own view; drift joined the risks", () => {
  assert.equal(to("mgr", "/signals?view=drift"), "/signals?view=risks");
  assert.equal(to("sm", "/signals?view=drift"), "/signals?view=flow");
  assert.equal(to("mgr", "/signals?view=everything"), "/signals");
  assert.equal(to("po", "/signals?view=feed&days=90"), "/signals?days=90");
  assert.equal(to("po", "/signals?view=flow&scope=pod:pod-payments"), "(shown)");
});

test("the viewing day goes along with every redirect, before the hash", () => {
  assert.equal(to("dev", "/coordination?asOf=2026-10-02"), "/today?asOf=2026-10-02#asks");
  assert.equal(
    to("sm", "/delivery/pod/pod-data?asOf=2026-10-02"),
    "/today?pod=pod-data&asOf=2026-10-02",
  );
  assert.equal(
    to("dev", "/delivery/pod/pod-storefront?asOf=2026-10-02"),
    "/delivery/project/project-checkout?asOf=2026-10-02",
  );
  assert.equal(
    to("sm", "/signals?view=risks&asOf=2026-10-02"),
    "/signals?view=flow&asOf=2026-10-02",
  );
  assert.equal(to("dev", "/admin?tab=gates&asOf=2026-10-02"), "/today?asOf=2026-10-02");
});

test("the toast says what was opened instead, and which other role of theirs has the page", () => {
  assert.equal(
    redirectToast("signals", "Developer"),
    "Opened Today: Signals isn't part of the developer view.",
  );
  assert.equal(
    redirectToast("admin", "Scrum Master"),
    "Opened Today: Admin isn't part of the scrum master view.",
  );
  assert.equal(roleOfferingPage("admin", ["mgr", "admin"], "mgr", false, rolePriority), "admin");
  assert.equal(roleOfferingPage("admin", ["mgr"], "mgr", false, rolePriority), null);
  assert.equal(
    roleOfferingPage("signals", ["dev", "mgr"], "dev", false, rolePriority),
    "mgr",
    "the most senior role that has it",
  );
  assert.equal(roleOfferingPage("chat", ["dev", "sm"], "dev", false, rolePriority), null);
});

test("palette rows go to Delivery's panel of each kind the role's Delivery lists", () => {
  const targets = (role: AppRole) => {
    const t = paletteTargets(access(role));
    return {
      program: t.program?.("p1") ?? null,
      project: t.project?.("p2") ?? null,
      workstream: t.workstream?.("w1") ?? null,
      pod: t.pod?.("d1") ?? null,
      person: t.person?.("d1") ?? null,
    };
  };
  assert.deepEqual(targets("mgr"), {
    program: "/delivery/program/p1",
    project: "/delivery/project/p2",
    workstream: "/delivery/workstream/w1",
    pod: "/delivery/pod/d1",
    person: "/delivery/pod/d1",
  });
  assert.deepEqual(targets("exec"), {
    program: "/delivery/program/p1",
    project: "/delivery/project/p2",
    workstream: "/delivery/workstream/w1",
    pod: null,
    person: null,
  });
  // Their own part only: which of these rows exist is the tree's (paletteRows.ownRows).
  for (const role of ["sm", "po", "dev"] as const) {
    assert.deepEqual(
      targets(role),
      {
        program: null,
        project: "/delivery/project/p2",
        workstream: null,
        pod: "/delivery/pod/d1",
        person: "/delivery/pod/d1",
      },
      role,
    );
  }
});

test("the assistant floats on every tab for the aggregate readers, never for a developer", () => {
  assert.deepEqual(
    (["dev", "sm", "po", "mgr", "exec", "admin"] as const).map((role) => access(role).assistant),
    [false, true, true, true, true, true],
  );
  // Roles combine: a developer who is also a scrum master has it.
  assert.equal(accessOf({ lens: ["dev", "sm"], chatEnabled: false }).assistant, true);
});
