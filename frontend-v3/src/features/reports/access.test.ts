import assert from "node:assert/strict";
import { test } from "node:test";

import { accessFor, actionError, maySignOff, signOffWho } from "./access.ts";

test("each lens gets the backend's capabilities, an admin all of them", () => {
  assert.deepEqual(accessFor(["po"]), {
    readProjectProgress: true,
    setProjectDates: true,
    setPodDates: false,
    editGates: true,
    readPodDelivery: true,
  });
  assert.deepEqual(accessFor(["sm"]), {
    readProjectProgress: false,
    setProjectDates: false,
    setPodDates: true,
    editGates: true,
    readPodDelivery: true,
  });
  assert.equal(accessFor(["exec"]).editGates, false);
  assert.equal(accessFor(["exec"]).setProjectDates, false);
  assert.equal(accessFor(["dev"]).readPodDelivery, false);
  assert.ok(Object.values(accessFor(["admin"])).every(Boolean));
});

test("only the roles a kind names sign it off, and an admin", () => {
  const acceptance = { sign_off_roles: ["po", "mgr"] as ("po" | "mgr")[] };
  assert.equal(maySignOff(acceptance, ["po"]), true);
  assert.equal(maySignOff(acceptance, ["dev"]), false);
  assert.equal(maySignOff(acceptance, ["admin"]), true);
  assert.equal(signOffWho(acceptance), "a product owner or manager");
  assert.equal(signOffWho({ sign_off_roles: ["admin"] }), "an admin");
  assert.equal(signOffWho({ sign_off_roles: ["dev", "sm"] }), "a developer or scrum master");
});

test("a capability refusal leads with who may do it and keeps the server's reason", () => {
  assert.equal(
    actionError({ status: 403, message: "U1006 is not authorized for set_project_dates" }),
    "Only a product owner, manager or admin can do this. The server said: U1006 is not authorized for set_project_dates",
  );
  assert.equal(
    actionError({
      status: 403,
      message: "Only the pod's own scrum master or a manager sets the pod's date.",
    }),
    "Not allowed. The server said: Only the pod's own scrum master or a manager sets the pod's date.",
  );
});

test("validation errors name the field in words, a domain 422 keeps its sentence", () => {
  assert.equal(
    actionError({
      status: 422,
      message: "Request failed with status 422.",
      detail: { detail: [{ loc: ["body", "target_date"], msg: "Input should be a valid date" }] },
    }),
    "Delivery date: Input should be a valid date.",
  );
  assert.equal(
    actionError({
      status: 422,
      message: "A delivery date more than a year in the past is not a plan.",
      detail: { detail: "A delivery date more than a year in the past is not a plan." },
    }),
    "A delivery date more than a year in the past is not a plan.",
  );
  assert.equal(actionError(new Error("Network down")), "Network down");
  assert.equal(actionError(undefined), "Something went wrong. Try again.");
});
