import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dailyPath,
  hubPath,
  nodePath,
  podPath,
  podTabPath,
  portfolioPath,
  programPath,
  projectPath,
  projectTabPath,
  workstreamPath,
} from "./links.ts";

test("each thing opens in the hub, at its stable id, encoded", () => {
  assert.equal(projectPath("project-checkout"), "/projects/project-checkout");
  assert.equal(projectTabPath("project checkout", "scope"), "/projects/project%20checkout/scope");
  assert.equal(
    podTabPath("project-checkout", "pod/payments", "warnings"),
    "/projects/project-checkout/pods/pod%2Fpayments/warnings",
  );
  assert.equal(portfolioPath(), "/portfolio");
  assert.equal(portfolioPath("warnings"), "/portfolio/warnings");
  assert.equal(hubPath({ level: "all" }, "reports"), "/portfolio/reports");
  assert.equal(programPath("program digital"), "/portfolio/date?program=program%20digital");
  assert.equal(dailyPath("project-checkout"), "/projects/project-checkout/reports");
  assert.equal(
    dailyPath("project-checkout", "rep 1"),
    "/projects/project-checkout/reports?report=rep%201",
  );
});

test("a section's anchor opens the tab it lives on", () => {
  assert.equal(
    projectPath("project checkout", "timeline"),
    "/projects/project%20checkout/date#timeline",
  );
  assert.equal(projectPath("p", "warnings"), "/projects/p/warnings#warnings");
  assert.equal(projectPath("p", "questions"), "/projects/p/warnings#questions");
  assert.equal(projectPath("p", "gates"), "/projects/p/scope#gates");
  assert.equal(projectPath("p", "work"), "/projects/p/date#work");
  // An anchor no tab owns leaves the tab to the role's first.
  assert.equal(projectPath("p", "elsewhere"), "/projects/p#elsewhere");
});

test("a pod opens its part of a project; without one, its old address, which the router places", () => {
  assert.equal(podPath("pod-payments", undefined, "p"), "/projects/p/pods/pod-payments/date");
  assert.equal(
    podPath("pod-payments", "blockers", "p"),
    "/projects/p/pods/pod-payments/warnings#blockers",
  );
  assert.equal(podPath("pod/payments"), "/pods/pod%2Fpayments");
  assert.equal(podPath("pod-payments", "status"), "/pods/pod-payments#status");
});

test("a workstream opens in its first project's date, under Workstreams", () => {
  assert.equal(
    workstreamPath("ws-payments", ["project-checkout"]),
    "/projects/project-checkout/date?workstream=ws-payments#work",
  );
  assert.equal(workstreamPath("ws-lonely", []), "/delivery/workstream/ws-lonely");
  assert.deepEqual(
    (["program", "project", "pod"] as const).map((kind) => nodePath(kind, "x")),
    ["/portfolio/date?program=x", "/projects/x", "/pods/x"],
  );
  assert.equal(nodePath("pod", "x", ["p"]), "/projects/p/pods/x/date");
  assert.equal(nodePath("workstream", "w", ["p"]), "/projects/p/date?workstream=w#work");
});
