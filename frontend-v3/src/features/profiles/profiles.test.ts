import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  MemberProfileResponse,
  ProfileResponse,
  ProfileReviewItemResponse,
} from "../../api/schema";
import {
  acceptAllWords,
  chipNote,
  importPlacementWords,
  isChecked,
  offeredProfiles,
  profileDone,
  profileForPodRole,
  profileLabel,
  profileOptions,
  profileRefusal,
  reviewOrder,
  reviewSummary,
  sourceWords,
  suggestionLine,
} from "./profiles.ts";

const entry = (slug: string, label: string, sort: number, hidden = false): ProfileResponse => ({
  slug,
  label,
  code_work: slug === "development",
  title_words: [],
  sort,
  hidden,
  customized: false,
});

const CATALOG = [
  entry("qa", "QA and test", 20),
  entry("development", "Development", 10),
  entry("design", "UX and design", 50, true),
];

const member = (overrides: Partial<MemberProfileResponse> = {}): MemberProfileResponse => ({
  pod_id: "pod-pay",
  pod_name: "Payments Pod",
  member_id: "U1",
  member_name: "Sofia Bergmann",
  role: "developer",
  profile: "qa",
  label: "QA and test",
  code_work: false,
  source: "admin",
  checked: true,
  since: "2026-10-12",
  set_by_name: "Asha Rao",
  set_at: null,
  is_lead: false,
  ...overrides,
});

const day = (iso: string) => `day ${iso}`;

test("a pod role reads as the profile the server defaults it to", () => {
  assert.equal(profileForPodRole("developer"), "development");
  assert.equal(profileForPodRole("Scrum master"), "delivery");
  assert.equal(profileForPodRole("product_owner"), "product");
  assert.equal(profileForPodRole("tester"), "development");
  assert.equal(profileForPodRole(null), "development");
});

test("only offered profiles are picked, and a hidden one held now still shows", () => {
  assert.deepEqual(
    offeredProfiles(CATALOG).map((item) => item.slug),
    ["development", "qa"],
  );
  assert.deepEqual(profileOptions(CATALOG, "design"), [
    { value: "development", label: "Development" },
    { value: "qa", label: "QA and test" },
    { value: "design", label: "UX and design (hidden)", disabled: true },
  ]);
  assert.equal(profileLabel(CATALOG, "site_ops"), "Site ops");
});

test("where a profile comes from, in words", () => {
  assert.equal(
    sourceWords(member({ source: "pod_role", since: null, set_by_name: null }), day),
    "From their pod role; nobody checked it yet.",
  );
  assert.equal(
    sourceWords(member({ source: "backfill" }), day),
    "From their pod role; nobody checked it yet.",
  );
  assert.equal(sourceWords(member(), day), "Set by Asha Rao on day 2026-10-12.");
  assert.equal(
    sourceWords(member({ source: "import" }), day),
    "Set when they were imported on day 2026-10-12.",
  );
  assert.equal(
    sourceWords(member({ source: "suggestion_accepted", set_by_name: null }), day),
    "Suggestion accepted on day 2026-10-12.",
  );
  assert.equal(isChecked(member({ source: "backfill" })), false);
});

test("a chip says the role, then the profile, and marks one read from the role", () => {
  assert.equal(chipNote("Developer", member()), "Developer · QA and test");
  assert.equal(
    chipNote("Developer", member({ source: "pod_role", label: "Development" })),
    "Developer · Development (from the role)",
  );
  assert.equal(chipNote("Developer", undefined), "Developer");
  assert.equal(chipNote("", undefined), undefined);
});

test("a suggestion is said with its reasons", () => {
  assert.equal(
    suggestionLine({
      profile: "qa",
      label: "QA and test",
      reasons: ["their title says 'QA Engineer'", "3 of their 4 issues in this pod are Bug"],
    }),
    "Suggested: QA and test, because their title says 'QA Engineer' and 3 of their 4 issues in this pod are Bug.",
  );
  assert.equal(
    suggestionLine({ profile: "qa", label: "QA and test", reasons: [] }),
    "Suggested: QA and test.",
  );
});

const item = (
  name: string,
  pod: string,
  now: string,
  suggested: string,
): ProfileReviewItemResponse => ({
  current: member({ member_name: name, pod_name: pod, profile: now, source: "backfill" }),
  suggestion: { profile: suggested, label: suggested.toUpperCase(), reasons: [] },
});

test("the review lists changes first and Accept all says what it changes", () => {
  const items = [
    item("Kai", "Payments Pod", "development", "development"),
    item("Sofia", "Payments Pod", "development", "qa"),
  ];
  assert.deepEqual(
    reviewOrder(items).map((each) => each.current.member_name),
    ["Sofia", "Kai"],
  );
  const words = acceptAllWords(items);
  assert.equal(words.title, "Accept 2 suggestions?");
  assert.match(words.effect, /^1 of 2 change; the rest keep the profile they have/);
  assert.match(words.effect, /grants no rights/);
  assert.deepEqual(words.names, ["Kai in Payments Pod: DEVELOPMENT", "Sofia in Payments Pod: QA"]);
  assert.equal(reviewSummary(0), null);
  assert.equal(
    reviewSummary(1),
    "1 profile comes from the pod role and nobody has checked it yet.",
  );
});

test("a refused change outside Admin says it is not available, with the server's reason", () => {
  assert.equal(
    profileRefusal({
      status: 403,
      message: "You set profiles in the pods you run, and this is not one of them.",
    }),
    "Not available to you. The server said: You set profiles in the pods you run, and this is not one of them.",
  );
  assert.equal(profileRefusal({ status: 403 }), "Not available to you.");
  assert.equal(profileRefusal({ status: 422, message: "hidden" }), null);
  assert.equal(profileRefusal("boom"), null);
});

test("the done and import lines name the person, the profile and the pod", () => {
  assert.equal(profileDone(member()), "Sofia Bergmann is now QA and test in Payments Pod.");
  assert.equal(
    importPlacementWords(2, "Payments Pod", "Developer", "QA and test"),
    "They all join Payments Pod as developer with the QA and test profile. Someone already in the pod keeps their role.",
  );
  assert.equal(
    importPlacementWords(1, "Payments Pod", "Scrum master", null),
    "They join Payments Pod as scrum master, their profile read from the role until someone sets it. Someone already in the pod keeps their role.",
  );
});
