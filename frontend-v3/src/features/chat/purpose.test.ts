import assert from "node:assert/strict";
import { test } from "node:test";

import { purposeWords } from "./purpose.ts";

const message = (purpose: string | null, metadata: Record<string, string> = {}) => ({
  purpose,
  metadata,
});

test("the backend's real purposes read as plain words", () => {
  assert.equal(purposeWords(message("status_checkin")), "check-in");
  assert.equal(purposeWords(message("status_clarification")), "follow-up question");
  assert.equal(purposeWords(message("status_nudge")), "reminder");
  assert.equal(purposeWords(message("status_escalation")), "missed check-in notice");
  assert.equal(purposeWords(message("cross_person_request")), "request");
  assert.equal(purposeWords(message("cross_person_request_resolved")), "request resolved");
});

test("a day report has no purpose but says what it is; a reply says nothing", () => {
  assert.equal(purposeWords(message(null, { kind: "day_report" })), "day report");
  assert.equal(purposeWords(message(null)), null);
});

test("an unknown purpose is shown in its own words rather than hidden", () => {
  assert.equal(purposeWords(message("weekly_digest")), "weekly digest");
});
