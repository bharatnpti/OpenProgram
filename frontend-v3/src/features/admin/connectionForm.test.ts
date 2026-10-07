import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConnectionResponse, ConnectorFieldDto } from "../../api/schema";
import {
  connectionState,
  formFromConnection,
  groupOf,
  missingRequired,
  optionLabel,
  runsOnServerSettings,
  savePayload,
  savedBy,
  testBlock,
  testPayload,
  visibleFields,
  withValidOptions,
} from "./connectionForm.ts";

const when = (field: string, ...values: string[]) => ({ field, values });
const field = (over: Partial<ConnectorFieldDto> & { key: string }): ConnectorFieldDto => ({
  label: over.key,
  kind: "text",
  required: false,
  help: "",
  placeholder: "",
  default: null,
  options: [],
  shown_when: null,
  ...over,
});

// Jira on Data Center with a personal access token stored, as the API returns it:
// the token itself never comes back, only its name in secrets_set.
const JIRA: ConnectionResponse = {
  connector: "jira",
  name: "Jira",
  description: "",
  purposes: ["issue_tracker"],
  exclusive_group: null,
  configured: true,
  enabled: true,
  settings: {
    deployment: "data_center",
    base_url: "https://jira.example.invalid",
    auth_method: "personal_access_token",
  },
  secrets_set: ["personal_access_token"],
  environment_configured: false,
  updated_at: null,
  updated_by: null,
  updated_by_name: null,
  last_test: null,
  fields: [
    field({
      key: "deployment",
      label: "Jira type",
      kind: "select",
      required: true,
      default: "cloud",
      options: [
        { value: "cloud", label: "Jira Cloud (atlassian.net)", shown_when: null },
        { value: "data_center", label: "Jira Data Center or Server", shown_when: null },
      ],
    }),
    field({ key: "base_url", label: "Jira address", kind: "url", required: true }),
    field({
      key: "auth_method",
      label: "Sign-in method",
      kind: "select",
      required: true,
      default: "api_token",
      options: [
        {
          value: "api_token",
          label: "Email and API token",
          shown_when: when("deployment", "cloud"),
        },
        {
          value: "personal_access_token",
          label: "Personal access token",
          shown_when: when("deployment", "data_center"),
        },
      ],
    }),
    field({
      key: "email",
      label: "Account email",
      kind: "email",
      required: true,
      shown_when: when("auth_method", "api_token"),
    }),
    field({
      key: "api_token",
      label: "API token",
      kind: "secret",
      required: true,
      shown_when: when("auth_method", "api_token"),
    }),
    field({
      key: "personal_access_token",
      label: "Personal access token",
      kind: "secret",
      required: true,
      shown_when: when("auth_method", "personal_access_token"),
    }),
  ],
};

test("a stored secret is never in the form, and is kept unless typed over or cleared", () => {
  const form = formFromConnection(JIRA);

  assert.deepEqual(form.secrets, {});
  assert.equal("personal_access_token" in form.values, false);
  assert.deepEqual(savePayload(JIRA, form).secrets, {});
  assert.deepEqual(
    savePayload(JIRA, { ...form, secrets: { personal_access_token: " new " } }).secrets,
    { personal_access_token: "new" },
  );
  assert.deepEqual(
    savePayload(JIRA, { ...form, clearedSecrets: ["personal_access_token"] }).secrets,
    { personal_access_token: null },
  );
});

test("only the fields of the chosen sign-in show", () => {
  const keys = visibleFields(JIRA.fields, formFromConnection(JIRA).values).map((f) => f.key);

  assert.deepEqual(keys, ["deployment", "base_url", "auth_method", "personal_access_token"]);
});

test("switching Jira to Cloud moves the sign-in to one Cloud offers", () => {
  const values = withValidOptions(JIRA.fields, {
    ...formFromConnection(JIRA).values,
    deployment: "cloud",
  });

  assert.equal(values.auth_method, "api_token");
  assert.deepEqual(
    visibleFields(JIRA.fields, values).map((f) => f.key),
    ["deployment", "base_url", "auth_method", "email", "api_token"],
  );
});

test("a required secret counts as present when stored, and missing once cleared", () => {
  const form = formFromConnection(JIRA);

  assert.deepEqual(missingRequired(JIRA, form), []);
  assert.deepEqual(
    missingRequired(JIRA, { ...form, clearedSecrets: ["personal_access_token"] }).map((f) => f.key),
    ["personal_access_token"],
  );
});

test("a test sends a typed secret, and a secret marked to clear as null, so none falls back", () => {
  const form = { ...formFromConnection(JIRA), clearedSecrets: ["personal_access_token"] };

  // The server leaves a null secret out of the test and never takes it from the store.
  assert.deepEqual(testPayload(JIRA, form)?.secrets, { personal_access_token: null });
  assert.deepEqual(
    testPayload(JIRA, { ...form, secrets: { personal_access_token: "fake-token" } })?.secrets,
    { personal_access_token: "fake-token" },
  );
  // A secret left alone is not in the body: the stored one is used.
  assert.deepEqual(testPayload(JIRA, { ...formFromConnection(JIRA), enabled: false }), undefined);
  const moved = {
    ...formFromConnection(JIRA),
    values: { ...formFromConnection(JIRA).values, base_url: "https://jira.example.invalid/x" },
  };
  assert.deepEqual(testPayload(JIRA, moved)?.secrets, {});
});

