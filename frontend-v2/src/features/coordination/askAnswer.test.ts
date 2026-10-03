import assert from "node:assert/strict";
import { test } from "node:test";

import { answerLines, sourceChips } from "./askAnswer.ts";

test("chips show a person's name, an issue key and a pod's name, never the raw id", () => {
  const chips = sourceChips({
    references: ["U0AA1OMAR01", "CHK-8", "pod-data", "program-platform"],
    sources: [
      { id: "U0AA1OMAR01", kind: "developer", label: "Omar Haddad" },
      { id: "CHK-8", kind: "task", label: "CHK-8" },
      { id: "pod-data", kind: "pod", label: "Data Pod" },
      { id: "program-platform", kind: "program", label: "Digital Platform Program" },
    ],
  });

  assert.deepEqual(
    chips.map((chip) => chip.label),
    ["Omar Haddad", "CHK-8", "Data Pod", "Digital Platform Program"],
  );
  assert.equal(chips[0].title, "developer · U0AA1OMAR01");
});

test("a chip links to its Delivery panel only for the kinds that have one", () => {
  const chips = sourceChips({
    references: [],
    sources: [
      { id: "pod-data", kind: "pod", label: "Data Pod" },
      { id: "acme/storefront-web", kind: "repo", label: "acme/storefront-web" },
      { id: "U0AA1OMAR01", kind: "developer", label: "Omar Haddad" },
    ],
  });

  assert.deepEqual(
    chips.map((chip) => chip.to),
    ["/delivery/pod/pod-data", undefined, undefined],
  );
});

test("an unmatched reference keeps its id, and an older server's ids show as they are", () => {
  const unmatched = sourceChips({
    references: ["U0AA1IRA01"],
    sources: [{ id: "U0AA1IRA01", kind: null, label: null }],
  });
  const older = sourceChips({ references: ["pod-data", "pod-data"] });

  assert.deepEqual(
    unmatched.map((chip) => [chip.label, chip.title, chip.to]),
    [["U0AA1IRA01", "U0AA1IRA01", undefined]],
  );
  assert.deepEqual(
    older.map((chip) => chip.label),
    ["pod-data"],
  );
});

test("an answer splits into its verdict, its bullets and its closing line", () => {
  const lines = answerLines(
    "Program is red because:\n• 1 open blocker: CHK-8 (Zoe Almeida)\n- Partial updates: Omar Haddad\n\nStorefront is green.",
  );

  assert.deepEqual(lines, [
    { kind: "text", text: "Program is red because:" },
    { kind: "bullet", text: "1 open blocker: CHK-8 (Zoe Almeida)" },
    { kind: "bullet", text: "Partial updates: Omar Haddad" },
    { kind: "text", text: "Storefront is green." },
  ]);
});
