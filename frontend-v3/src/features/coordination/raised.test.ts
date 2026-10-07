import assert from "node:assert/strict";
import { test } from "node:test";

import { raisedStillOpen } from "./raised.ts";

test("an ask nobody was matched to still waits on the person who raised it", () => {
  assert.equal(raisedStillOpen("open"), true);
  assert.equal(raisedStillOpen("acknowledged"), true);
  assert.equal(raisedStillOpen("needs_resolution"), true);
  assert.equal(raisedStillOpen("resolved"), false);
  assert.equal(raisedStillOpen("dismissed"), false);
});
