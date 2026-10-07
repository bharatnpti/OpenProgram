import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { DirectoryItemResponse } from "../api/schema";
import {
  chooseProgram,
  podsOf,
  podsOfPerson,
  programsOfProjects,
  projectsOf,
  projectsOfPerson,
  rankPrograms,
  rankProjects,
  runsPod,
  scopeToProgram,
} from "./scope.ts";

function item(over: Partial<DirectoryItemResponse> & { id: string }): DirectoryItemResponse {
  return {
    kind: "pod",
    name: over.id,
    description: null,
    code: null,
    metadata: {},
    rag: "green",
    source: "confirmed",
    program_ids: [],
    project_ids: [],
    workstream_ids: [],
    pod_ids: [],
    member_ids: [],
    task_ids: [],
    people: [],
    in_use: true,
    ...over,
  };
}

describe("a person's pods", () => {
  const payments = item({ id: "pod-payments", member_ids: ["U1006", "U1007"] });
  const identity = item({ id: "pod-identity", member_ids: ["U1008"] });
  const data = item({ id: "pod-data", metadata: { escalation_sm_member_id: "U1014" } });

  test("a member of a pod belongs to it", () => {
    assert.equal(runsPod(payments, "U1006"), true);
    assert.equal(runsPod(payments, "U1008"), false);
    assert.deepEqual(podsOfPerson([payments, identity, data], "U1006"), {
      pods: [payments],
      own: true,
    });
  });

  test("a scrum master named only as a pod's contact still runs it (the backend's rule)", () => {
    assert.equal(runsPod(data, "U1014"), true);
    assert.deepEqual(podsOfPerson([payments, identity, data], "U1014"), {
      pods: [data],
      own: true,
    });
  });

  test("a scrum master in no pod is offered every pod, and the screen is told", () => {
    assert.deepEqual(podsOfPerson([payments, identity, data], "U1099"), {
      pods: [payments, identity, data],
      own: false,
    });
    // A sign-in that names no member offers every pod too.
    assert.deepEqual(podsOfPerson([payments], null), { pods: [payments], own: false });
    assert.deepEqual(podsOf([payments, identity], "U1008"), [identity]);
  });
});

describe("a person's projects and programs", () => {
  const checkout = item({ id: "project-checkout", kind: "project", program_ids: ["program-a"] });
  const insights = item({ id: "project-insights", kind: "project", program_ids: ["program-b"] });
  const payments = item({ id: "pod-payments", project_ids: ["project-checkout"] });

  test("the projects of their pods, or every project with a note that it is a fallback", () => {
    assert.deepEqual(projectsOfPerson([checkout, insights], [payments], true), {
      projects: [checkout],
      own: true,
      fallback: "none",
    });
    // In no pod of their own: every project, also one no pod works on yet.
    assert.deepEqual(projectsOfPerson([checkout, insights], [payments], false), {
      projects: [checkout, insights],
      own: false,
      fallback: "no-pod",
    });
    const idle = item({ id: "pod-idle", project_ids: [] });
    assert.deepEqual(projectsOfPerson([checkout, insights], [idle], true), {
      projects: [checkout, insights],
      own: false,
      fallback: "no-project",
    });
    assert.deepEqual(projectsOf([checkout, insights], [payments]), [checkout]);
  });

  test("the programs those projects belong to", () => {
    const a = item({ id: "program-a", kind: "program" });
    const b = item({ id: "program-b", kind: "program" });
    assert.deepEqual(programsOfProjects([a, b], [checkout]), [a]);
    assert.deepEqual(programsOfProjects([a, b], []), []);
  });
});

describe("several programs", () => {
  const a = item({ id: "a", kind: "program", name: "Alpha", rag: "green" });
  const b = item({ id: "b", kind: "program", name: "Beta", rag: "red" });
  const c = item({ id: "c", kind: "program", name: "Gamma", rag: null });
  const d = item({ id: "d", kind: "program", name: "Delta", rag: "red" });

  test("worst first, silence above green, then by name", () => {
    assert.deepEqual(
      rankPrograms([a, b, c, d]).map((p) => p.id),
      ["b", "d", "c", "a"],
    );
  });

  test("the one asked for if it exists, else the worst", () => {
    const ranked = rankPrograms([a, b, c]);
    assert.equal(chooseProgram(ranked, "a")?.id, "a");
    assert.equal(chooseProgram(ranked, "nope")?.id, "b");
    assert.equal(chooseProgram(ranked, null)?.id, "b");
    assert.equal(chooseProgram([], "a"), null);
  });

  const projects = [
    item({ id: "p1", kind: "project", program_ids: ["a"] }),
    item({ id: "p2", kind: "project", program_ids: ["b"] }),
  ];
  const workstreams = [
    item({ id: "w1", kind: "workstream", project_ids: ["p1"] }),
    item({ id: "w2", kind: "workstream", project_ids: ["p2"] }),
  ];
  const pods = [
    item({ id: "pod1", project_ids: ["p1"] }),
    item({ id: "pod2", project_ids: ["p2"] }),
    item({ id: "pod3", project_ids: [] }),
  ];

  test("with one program the whole directory is the portfolio, orphans included", () => {
    const all = { projects, workstreams, pods };
    assert.equal(scopeToProgram("a", 1, all), all);
    assert.equal(scopeToProgram(null, 3, all), all);
  });

  test("with several, a program shows its own projects and what those hold", () => {
    const scoped = scopeToProgram("a", 2, { projects, workstreams, pods });
    assert.deepEqual(
      scoped.projects.map((x) => x.id),
      ["p1"],
    );
    assert.deepEqual(
      scoped.workstreams.map((x) => x.id),
      ["w1"],
    );
    assert.deepEqual(
      scoped.pods.map((x) => x.id),
      ["pod1"],
    );
  });
});

describe("which of a person's projects opens first", () => {
  const checkout = item({
    id: "project-checkout",
    name: "Checkout Revamp",
    kind: "project",
    rag: "amber",
  });
  const identity = item({
    id: "project-identity",
    name: "Identity Platform",
    kind: "project",
    rag: "amber",
  });
  const insights = item({
    id: "project-insights",
    name: "Customer Insights",
    kind: "project",
    rag: "amber",
  });
  const pods = [
    item({ id: "pod-data", project_ids: ["project-insights"] }),
    item({ id: "pod-identity", project_ids: ["project-identity"] }),
  ];

  test("the worst colour comes first, silence above green", () => {
    const worst = [
      { ...checkout, rag: "green" as const },
      { ...identity, rag: "red" as const },
      { ...insights, rag: "unknown" as const },
    ];
    assert.deepEqual(
      rankProjects(worst, pods).map((p) => p.id),
      ["project-identity", "project-insights", "project-checkout"],
    );
  });

  test("equally bad projects go by the person's first pod, then by name", () => {
    assert.deepEqual(
      rankProjects([identity, insights], pods).map((p) => p.id),
      ["project-insights", "project-identity"],
    );
    assert.deepEqual(
      rankProjects([identity, insights], [...pods].reverse()).map((p) => p.id),
      ["project-identity", "project-insights"],
    );
    // A project none of the pods works on is last of its colour, then A to Z.
    assert.deepEqual(
      rankProjects([checkout, identity], pods).map((p) => p.id),
      ["project-identity", "project-checkout"],
    );
  });

  test("the list asked about is not reordered in place", () => {
    const given = [insights, identity];
    rankProjects(given, pods);
    assert.deepEqual(
      given.map((p) => p.id),
      ["project-insights", "project-identity"],
    );
  });
});
