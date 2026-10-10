import assert from "node:assert/strict";
import { test } from "node:test";

import type { DirectoryItemResponse } from "../../api/schema";
import { accessOf, capabilitiesFor, paletteTargets } from "../../app/access.ts";
import type { AppRole } from "../../app/roleWords.ts";
import {
  askRow,
  directoryRows,
  matchRows,
  noPodNote,
  paletteMark,
  peopleRows,
  screenRows,
} from "./paletteRows.ts";

const targetsOf = (role: AppRole) =>
  paletteTargets(accessOf({ lens: [role], chatEnabled: false }), capabilitiesFor([role]));
const manager = targetsOf("mgr");

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

test("for a manager every node opens its Delivery panel, and an empty workstream is left out", () => {
  const rows = directoryRows(directory, manager);
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

test("a node's mark is its own status from the directory, in words, never its kind's colour", () => {
  // The QA finding: Payments Pod was amber on Delivery, Today and Overall, and
  // the palette drew it green, because every pod's dot was green.
  const coloured = {
    programs: [item("program", "program-platform", "Digital Platform Program", { rag: "red" })],
    projects: [item("project", "project-checkout", "Checkout Revamp", { rag: "green" })],
    workstreams: [item("workstream", "ws-payments", "Payments API", { rag: "amber" })],
    pods: [
      item("pod", "pod-payments", "Payments Pod", { rag: "amber" }),
      item("pod", "pod-storefront", "Storefront Pod", { rag: "unknown" }),
      item("pod", "pod-data", "Data Pod"),
    ],
  };
  const marks = directoryRows(coloured, manager).map((row) => [row.label, paletteMark(row)]);
  assert.deepEqual(marks, [
    ["Digital Platform Program", { kind: "status", tone: "danger", words: "Off track" }],
    ["Checkout Revamp", { kind: "status", tone: "success", words: "On track" }],
    ["Payments API", { kind: "status", tone: "warning", words: "At risk" }],
    ["Payments Pod", { kind: "status", tone: "warning", words: "At risk" }],
    // Nobody reported one, or none was ever rolled up: grey, never green.
    ["Storefront Pod", { kind: "status", tone: "neutral", words: "Status unknown" }],
    ["Data Pod", { kind: "status", tone: "neutral", words: "Status unknown" }],
  ]);
});

test("a screen, the assistant and a person have no status, so no status colour", () => {
  assert.deepEqual(paletteMark({ kind: "screen" }), { kind: "screen" });
  assert.deepEqual(paletteMark({ kind: "ask" }), { kind: "ask" });
  // Even a status on a person's row would not be drawn: the navigator gives people none.
  assert.deepEqual(paletteMark({ kind: "person", rag: "green" }), { kind: "person" });
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
    manager.person,
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
    ...directoryRows(directory, manager),
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

const crew = [
  { id: "U1007", name: "Kai Thompson", title: "Backend Engineer" },
  { id: "U1001", name: "Asha Rao", title: null },
  { id: "U1011", name: "Elena Fischer", title: "Director" },
];
const twoPods = [
  item("pod", "pod-identity", "Identity Pod", { member_ids: ["U1001", "U1007"] }),
  item("pod", "pod-payments", "Payments Pod", { member_ids: ["U1001"] }),
];

test("a person is found by any of their pods, and the row names the pod that matched", () => {
  const rows = peopleRows(crew, twoPods, manager.person);
  const asha = rows.find((row) => row.label === "Asha Rao");
  // Alphabetically Identity Pod is her first pod, so by default that is what the row opens.
  assert.equal(asha?.hint, "Person · Identity Pod and 1 more");
  assert.equal(asha?.to, "/delivery/pod/pod-identity");

  const found = matchRows(rows, "pay");
  assert.deepEqual(
    found.map((row) => [row.label, row.hint, row.to]),
    [["Asha Rao", "Person · Payments Pod and 1 more", "/delivery/pod/pod-payments"]],
  );
  // A query that matches their first pod leaves the row as it is.
  assert.equal(matchRows(rows, "identity")[0].to, "/delivery/pod/pod-identity");
  assert.deepEqual(
    matchRows(rows, "asha pay").map((row) => row.label),
    ["Asha Rao"],
  );
  assert.deepEqual(matchRows(rows, "kai pay"), [], "every word still has to match");
});

test("a person in no pod is never offered, and the empty result says why", () => {
  assert.deepEqual(matchRows(peopleRows(crew, twoPods, manager.person), "elena"), []);
  assert.equal(
    noPodNote(crew, twoPods, "elena"),
    "Elena Fischer is in no pod, so there is no pod page to open.",
  );
  assert.equal(noPodNote(crew, twoPods, "kai"), null, "someone in a pod is not the reason");
  assert.equal(noPodNote(crew, twoPods, "nobody like this"), null);
  assert.equal(noPodNote(crew, twoPods, "  "), null);
});

const rowsFor = (role: AppRole) =>
  [
    ...directoryRows(directory, targetsOf(role)),
    ...peopleRows(crew, directory.pods, targetsOf(role).person),
  ].map((row) => [row.kind, row.label, row.to]);

test("an executive gets programs, projects and workstreams in Delivery, and no pods or people", () => {
  assert.deepEqual(rowsFor("exec"), [
    ["program", "Digital Platform Program", "/delivery/program/program-platform"],
    ["project", "Checkout Revamp", "/delivery/project/project-checkout"],
    ["workstream", "Payments API", "/delivery/workstream/ws-payments"],
  ]);
});

test("a scrum master's pods and people open the pod on Today", () => {
  assert.deepEqual(rowsFor("sm"), [
    ["pod", "Storefront Pod", "/today?pod=pod-storefront"],
    ["pod", "Payments Pod", "/today?pod=pod-payments"],
    ["person", "Asha Rao", "/today?pod=pod-payments"],
    ["person", "Kai Thompson", "/today?pod=pod-payments"],
  ]);
  // Found by another of their pods, a person opens that pod there too.
  const found = matchRows(peopleRows(crew, directory.pods, targetsOf("sm").person), "storefront");
  assert.deepEqual(
    found.map((row) => [row.label, row.to]),
    [["Asha Rao", "/today?pod=pod-storefront"]],
  );
});

test("a product owner's projects open on Today; a developer's in Reports; neither gets people", () => {
  assert.deepEqual(rowsFor("po"), [
    ["project", "Checkout Revamp", "/today?project=project-checkout"],
  ]);
  assert.deepEqual(rowsFor("dev"), [
    ["project", "Checkout Revamp", "/reports/project-checkout/daily"],
  ]);
});

test("the assistant's row: its name with no query, and a query becomes the question to send", () => {
  const label = "Ask Ora";
  assert.deepEqual(askRow("", label), {
    key: "ask",
    kind: "ask",
    label,
    hint: "A question about the delivery data",
    to: "",
    ask: { draft: "" },
  });
  assert.equal(askRow("ask", label).key, "ask", "a query naming the row finds it");
  assert.equal(askRow(" ORA ", label).key, "ask");
  const question = askRow("  who is blocked?  ", label);
  assert.equal(question.key, "ask:query");
  assert.equal(question.label, "Ask Ora: “who is blocked?”");
  assert.deepEqual(question.ask, { draft: "who is blocked?" });
});
