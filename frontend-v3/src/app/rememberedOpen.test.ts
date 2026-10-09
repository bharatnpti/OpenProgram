import assert from "node:assert/strict";
import { test } from "node:test";

import {
  readRemembered,
  rememberedKey,
  writeRemembered,
  type OpenStore,
} from "./rememberedOpen.ts";

const memory = (): OpenStore & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
  };
};

test("a folding panel's state is kept per person", () => {
  assert.equal(rememberedKey("sm-checkins", "U1006"), "openprogram.v3.open.sm-checkins.U1006");
  assert.equal(rememberedKey("sm-checkins", null), "openprogram.v3.open.sm-checkins.anyone");
  const store = memory();
  const ira = rememberedKey("sm-checkins", "U1006");
  const asha = rememberedKey("sm-checkins", "U1001");
  assert.equal(
    readRemembered(() => store, ira, false),
    false,
    "folded until opened",
  );
  writeRemembered(() => store, ira, true);
  assert.equal(
    readRemembered(() => store, ira, false),
    true,
  );
  assert.equal(
    readRemembered(() => store, asha, false),
    false,
    "another person's choice is theirs",
  );
  writeRemembered(() => store, ira, false);
  assert.equal(
    readRemembered(() => store, ira, true),
    false,
    "a closed panel stays closed",
  );
});

test("blocked storage opens the panel as it does by default, and never throws", () => {
  const blocked = (): OpenStore => {
    throw new Error("SecurityError");
  };
  const throwing: OpenStore = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  assert.equal(readRemembered(blocked, "k", false), false);
  assert.equal(
    readRemembered(() => throwing, "k", true),
    true,
  );
  assert.doesNotThrow(() => writeRemembered(blocked, "k", true));
  assert.doesNotThrow(() => writeRemembered(() => throwing, "k", true));
});
