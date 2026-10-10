import assert from "node:assert/strict";
import { test } from "node:test";

import { RAG_WORDS, ragWords, toneForRag } from "./status.ts";

test("a colour is named in words, and a colour nobody reported is Status unknown", () => {
  assert.equal(ragWords("green"), "On track");
  assert.equal(ragWords("amber"), "At risk");
  assert.equal(ragWords("red"), "Off track");
  assert.equal(ragWords("unknown"), "Status unknown");
  assert.equal(ragWords(null), "Status unknown");
  assert.equal(ragWords(undefined), "Status unknown");
  // A value a newer server adds is never printed as it came.
  assert.equal(ragWords("purple" as never), "Status unknown");
});

test("no colour word is the enum, and each keeps its tone", () => {
  for (const [rag, words] of Object.entries(RAG_WORDS)) {
    assert.notEqual(words, rag);
    assert.ok(words[0] === words[0].toUpperCase(), `${words} starts a sentence`);
  }
  assert.equal(toneForRag("amber"), "warning");
  assert.equal(toneForRag("unknown"), "neutral");
});
