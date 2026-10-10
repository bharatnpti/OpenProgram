import assert from "node:assert/strict";
import { test } from "node:test";

import { describeAuthFailure } from "./authWords.ts";

test("no answer at all says what to check, not 'sign in'", () => {
  const text = describeAuthFailure(new TypeError("Failed to fetch"));
  assert.match(text, /did not answer/);
  assert.match(text, /CORS/);
  assert.doesNotMatch(text, /sign in/i);
});

test("an error answer gives its status and the backend's message", () => {
  const error = Object.assign(new Error("database is not reachable"), { status: 503 });
  assert.equal(
    describeAuthFailure(error),
    "It answered with an error (503): database is not reachable",
  );
  assert.equal(
    describeAuthFailure(Object.assign(new Error(""), { status: 500 })),
    "It answered with an error (500).",
  );
});
