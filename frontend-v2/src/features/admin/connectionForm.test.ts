import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConnectionResponse } from "../../api/schema";
import {
  connectionState,
  formFromConnection,
  missingRequired,
  savePayload,
  savedBy,
  testPayload,
  visibleFields,
  withValidOptions,
} from "./connectionForm.ts";

const when = (field: string, ...values: string[]) => ({ field, values });

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
    base_url: "https://jira.example.com",
    auth_method: "personal_access_token",
  },
  secrets_set: ["personal_access_token"],
  environment_configured: false,
  updated_at: null,
  updated_by: null,
  updated_by_name: null,
  last_test: null,
  fields: [
    {
      key: "deployment",
      label: "Jira type",
      kind: "select",
      required: true,
      help: "",
      placeholder: "",
      default: "cloud",
      options: [
        { value: "cloud", label: "Cloud", shown_when: null },
        { value: "data_center", label: "Data Center", shown_when: null },
      ],
      shown_when: null,
    },
    {
      key: "base_url",
      label: "Jira address",
      kind: "url",
      required: true,
      help: "",
      placeholder: "",
      default: null,
      options: [],
      shown_when: null,
    },
    {
      key: "auth_method",
      label: "Sign-in method",
      kind: "select",
      required: true,
      help: "",
      placeholder: "",
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
      shown_when: null,
    },
    {
      key: "email",
      label: "Account email",
      kind: "email",
      required: true,
      help: "",
      placeholder: "",
      default: null,
      options: [],
      shown_when: when("auth_method", "api_token"),
    },
    {
      key: "api_token",
      label: "API token",
      kind: "secret",
      required: true,
      help: "",
      placeholder: "",
      default: null,
      options: [],
      shown_when: when("auth_method", "api_token"),
    },
    {
      key: "personal_access_token",
      label: "Personal access token",
      kind: "secret",
      required: true,
      help: "",
      placeholder: "",
      default: null,
      options: [],
      shown_when: when("auth_method", "personal_access_token"),
    },
  ],
};

test("a stored secret is kept unless it is typed over or cleared", () => {
  const form = formFromConnection(JIRA);

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

test("switching the Jira type moves the sign-in to one it offers", () => {
  const values = withValidOptions(JIRA.fields, {
    ...formFromConnection(JIRA).values,
    deployment: "cloud",
  });

  assert.equal(values.auth_method, "api_token");
});

test("a required secret counts as present when stored, and missing once cleared", () => {
  const form = formFromConnection(JIRA);

  assert.deepEqual(missingRequired(JIRA, form), []);
  assert.deepEqual(
    missingRequired(JIRA, { ...form, clearedSecrets: ["personal_access_token"] }).map((f) => f.key),
    ["personal_access_token"],
  );
});

test("a test sends typed secrets only, never a clear", () => {
  const form = { ...formFromConnection(JIRA), clearedSecrets: ["personal_access_token"] };

  assert.deepEqual(testPayload(JIRA, form).secrets, {});
});

test("a card names who saved it, or shows the id when it is no member's", () => {
  assert.equal(savedBy({ ...JIRA, updated_by: "U0C1", updated_by_name: "Asha Rao" }), "Asha Rao");
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
