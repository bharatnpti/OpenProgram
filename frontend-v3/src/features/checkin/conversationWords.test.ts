import assert from "node:assert/strict";
import { test } from "node:test";

import { emptyConversationWords, orderedTurns, turnAuthor } from "./conversationWords.ts";

test("a turn is Ora's or the person's own", () => {
  assert.equal(turnAuthor({ role: "agent" }), "Ora");
  assert.equal(turnAuthor({ role: "user" }), "You");
});

test("turns read oldest first, and equal times keep the server's order", () => {
  const turns = [
    { role: "user" as const, text: "b", at: "2026-10-13T09:35:00Z" },
    { role: "agent" as const, text: "a", at: "2026-10-13T09:30:00Z" },
    { role: "agent" as const, text: "c", at: "2026-10-13T09:35:00Z" },
  ];
  assert.deepEqual(
    orderedTurns(turns).map((turn) => turn.text),
    ["a", "b", "c"],
  );
});

test("an empty column says why: not kept past the window, or nothing said yet", () => {
  const base = { day: "2026-10-13", retention_days: 30 };
  assert.equal(
    emptyConversationWords({ ...base, kept: false, note: "Not kept after 30 days." }, false),
    "Not kept after 30 days.",
  );
  assert.equal(
    emptyConversationWords({ ...base, kept: false, note: null }, false),
    "Not kept after 30 days.",
  );
  assert.match(
    emptyConversationWords({ ...base, kept: true, note: null }, true),
    /^Nothing yet today/,
  );
  assert.equal(
    emptyConversationWords({ ...base, kept: true, note: null }, false),
    "Nothing was said on Tue 13 Oct.",
  );
});
