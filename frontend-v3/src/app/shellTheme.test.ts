import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// The console's dark theme is one hook (data-theme="dark" on <html>) and one remap
// (index.css). The shell follows it where it carries .op-shell, or reads the surface
// tokens: a menu or picker opens in a portal outside the frame, so it carries the class
// itself, and a plain bg-white there would stay white around dark ink.
const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const css = read("../index.css");

/** The shell parts, as files: the frame, its header controls, and the pickers they open. */
const SHELL = [
  "./Layout.tsx",
  "../components/shell/AccountMenu.tsx",
  "../components/shell/ActingAs.tsx",
  "../components/shell/CommandPalette.tsx",
  "../components/shell/ViewingDate.tsx",
  "../features/project/ProjectSwitcher.tsx",
];

test("the dark remap reaches the shell as it reaches the report pages", () => {
  const remap = css.match(/:root\[data-theme="dark"\] :is\(([^)]*)\) \{/);
  assert.ok(remap, "no dark remap block");
  const reached = remap[1].split(",").map((name) => name.trim());
  for (const name of [".op-shell", ".op-viz", ".op-viz-card"]) {
    assert.ok(reached.includes(name), `${name} is not in the dark remap`);
  }
  // The page behind the shell and the body turn with it.
  assert.match(css, /--op-page: #ffffff;/);
  assert.match(css, /:root\[data-theme="dark"\] \{[^}]*--op-page: #0d0d0d;/);
  assert.match(css, /:root\[data-theme="dark"\] body \{[^}]*background: var\(--op-page\)/);
});

test("the shell's frame and each portal it opens carry .op-shell", () => {
  assert.match(read("./Layout.tsx"), /"op-shell min-h-screen/);
  // The menu opened from the header's button, the jump palette and the project switcher
  // are Radix portals: outside the frame, so each content element is its own shell.
  const portals: [string, RegExp][] = [
    ["./Layout.tsx", /id="op-menu"[^>]*className="op-shell /],
    ["../components/shell/CommandPalette.tsx", /className="op-shell fixed/],
    ["../features/project/ProjectSwitcher.tsx", /className="op-shell fixed/],
  ];
  for (const [file, pattern] of portals) {
    assert.match(read(file).replace(/\s+/g, " "), pattern, `${file} lost its .op-shell`);
  }
});

test("no shell part is drawn on a fixed white", () => {
  for (const file of SHELL) {
    assert.doesNotMatch(read(file), /\bbg-white\b/, `${file} uses bg-white, not the surface token`);
  }
});
