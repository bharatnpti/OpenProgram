import assert from "node:assert/strict";
import { test } from "node:test";

import { carriedQuery, isHubTab, parseHub, placePath, tabOfAnchor } from "./hubPaths.ts";

test("each place has one address, its ids encoded, the tab after it", () => {
  assert.equal(placePath({ level: "all" }), "/portfolio");
  assert.equal(placePath({ level: "all" }, "warnings"), "/portfolio/warnings");
  assert.equal(placePath({ level: "project", projectId: "p 1" }, "date"), "/projects/p%201/date");
  assert.equal(
    placePath({ level: "pod", projectId: "p-1", podId: "pod/x" }, "scope"),
    "/projects/p-1/pods/pod%2Fx/scope",
  );
  assert.equal(placePath({ level: "pod", projectId: "p-1", podId: "x" }), "/projects/p-1/pods/x");
});

test("a path is read back to its place and tab; the bare landing and other pages are not the hub's", () => {
  assert.deepEqual(parseHub("/portfolio/date"), {
    place: { level: "all" },
    tab: "date",
    rest: null,
  });
  assert.deepEqual(parseHub("/portfolio"), { place: { level: "all" }, tab: null, rest: null });
  assert.deepEqual(parseHub("/projects/p%201/reports"), {
    place: { level: "project", projectId: "p 1" },
    tab: "reports",
    rest: null,
  });
  assert.deepEqual(parseHub("/projects/p-1/pods/x/warnings"), {
    place: { level: "pod", projectId: "p-1", podId: "x" },
    tab: "warnings",
    rest: null,
  });
  // Anything after the place that is not a tab is kept for the redirects to read.
  assert.deepEqual(parseHub("/projects/p-1/daily"), {
    place: { level: "project", projectId: "p-1" },
    tab: null,
    rest: "daily",
  });
  assert.deepEqual(parseHub("/projects/p-1/date/more"), {
    place: { level: "project", projectId: "p-1" },
    tab: null,
    rest: "date/more",
  });
  // "pods" alone, with no pod, is not a pod.
  assert.equal(parseHub("/projects/p-1/pods")?.place.level, "project");
  for (const path of ["/projects", "/today", "/pods/x", "/warnings", "/"]) {
    assert.equal(parseHub(path), null, path);
  }
  assert.equal(isHubTab("scope"), true);
  assert.equal(isHubTab("daily"), false);
  assert.equal(isHubTab(undefined), false);
});

test("an anchor sent before the tabs opens the tab it lives on", () => {
  const project = (hash: string) => tabOfAnchor("project", hash);
  assert.deepEqual(
    [
      "#timeline",
      "#stages",
      "#work",
      "#warnings",
      "#questions",
      "#scope",
      "#gates",
      "#hygiene",
    ].map(project),
    ["date", "date", "date", "warnings", "warnings", "scope", "scope", "scope"],
  );
  assert.equal(project("#nothing"), null);
  assert.equal(project(""), null);
  assert.deepEqual(
    ["#checkins", "#blockers", "#status", "#people", "#dates"].map((hash) =>
      tabOfAnchor("pod", hash),
    ),
    ["warnings", "warnings", "warnings", "warnings", "date"],
  );
  assert.equal(tabOfAnchor("pod", "#constructor"), null, "no inherited key is a tab");
});

test("a move carries the viewing day; the release while the project stays; the programme between All tabs", () => {
  const search = "?asOf=2026-10-02&release=rel-1&row=pod:x&program=g&report=r&q=pay";
  const all = { place: { level: "all" as const }, tab: "date" as const };
  const project = { place: { level: "project" as const, projectId: "p" }, tab: "date" as const };
  const pod = {
    place: { level: "pod" as const, projectId: "p", podId: "x" },
    tab: "date" as const,
  };
  const other = { place: { level: "project" as const, projectId: "q" }, tab: "date" as const };
  // Picking a tab, or a pod of the same project, keeps the release.
  assert.equal(
    carriedQuery(search, project, { ...project, tab: "scope" }),
    "?asOf=2026-10-02&release=rel-1",
  );
  assert.equal(carriedQuery(search, project, pod), "?asOf=2026-10-02&release=rel-1");
  // Another project: its own releases, so only the day goes along.
  assert.equal(carriedQuery(search, project, other), "?asOf=2026-10-02");
  assert.equal(
    carriedQuery(search, all, { ...all, tab: "warnings" }),
    "?asOf=2026-10-02&program=g",
  );
  assert.equal(carriedQuery(search, all, project), "?asOf=2026-10-02");
  assert.equal(carriedQuery("", project, pod), "");
});
