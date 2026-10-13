import assert from "node:assert/strict";
import { test } from "node:test";

import { screenRows } from "../components/shell/paletteRows.ts";
import { showsCurrentState, viewingDateNote, withViewingDateParam } from "../lib/viewingDate.ts";
import { accessOf, legacyPath, pageOf, redirectFor, capabilitiesFor } from "./access.ts";
import { NAV, NAV_GROUPS, shownNav } from "./nav.ts";
import type { AppRole } from "./roleWords.ts";

const ROLES: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];
const access = (role: AppRole) => accessOf({ lens: [role], chatEnabled: false });

test("the sidebar is four groups, thirteen pages, each with its own address and page", () => {
  assert.deepEqual(
    NAV_GROUPS.map((group) => group.label),
    ["Daily", "Delivery", "Insight", "Setup"],
  );
  assert.equal(NAV.length, 13);
  assert.equal(new Set(NAV.map((item) => item.to)).size, NAV.length);
  for (const item of NAV) assert.equal(pageOf(item.to), item.page, item.to);
  // No sidebar address is an old one: nothing the nav draws is ever redirected for its role.
  const none = { podProjects: () => [], workstreamProjects: () => [] };
  for (const role of ROLES) {
    for (const item of shownNav(access(role))) {
      const at = { pathname: item.to, search: "", hash: "" };
      assert.equal(legacyPath(at, access(role), none), null, `${role} ${item.to}`);
      assert.equal(
        redirectFor(at, access(role), capabilitiesFor([role]), none),
        null,
        `${role} ${item.to}`,
      );
    }
  }
});

test("⌘K offers the role's own pages, in the sidebar's order, and nothing else", () => {
  for (const role of ROLES) {
    assert.deepEqual(
      screenRows(shownNav(access(role))).map((row) => row.to),
      shownNav(access(role)).map((item) => item.to),
      role,
    );
  }
  assert.deepEqual(
    screenRows(shownNav(access("dev"))).map((row) => row.label),
    ["Today", "Check-in", "Projects", "Pods", "Reports"],
  );
});

test("the past-day picker reaches every page: each keeps the viewing day, Admin shows now", () => {
  for (const item of NAV) {
    assert.equal(showsCurrentState(item.to), item.page === "admin", item.to);
    // The banner always says what a past day shows on it.
    assert.ok(viewingDateNote(item.to).length > 0, item.to);
    // The header's picker writes the day into whatever the page's own query is.
    assert.equal(
      withViewingDateParam(item.page === "projects" ? "?row=pod:x" : "", "2026-10-02"),
      item.page === "projects" ? "?row=pod%3Ax&asOf=2026-10-02" : "?asOf=2026-10-02",
      item.to,
    );
  }
  // The project and pod pages follow the day like the rest.
  assert.equal(showsCurrentState("/projects/project-checkout"), false);
  assert.equal(showsCurrentState("/pods/pod-payments"), false);
});
