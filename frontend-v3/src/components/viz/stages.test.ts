import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { STAGE_ORDER, stageColor } from "./stages.ts";

const css = readFileSync(new URL("../../index.css", import.meta.url), "utf8");

/** Every `--op-stage-*` value set in the blocks the selector opens, last one winning. */
function stageTokens(selector: RegExp): Map<string, string> {
  const tokens = new Map<string, string>();
  for (const block of css.matchAll(selector)) {
    for (const [, name, value] of block[1].matchAll(/--op-stage-([a-z-]+):\s*(#[0-9a-f]{6});/g)) {
      tokens.set(name, value);
    }
  }
  return tokens;
}

/** WCAG relative luminance and contrast ratio. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((at) => {
    const channel = parseInt(hex.slice(at, at + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const tokenOf = (stage: string) => stage.replace(/_/g, "-");
const light = stageTokens(/(?:^|\n):root\s*\{([^}]*)\}/g);
const dark = stageTokens(/(?:^|\n):root\[data-theme="dark"\]\s*\{([^}]*)\}/g);
// The dark surfaces a stage colour is drawn on: Overall's page (.op-viz), the
// table header and Daily's sent text, the cards, and Daily's tiles and the grey fill.
const DARK_SURFACES = ["#0d0d0d", "#141413", "#1a1a19", "#262625"];

test("each of the six stages has one colour in light and a step of its own in dark", () => {
  for (const stage of STAGE_ORDER) {
    assert.ok(light.has(tokenOf(stage)), `no light colour for ${stage}`);
    assert.ok(dark.has(tokenOf(stage)), `no dark step for ${stage}`);
    assert.equal(stageColor(stage), `var(--op-stage-${tokenOf(stage)})`);
  }
  assert.equal(light.size, STAGE_ORDER.length);
  assert.equal(dark.size, STAGE_ORDER.length);
  // Six different colours in each theme: a stage is told apart by its hue too.
  assert.equal(new Set(light.values()).size, STAGE_ORDER.length);
  assert.equal(new Set(dark.values()).size, STAGE_ORDER.length);
});

test("every dark stage step meets 3:1 on every dark surface it is drawn on", () => {
  for (const stage of STAGE_ORDER) {
    const colour = dark.get(tokenOf(stage)) ?? "";
    for (const surface of DARK_SURFACES) {
      const ratio = contrast(colour, surface);
      assert.ok(ratio >= 3, `${stage} ${colour} is ${ratio.toFixed(2)}:1 on ${surface}`);
    }
  }
});
