import assert from "node:assert/strict";
import { test } from "node:test";

import {
  autoMatchSummary,
  buildIdentityUpdate,
  formFromLink,
  gapWords,
  identityGaps,
  importSummary,
  stamp,
  syncSummary,
  validateIdentity,
} from "./identityForm.ts";

const link = {
  chat_user_id: "U1007",
  jira_account_id: "kai",
  jira_email: "kai.thompson@example.com",
  vcs_username: null,
};

test("a person with no link yet starts from an empty form", () => {
  assert.deepEqual(formFromLink(null), {
    chatUserId: "",
    jiraAccountId: "",
    jiraEmail: "",
    vcsUsername: "",
  });
  assert.equal(formFromLink(link).vcsUsername, "");
});

test("an untouched form sends nothing", () => {
  assert.deepEqual(buildIdentityUpdate(link, formFromLink(link)), {});
});

test("only the ids that changed are sent, and a blank clears one", () => {
  const form = { ...formFromLink(link), vcsUsername: " kai-t ", jiraAccountId: "" };
  assert.deepEqual(buildIdentityUpdate(link, form), {
    vcs_username: "kai-t",
    jira_account_id: null,
  });
});

test("a first link sends only what was filled in", () => {
  const form = { ...formFromLink(null), chatUserId: "U1020" };
  assert.deepEqual(buildIdentityUpdate(null, form), { chat_user_id: "U1020" });
});

test("an email must look like one, and an id has no spaces", () => {
  const ok = formFromLink(link);
  assert.deepEqual(validateIdentity(ok), {});
  assert.equal(
    validateIdentity({ ...ok, jiraEmail: "kai" }).jiraEmail,
    "Use an address such as name@example.com.",
  );
  assert.equal(
    validateIdentity({ ...ok, chatUserId: "U 1" }).chatUserId,
    "An id has no spaces in it.",
  );
  assert.deepEqual(validateIdentity({ ...ok, jiraEmail: "" }), {});
});

test("what is missing reads as the accounts the console needs", () => {
  assert.deepEqual(identityGaps(link), ["vcs_username"]);
  assert.deepEqual(identityGaps(null), ["chat_user_id", "jira_account_id", "vcs_username"]);
  assert.equal(gapWords(["chat_user_id", "jira_account_id"]), "chat id and Jira account");
  assert.equal(gapWords(["vcs_username"]), "Git username");
});

test("a sync reports who was found and who left", () => {
  assert.equal(
    syncSummary({ synced_count: 14, deactivated_count: 0 }),
    "14 people found in the chat directory. Nobody has left it since the last sync.",
  );
  assert.equal(
    syncSummary({ synced_count: 1, deactivated_count: 2 }),
    "1 person found in the chat directory; 2 left the directory and can't be imported.",
  );
});

test("a moment is written in one time zone, so the day and the time agree", () => {
  assert.equal(stamp("2026-10-06T18:36:00Z", "UTC"), "Tue 6 Oct 18:36");
  // Past midnight in India, the next day: the day moves with the time.
  assert.equal(stamp("2026-10-06T18:36:00Z", "Asia/Kolkata"), "Wed 7 Oct 00:06");
  assert.equal(stamp("2026-10-06T00:05:00Z", "UTC"), "Tue 6 Oct 00:05");
  assert.equal(stamp(null), "never");
});

test("an import says how many were new", () => {
  assert.equal(importSummary(3, 0), "Imported 3 people as members.");
  assert.equal(importSummary(1, 2), "Imported 1 person as members. 2 already were.");
});

test("auto-match names who it filled in and with what", () => {
  assert.equal(
    autoMatchSummary({ updated_count: 0, members: [] }),
    "Nothing to fill in: every member already has what the directory knows.",
  );
  assert.equal(
    autoMatchSummary({
      updated_count: 2,
      members: [
        { id: "U1010", name: "Raj Iyer", filled: ["chat_user_id", "jira_email"] },
        { id: "U1011", name: "Elena Fischer", filled: ["chat_user_id"] },
      ],
    }),
    "Filled in 2 members: Raj Iyer (chat id and Jira email), Elena Fischer (chat id).",
  );
});
