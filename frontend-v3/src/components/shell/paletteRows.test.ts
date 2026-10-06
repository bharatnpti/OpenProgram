import assert from "node:assert/strict";
import { test } from "node:test";

import type { DirectoryItemResponse } from "../../api/schema";
import { directoryRows, matchRows, peopleRows, screenRows } from "./paletteRows.ts";

const item = (
  kind: DirectoryItemResponse["kind"],
  id: string,
  name: string,
  extra: Partial<DirectoryItemResponse> = {},
): DirectoryItemResponse => ({
  id,
  kind,
  name,
  description: null,
  code: null,
  metadata: {},
  rag: null,
  source: null,
  program_ids: [],
  project_ids: [],
  workstream_ids: [],
  pod_ids: [],
  member_ids: [],
  task_ids: [],
  people: [],
  in_use: true,
  ...extra,
});

const directory = {
  programs: [item("program", "program-platform", "Digital Platform Program")],
  projects: [
    item("project", "project-checkout", "Checkout Revamp", { program_ids: ["program-platform"] }),
  ],
  workstreams: [
    item("workstream", "ws-payments", "Payments API", { project_ids: ["project-checkout"] }),
    item("workstream", "ws-empty", "Empty Stream", { in_use: false }),
  ],
  pods: [
    item("pod", "pod-storefront", "Storefront Pod", {
      project_ids: ["project-checkout"],
      member_ids: ["U1001"],
    }),
    item("pod", "pod-payments", "Payments Pod", {
      project_ids: ["project-checkout"],
      member_ids: ["U1001", "U1007"],
    }),
  ],
};

test("every node opens its Delivery panel, and an empty workstream is left out", () => {
  const rows = directoryRows(directory);
  assert.deepEqual(
    rows.map((row) => [row.label, row.hint, row.to]),
    [
      ["Digital Platform Program", "Program", "/delivery/program/program-platform"],
      [
        "Checkout Revamp",
        "Project · Digital Platform Program",
        "/delivery/project/project-checkout",
      ],
      ["Payments API", "Workstream · Checkout Revamp", "/delivery/workstream/ws-payments"],
      ["Storefront Pod", "Pod · Checkout Revamp", "/delivery/pod/pod-storefront"],
      ["Payments Pod", "Pod · Checkout Revamp", "/delivery/pod/pod-payments"],
    ],
  );
});

test("a person opens their pod; someone in no pod or with no name is not offered", () => {
  const rows = peopleRows(
    [
      { id: "U1007", name: "Kai Thompson", title: "Backend Engineer" },
      { id: "U1001", name: "Asha Rao", title: null },
      { id: "U1011", name: "Elena Fischer", title: "Director" },
      { id: "U1099", name: "U1099" },
      { id: "U1007", name: "Kai Thompson" },
    ],
    directory.pods,
  );
  assert.deepEqual(
    rows.map((row) => [row.label, row.hint, row.to]),
    [
      ["Asha Rao", "Person · Payments Pod and 1 more", "/delivery/pod/pod-payments"],
      ["Kai Thompson", "Backend Engineer · Payments Pod", "/delivery/pod/pod-payments"],
    ],
  );
});

test("a query keeps rows holding every word, names starting with it first", () => {
  const rows = [
    ...screenRows([
      { to: "/today", label: "Today", hint: "Your day" },
      { to: "/delivery", label: "Delivery", hint: "Programs, projects, workstreams and pods" },
    ]),
    ...directoryRows(directory),
  ];
  assert.deepEqual(
    matchRows(rows, "pay").map((row) => row.label),
    ["Payments API", "Payments Pod"],
  );
  assert.deepEqual(
    matchRows(rows, "pod check").map((row) => row.label),
    ["Storefront Pod", "Payments Pod"],
    "words may match the hint",
  );
  assert.deepEqual(
    matchRows(rows, "de")
      .map((row) => row.label)
      .slice(0, 1),
    ["Delivery"],
  );
  assert.deepEqual(matchRows(rows, "nothing like this"), []);
  assert.equal(matchRows(rows, "", 3).length, 3);
  assert.equal(matchRows(rows, "  ")[0].label, "Today");
});
