import assert from "node:assert/strict";
import { test } from "node:test";

import { refusal } from "./adminErrors.ts";

const refused = (status: number, message: string, detail?: unknown) => ({
  status,
  message,
  detail,
});

test("a duplicate id says which id is taken and what to do", () => {
  assert.equal(
    refusal(refused(409, "node pod-data already exists")),
    "The id pod-data is already taken. Choose a different name.",
  );
});

test("a link that is already there and one that is gone read as plain facts", () => {
  assert.equal(refusal(refused(409, "contains link already exists")), "They are already linked.");
  assert.equal(
    refusal(refused(404, "contains link was not found")),
    "They are not linked any more. Refresh the list.",
  );
  assert.equal(
    refusal(refused(404, "program link for project project-x was not found")),
    "They are not linked any more. Refresh the list.",
  );
});

test("a missing record names what is gone", () => {
  assert.equal(
    refusal(refused(404, "workstream ws-x not found for tenant demo")),
    "There is no workstream ws-x. It may have been removed; refresh the list and try again.",
  );
  assert.equal(
    refusal(refused(404, "something unexpected")),
    "It no longer exists. Someone may have removed it; refresh the list and try again.",
  );
});

test("the wrong kind of record is told apart from a missing one", () => {
  assert.equal(
    refusal(refused(404, "U1001 exists as a developer, not a pod")),
    "U1001 is a developer, not a pod.",
  );
});

test("validation failures from the service become sentences", () => {
  assert.equal(
    refusal(refused(400, "self links are not allowed")),
    "Something can't be linked to itself.",
  );
  assert.equal(refusal(refused(400, "name must not be empty")), "The name can't be empty.");
  assert.equal(
    refusal(refused(400, "Kai Thompson has no chat ID linked")),
    "Kai Thompson has no chat id yet, so they can't be a contact. Set one under Directory.",
  );
  assert.equal(
    refusal(refused(400, "an escalation contact needs a member")),
    "Pick a member for each contact you keep.",
  );
});

test("a request the schema rejects shows the field and the problem", () => {
  const detail = {
    detail: [
      {
        loc: ["body", "name"],
        msg: "String should have at least 1 character",
        type: "string_too_short",
      },
    ],
  };
  assert.equal(
    refusal(refused(422, "Request failed with status 422.", detail)),
    "The name can't be empty.",
  );
  const other = { detail: [{ loc: ["body", "role"], msg: "Field required", type: "missing" }] };
  assert.equal(refusal(refused(422, "x", other)), "Role: Field required.");
  const git = {
    detail: [{ loc: ["body", "vcs_username"], msg: "Value error", type: "value_error" }],
  };
  assert.equal(refusal(refused(422, "x", git)), "Git username: Value error.");
  assert.equal(
    refusal(refused(422, "Request failed with status 422.")),
    "Request failed with status 422.",
  );
});

test("a refusal for the role keeps the server's reason", () => {
  assert.equal(
    refusal(refused(403, "U1007 is not authorized for manage_config")),
    "Only an admin can change this. The server said: U1007 is not authorized for manage_config",
  );
});

test("a chat directory that is down or unconfigured is said so", () => {
  assert.equal(
    refusal(refused(503, "slack is unavailable")),
    "A service this needs cannot be reached right now. Slack is unavailable.",
  );
  assert.equal(
    refusal(refused(424, "slack bot token is missing")),
    "A connection this needs is not set up. Slack bot token is missing.",
  );
});

test("a network failure is not shown as a server message", () => {
  assert.equal(
    refusal(new TypeError("Failed to fetch")),
    "Could not reach the server. Check the connection and try again.",
  );
  assert.equal(
    refusal(undefined),
    "Could not reach the server. Check the connection and try again.",
  );
});

test("text no rule knows is shown as the server said it", () => {
  assert.equal(
    refusal(refused(409, "You are viewing 3 Oct. Return to today to change anything.")),
    "You are viewing 3 Oct. Return to today to change anything.",
  );
  assert.equal(refusal(refused(409, "")), "That did not work.");
});

test("a server error says nothing is known to have changed", () => {
  const words =
    "The server hit an unexpected error. Refresh to see what was saved, then try again.";
  assert.equal(refusal(refused(500, "Internal Server Error")), words);
  assert.equal(refusal(refused(502, "")), words);
});
