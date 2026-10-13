import assert from "node:assert/strict";
import { test } from "node:test";

import { dailyPath, nodePath, podPath, programPath, projectPath, workstreamPath } from "./links.ts";

test("each thing opens on its own page, at its stable id, encoded", () => {
  assert.equal(projectPath("project-checkout"), "/projects/project-checkout");
  assert.equal(
    projectPath("project checkout", "timeline"),
    "/projects/project%20checkout#timeline",
  );
  assert.equal(podPath("pod/payments"), "/pods/pod%2Fpayments");
  assert.equal(programPath("program digital"), "/portfolio?program=program%20digital");
  assert.equal(dailyPath("project-checkout"), "/reports/project-checkout/daily");
});

test("a workstream opens in its first project's Work, else its old address resolves it", () => {
  assert.equal(
    workstreamPath("ws-payments", ["project-checkout"]),
    "/projects/project-checkout#work",
  );
  assert.equal(workstreamPath("ws-lonely", []), "/delivery/workstream/ws-lonely");
  assert.deepEqual(
    (["program", "project", "pod"] as const).map((kind) => nodePath(kind, "x")),
    ["/portfolio?program=x", "/projects/x", "/pods/x"],
  );
  assert.equal(nodePath("workstream", "w", ["p"]), "/projects/p#work");
});
