import assert from "node:assert/strict";
import { test } from "node:test";

import { DEV_SIGN_IN, accountLine, groupPeople, personOption, rolesLabel } from "./roleWords.ts";

const person = (name: string, roles: string[], title: string | null = null) => ({
  name,
  roles,
  title,
});

test("every role a person holds is named, leaders first and admin last", () => {
  assert.equal(rolesLabel(["admin", "mgr"]), "Manager + Admin");
  assert.equal(rolesLabel(["dev"]), "Developer");
  assert.equal(rolesLabel(["viewer"]), "no role");
  assert.equal(rolesLabel(null), "no role");
});

test("people are grouped once, under their most senior role, keeping the backend's order", () => {
  const groups = groupPeople([
    person("Elena Fischer", ["exec"]),
    person("Asha Rao", ["mgr", "admin"]),
    person("Mina Patel", ["po"]),
    person("Ira Novak", ["sm"]),
    person("Kai Thompson", ["dev"]),
    person("Liam Chen", ["dev"]),
    person("Guest", []),
  ]);
  assert.deepEqual(
    groups.map((g) => [g.label, g.people.map((p) => p.name)]),
    [
      ["Executive", ["Elena Fischer"]],
      ["Manager", ["Asha Rao"]],
      ["Product Owner", ["Mina Patel"]],
      ["Scrum Master", ["Ira Novak"]],
      ["Developer", ["Kai Thompson", "Liam Chen"]],
      ["No role", ["Guest"]],
    ],
  );
});

test("an option names the person, their title, and any role beyond their group's", () => {
  assert.equal(
    personOption(person("Asha Rao", ["mgr", "admin"], "Engineering Manager")),
    "Asha Rao · Engineering Manager (also Admin)",
  );
  assert.equal(
    personOption(person("Kai Thompson", ["dev"], "Backend Engineer")),
    "Kai Thompson · Backend Engineer",
  );
  assert.equal(personOption(person("Pat Doe", ["admin"], " ")), "Pat Doe");
});

test("the account menu calls a local sign-in what it is, never a demo", () => {
  assert.equal(DEV_SIGN_IN, "local sign-in (development)");
  assert.equal(
    accountLine(["mgr", "admin"], true),
    "Manager + Admin · local sign-in (development)",
  );
  assert.equal(accountLine(["dev"], false), "Developer");
  assert.doesNotMatch(accountLine(["dev"], true), /demo/i);
});
