import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  acceptsAsOf,
  currentServerToday,
  DATED_READS,
  learnServerToday,
  subscribeServerToday,
  withViewingAsOf,
} from "./asOf.ts";

type Operation = { parameters?: { name: string; in: string }[] };
const spec = JSON.parse(readFileSync(new URL("./openapi.json", import.meta.url), "utf8")) as {
  paths: Record<string, Record<string, Operation>>;
};

test("the dated reads are exactly the GET paths whose OpenAPI entry takes as_of", () => {
  const fromSpec = Object.entries(spec.paths)
    .filter(([, operations]) =>
      (operations.get?.parameters ?? []).some((p) => p.in === "query" && p.name === "as_of"),
    )
    .map(([path]) => path)
    .sort();
  assert.deepEqual([...DATED_READS].sort(), fromSpec);
});

test("a dated read asks for the viewing day; everything else is left alone", () => {
  const day = "2026-09-28";
  assert.equal(withViewingAsOf("/pods", "GET", day), "/pods?as_of=2026-09-28");
  assert.equal(
    withViewingAsOf("/pods/pod%20one/checkins", undefined, day),
    "/pods/pod%20one/checkins?as_of=2026-09-28",
  );
  assert.equal(
    withViewingAsOf("/portfolio/heatmap?program_root_id=p1", "GET", day),
    "/portfolio/heatmap?program_root_id=p1&as_of=2026-09-28",
  );
  assert.equal(
    withViewingAsOf("/projects/p1/requirements?as_of=2026-09-01&days=30", "GET", day),
    "/projects/p1/requirements?as_of=2026-09-01&days=30",
    "a read that names its own day keeps it",
  );
  assert.equal(
    withViewingAsOf("/day-reports?project_id=p1", "GET", day),
    "/day-reports?project_id=p1",
  );
  assert.equal(withViewingAsOf("/config/branding", "GET", day), "/config/branding");
  assert.equal(withViewingAsOf("/pods", "GET", null), "/pods", "today sends no as_of");
});

test("changes are never dated, even where the backend would take as_of", () => {
  const day = "2026-09-28";
  assert.equal(withViewingAsOf("/me/status/confirm", "POST", day), "/me/status/confirm");
  assert.equal(withViewingAsOf("/me/status/correct", "post", day), "/me/status/correct");
  assert.equal(withViewingAsOf("/pods", "DELETE", day), "/pods");
});

test("path templates match one segment per parameter", () => {
  assert.equal(acceptsAsOf("/persona/program/program-platform/trend"), true);
  assert.equal(acceptsAsOf("/workstreams/ws-1"), true);
  assert.equal(acceptsAsOf("/workstreams/ws-1/extra"), false);
  assert.equal(acceptsAsOf("/me/status/confirm"), false);
  assert.equal(acceptsAsOf("/portfolio/feed"), false);
});

test("today is learned from a dated read sent for today, and from nothing else", () => {
  let heard = 0;
  const stop = subscribeServerToday(() => (heard += 1));
  learnServerToday("/pods/pod-a/checkins?as_of=2026-09-28", "GET", { as_of: "2026-09-28" });
  learnServerToday("/pods/pod-a/checkins", "POST", { as_of: "2026-09-29" });
  learnServerToday("/config/members", "GET", { as_of: "2026-09-30" });
  learnServerToday("/me/focus", "GET", { as_of: "7 Oct" });
  assert.equal(currentServerToday(), null);
  learnServerToday("/me/focus", "GET", { as_of: "2026-10-07", focus: [] });
  assert.equal(currentServerToday(), "2026-10-07");
  learnServerToday("/portfolio/heatmap?program_root_id=p", "GET", { as_of: "2026-10-07" });
  assert.equal(heard, 1, "the same day again tells nobody");
  stop();
});
