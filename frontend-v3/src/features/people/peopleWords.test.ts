import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { GraphTreeDto } from "../../api/schema";
import { appRoles } from "../../app/roleWords.ts";
import { podTeam, whatEachRoleSees } from "./peopleWords.ts";

describe("what each role sees", () => {
  test("one row per role, each with the sidebar pages its access offers", () => {
    const rows = whatEachRoleSees();
    assert.deepEqual(
      rows.map((row) => row.role),
      appRoles,
    );
    const pages = Object.fromEntries(rows.map((row) => [row.role, row.pages]));
    assert.deepEqual(pages.dev, ["Today", "Check-in", "Projects"]);
    assert.equal(pages.exec.includes("People & roles"), false);
    assert.equal(pages.po.includes("People & roles"), true);
    assert.equal(pages.admin.includes("Admin"), true);
    assert.equal(pages.mgr.includes("Admin"), false);
    for (const row of rows) assert.ok(row.sees.length > 20, row.role);
  });

  test("an executive's row names no person, only counts and teams", () => {
    const exec = whatEachRoleSees().find((row) => row.role === "exec");
    assert.match(exec?.sees ?? "", /never a person's name/);
  });
});

const tree = (): GraphTreeDto =>
  ({
    root: { id: "program-1", kind: "program", name: "Programme", metadata: {} },
    nodes: [
      { id: "pod-pay", kind: "pod", name: "Payments Pod", metadata: {} },
      { id: "pod-x", kind: "pod", name: "Other Pod", metadata: {} },
      {
        id: "U2",
        kind: "developer",
        name: "Noah Weber",
        metadata: { title: "Backend Engineer", app_roles: "dev", timezone: "Europe/Berlin" },
      },
      {
        id: "U1",
        kind: "developer",
        name: "Ira Novak",
        metadata: { title: "Scrum Master", app_roles: "sm" },
      },
      { id: "U3", kind: "developer", name: "Omar Haddad", metadata: {} },
      { id: "task-1", kind: "task", name: "A task", metadata: {} },
    ],
    edges: [
      {
        from_node_id: "pod-pay",
        to_node_id: "U2",
        kind: "contains",
        valid_from: null,
        valid_to: null,
      },
      {
        from_node_id: "pod-pay",
        to_node_id: "U1",
        kind: "contains",
        valid_from: null,
        valid_to: null,
      },
      {
        from_node_id: "pod-pay",
        to_node_id: "task-1",
        kind: "contains",
        valid_from: null,
        valid_to: null,
      },
      {
        from_node_id: "pod-x",
        to_node_id: "U3",
        kind: "contains",
        valid_from: null,
        valid_to: null,
      },
    ],
  }) as GraphTreeDto;

describe("a pod's people from the team graph", () => {
  test("the people it contains, by name, with title and console roles, no time zone", () => {
    assert.deepEqual(podTeam([undefined, tree()], "pod-pay"), [
      // The title already says the role: said once.
      { id: "U1", name: "Ira Novak", line: "Scrum Master" },
      { id: "U2", name: "Noah Weber", line: "Backend Engineer · Developer" },
    ]);
  });

  test("null when no tree holds the pod", () => {
    assert.equal(podTeam([tree()], "pod-none"), null);
    assert.deepEqual(podTeam([tree()], "pod-x"), [{ id: "U3", name: "Omar Haddad", line: "" }]);
    assert.equal(podTeam([], "pod-pay"), null);
  });
});
