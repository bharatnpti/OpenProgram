import assert from "node:assert/strict";
import { test } from "node:test";

import { actionError } from "./errors.ts";

test("a refusal says what the role can't do, and the server's reason", () => {
  assert.equal(
    actionError(
      { status: 403, message: "U1001 is not authorized for read_own_work" },
      "confirm this check-in",
    ),
    "You can't confirm this check-in with your role. The server said: U1001 is not authorized for read_own_work",
  );
  assert.equal(
    actionError({ status: 403, message: "" }, "resolve this request"),
    "You can't resolve this request with your role.",
  );
});

test("a validation error names the field instead of a status code", () => {
  const error = {
    status: 422,
    message: "Request failed with status 422.",
    detail: {
      detail: [
        {
          type: "string_too_long",
          loc: ["body", "blocker_items", 0, "description"],
          msg: "String should have at most 500 characters",
        },
      ],
    },
  };
  assert.equal(
    actionError(error, "save the correction"),
    "A blocker is not accepted: string should have at most 500 characters.",
  );
  assert.equal(
    actionError(
      {
        status: 422,
        detail: {
          detail: [{ loc: ["body", "summary"], msg: "Value error, summary must not be blank" }],
        },
      },
      "save the correction",
    ),
    "The summary is not accepted: summary must not be blank.",
  );
  assert.equal(
    actionError({ status: 422, message: "Request failed with status 422." }, "save"),
    "Request failed with status 422.",
  );
});

test("a missing record or a conflict keeps the server's own sentence", () => {
  assert.equal(
    actionError(
      { status: 404, message: "cross-person request r1 not found" },
      "resolve this request",
    ),
    "cross-person request r1 not found",
  );
  assert.equal(
    actionError({ status: 409, message: "Viewing a past day." }, "confirm"),
    "Viewing a past day.",
  );
});

test("a server failure or no answer is said plainly", () => {
  assert.equal(
    actionError({ status: 503, message: "x" }, "confirm this check-in"),
    "The server could not confirm this check-in just now. Try again in a moment.",
  );
  assert.equal(
    actionError(new TypeError("Failed to fetch"), "confirm this check-in"),
    "Could not reach the server. Check your connection and try again.",
  );
  assert.equal(actionError({ status: 401 }, "x"), "You are signed out. Sign in again and retry.");
  assert.equal(actionError(undefined, "save it"), "Could not save it.");
});