test("an unchanged form tests what is stored, with no body, so the result is recorded", () => {
  assert.equal(testPayload(JIRA, formFromConnection(JIRA)), undefined);
  assert.equal(testBlock(JIRA, formFromConnection(JIRA)), null);
});

test("a stored secret is never tested against a changed address", () => {
  const form = formFromConnection(JIRA);
  const moved = { ...form, values: { ...form.values, base_url: "https://jira.example.co" } };
  assert.equal(
    testBlock(JIRA, moved),
    "Jira address changed. A stored secret is used only with the address and sign-in it was saved with, so enter Personal access token again to test the new values.",
  );
  const retyped = { ...moved, secrets: { personal_access_token: "fake-token" } };
  assert.equal(testBlock(JIRA, retyped), null);
  assert.deepEqual(testPayload(JIRA, retyped)?.settings?.base_url, "https://jira.example.co");
});

test("a secret marked to clear no longer stops a test: it is sent as null", () => {
  const cleared = { ...formFromConnection(JIRA), clearedSecrets: ["personal_access_token"] };
  assert.equal(testBlock(JIRA, cleared), null);
  assert.equal(
    testBlock(JIRA, { ...cleared, secrets: { personal_access_token: "fake-token" } }),
    null,
  );
});

test("a moved address still stops a test while any stored secret would go there", () => {
  const form = formFromConnection(JIRA);
  const moved = { ...form, values: { ...form.values, base_url: "https://jira.example.co" } };
  // Cleared, the one secret is no longer reused, so nothing reaches the new address.
  assert.equal(testBlock(JIRA, { ...moved, clearedSecrets: ["personal_access_token"] }), null);
  // With a second stored secret left alone, it would be reused and the block stays.
  const two = { ...JIRA, secrets_set: ["personal_access_token", "api_token"] };
  assert.match(
    testBlock(two, { ...moved, clearedSecrets: ["personal_access_token"] }) ?? "",
    /Jira address changed\..*enter API token again/,
  );
});

test("a card names who saved it, or shows the id when it is no member's", () => {
  assert.equal(savedBy({ ...JIRA, updated_by: "U1001", updated_by_name: "Asha Rao" }), "Asha Rao");
  assert.equal(savedBy({ ...JIRA, updated_by: "U0C1" }), "U0C1");
  assert.equal(savedBy(JIRA), "an admin");
});

test("a card says whether the tenant, the server, or nobody set it up", () => {
  assert.equal(connectionState(JIRA), "on");
  assert.equal(connectionState({ ...JIRA, enabled: false }), "off");
  assert.equal(
    connectionState({ ...JIRA, configured: false, enabled: false, environment_configured: true }),
    "environment",
  );
  assert.equal(connectionState({ ...JIRA, configured: false, enabled: false }), "not_set_up");
});

test("connectors are grouped by where reports go and where work is read from", () => {
  assert.equal(groupOf(JIRA), "work");
  assert.equal(groupOf({ ...JIRA, purposes: ["code"] }), "work");
  assert.equal(groupOf({ ...JIRA, purposes: ["chat", "report_delivery"] }), "reports");
  assert.equal(groupOf({ ...JIRA, purposes: ["report_delivery"] }), "reports");
  assert.equal(groupOf({ ...JIRA, purposes: ["calendar"] }), "calendar");
  assert.equal(optionLabel(JIRA, "deployment"), "Jira Data Center or Server");
  assert.equal(optionLabel({ ...JIRA, settings: {} }, "deployment"), null);
});

// GitLab as a tenant that has none of its own finds it: the server's settings run it.
const GITLAB: ConnectionResponse = {
  ...JIRA,
  connector: "gitlab",
  name: "GitLab",
  configured: false,
  enabled: false,
  settings: {},
  secrets_set: [],
  environment_configured: true,
  fields: [
    field({
      key: "base_url",
      label: "GitLab address",
      kind: "url",
      required: true,
      default: "https://gitlab.com",
    }),
    field({ key: "token", label: "Access token", kind: "secret", required: true }),
    field({ key: "namespace_id", label: "Group" }),
  ],
};

test("a connection the server already runs does not start with a typical address filled in", () => {
  assert.equal(runsOnServerSettings(GITLAB), true);
  const form = formFromConnection(GITLAB);
  assert.equal(form.values.base_url, "", "typing a token over gitlab.com would repoint the tenant");
  // Not set up anywhere: the typical address is a fair start.
  const unset = formFromConnection({ ...GITLAB, environment_configured: false });
  assert.equal(unset.values.base_url, "https://gitlab.com");
  // Its own saved address always wins.
  const saved = formFromConnection({
    ...GITLAB,
    configured: true,
    settings: { base_url: "https://git.example.invalid" },
  });
  assert.equal(saved.values.base_url, "https://git.example.invalid");
  assert.equal(runsOnServerSettings({ ...GITLAB, configured: true }), false);
});

test("the address has to be typed before such a connection can be turned on", () => {
  const form = formFromConnection(GITLAB);
  assert.deepEqual(
    missingRequired(GITLAB, form).map((f) => f.key),
    ["base_url", "token"],
  );
});
