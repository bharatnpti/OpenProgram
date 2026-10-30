import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { askNowWords } from "./askNowWords.ts";

describe("Ask now", () => {
  test("names the person in full on the button, by first name after", () => {
    const words = askNowWords("Kai Thompson");
    assert.equal(words.button, "Ask Kai Thompson for a check-in now");
    assert.equal(words.title, "Ask Kai for a check-in now?");
    assert.match(words.effect, /^Ora sends Kai today's check-in/);
    assert.equal(words.done, "Check-in requested. Ora asks Kai in a moment.");
  });

  test("a one-word name is used as it is", () => {
    assert.equal(askNowWords("Asha").title, "Ask Asha for a check-in now?");
  });
});
