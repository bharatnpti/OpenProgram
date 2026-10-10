import assert from "node:assert/strict";
import { test } from "node:test";

import type { JiraWritesChangeDto, JiraWritesResponse } from "../../api/schema";
import {
  CONFIRM_MASTER_ON,
  KIND_ORDER,
  KIND_WORDS,
  changeLine,
  checkinWritebackLine,
  kindBody,
  kindOf,
  kindStateLine,
  masterLine,
  parseProjectKeys,
  projectProblems,
  projectsBody,
  projectsWords,
  readinessCreateLine,
  sourceWords,
} from "./jiraWritesWords.ts";

function writes(
  master: boolean,
  kinds: Partial<Record<"checkin_updates" | "console_moves" | "readiness_create", boolean>> = {},
  projects: string[] = [],
): JiraWritesResponse {
  const on = { checkin_updates: true, console_moves: true, readiness_create: false, ...kinds };
  return {
    master: { on: master, source: "admin" },
    kinds: KIND_ORDER.map((kind) => ({
      kind,
      on: on[kind],
      source: kind in kinds ? "admin" : "default",
      effective: master && on[kind],
    })),
    create_projects: { own_project: true, projects, source: "default" },
    changes: [],
  };
}

function change(overrides: Partial<JiraWritesChangeDto>): JiraWritesChangeDto {
  return {
    at: "2026-10-10T09:00:00Z",
    by: "U1001",
    by_name: "Asha Rao",
    setting: "readiness_create",
    before_on: false,
    after_on: true,
    before_projects: null,
    after_projects: null,
    before_source: "default",
    ...overrides,
  };
}

test("the panel's three kinds are named as the brief names them", () => {
  assert.deepEqual(
    KIND_ORDER.map((kind) => KIND_WORDS[kind].label),
    [
      "Update tickets from check-ins",
      "Move tickets from task updates",
      "Create release-readiness issues",
    ],
  );
  // Each says what still guards it.
  assert.match(KIND_WORDS.checkin_updates.guards.join(" "), /consent/);
  assert.match(KIND_WORDS.checkin_updates.guards.join(" "), /assignee/);
  assert.match(KIND_WORDS.checkin_updates.guards.join(" "), /merge request/);
  assert.match(KIND_WORDS.readiness_create.guards.join(" "), /pressing Create/);
});

test("the master's line and its confirmation say plainly what turning it on does", () => {
  assert.equal(
    masterLine({ on: false, source: "default" }),
    "Nothing is written to Jira, whatever is switched on below.",
  );
  assert.match(masterLine({ on: true, source: "admin" }), /for the kinds switched on below/);
  assert.equal(CONFIRM_MASTER_ON.title, "Turn on Jira writes for this tenant?");
  assert.match(CONFIRM_MASTER_ON.description, /only for the kinds switched on below/);
});

test("a kind on while the master is off is held, and says so", () => {
  const off = writes(false);
  const on = writes(true, { checkin_updates: false });

  assert.equal(
    kindStateLine(kindOf(off, "checkin_updates"), off.master),
    "On, but nothing is written while Jira writes for this tenant is off.",
  );
  assert.equal(
    kindStateLine(kindOf(on, "checkin_updates"), on.master),
    "Off: nothing of this kind is written to Jira.",
  );
  assert.equal(
    kindStateLine(kindOf(on, "console_moves"), on.master),
    "On: written to Jira, behind the guards below.",
  );
});

test("sources are said in words", () => {
  assert.equal(sourceWords("default"), "The default.");
  assert.equal(sourceWords("env"), "Set in the deployment's settings.");
  assert.equal(sourceWords("admin"), "Set by an admin.");
});

test("one switch's request sets that switch only", () => {
  assert.deepEqual(kindBody("checkin_updates", false), { checkin_updates: false });
  assert.deepEqual(kindBody("console_moves", true), { console_moves: true });
  assert.deepEqual(kindBody("readiness_create", true), { readiness_create: true });
});

test("project keys are tidied and checked as the server does", () => {
  assert.deepEqual(parseProjectKeys(" sec, OPS;ops  chk "), ["SEC", "OPS", "CHK"]);
  assert.deepEqual(projectProblems(true, "SEC, OPS"), []);
  assert.deepEqual(projectProblems(true, "S-EC"), [
    "A Jira project key is capital letters and digits, such as CHK: S-EC.",
  ]);
  assert.match(projectProblems(false, " ")[0] ?? "", /^Allow at least one project/);
  assert.equal(
    projectProblems(true, Array.from({ length: 21 }, (_, i) => `P${i}X`).join(",")).length,
    1,
  );
});

test("the projects form sends only a change", () => {
  const current = { own_project: true, projects: ["SEC"], source: "admin" as const };

  assert.equal(projectsBody(current, true, "sec"), null);
  assert.deepEqual(projectsBody(current, true, "SEC, OPS"), {
    own_project: true,
    projects: ["SEC", "OPS"],
  });
  assert.deepEqual(projectsBody(current, false, "SEC"), { own_project: false, projects: ["SEC"] });
});

test("allowed projects in words", () => {
  assert.equal(projectsWords({ own_project: true, projects: [] }), "the scope's own Jira project");
  assert.equal(
    projectsWords({ own_project: true, projects: ["SEC", "OPS"] }),
    "the scope's own Jira project and SEC, OPS",
  );
  assert.equal(projectsWords({ own_project: false, projects: ["SEC"] }), "only SEC");
});

test("a change says who changed what, from what", () => {
  assert.equal(
    changeLine(change({})),
    "Asha Rao turned Create release-readiness issues on (it was off, the default).",
  );
  assert.equal(
    changeLine(
      change({ setting: "master", before_on: true, after_on: false, before_source: "env" }),
    ),
    "Asha Rao turned Jira writes for this tenant off (it was on, from the deployment's settings).",
  );
  assert.equal(
    changeLine(change({ setting: "checkin_updates", before_on: true, after_on: true })),
    "Asha Rao set Update tickets from check-ins to on (it was on, the default).",
  );
  assert.equal(
    changeLine(
      change({
        by_name: null,
        setting: "create_projects",
        before_on: null,
        after_on: null,
        before_projects: { own_project: true, projects: [] },
        after_projects: { own_project: true, projects: ["SEC"] },
      }),
    ),
    "U1001 set Projects new issues may be created in to the scope's own Jira project and SEC (it was the scope's own Jira project, the default).",
  );
});

test("Release readiness says the effective state of creating issues", () => {
  assert.equal(
    readinessCreateLine(writes(true)),
    "Create release-readiness issues is off: drafts stay drafts, and nothing is created in Jira.",
  );
  assert.equal(
    readinessCreateLine(writes(false, { readiness_create: true })),
    "Create release-readiness issues is on, but Jira writes for this tenant is off, so nothing is created in Jira.",
  );
  assert.equal(
    readinessCreateLine(writes(true, { readiness_create: true }, ["SEC"])),
    "Create release-readiness issues is on: a person can press Create on a draft, and the issue goes to the scope's own Jira project and SEC.",
  );
});

test("Check-ins says whether a check-in can move a ticket", () => {
  assert.match(checkinWritebackLine(writes(false)), /^Jira writes for this tenant is off/);
  assert.match(
    checkinWritebackLine(writes(true, { checkin_updates: false })),
    /^Update tickets from check-ins is off/,
  );
  assert.match(checkinWritebackLine(writes(true)), /a member's consent decides/);
});
