import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProfileResponse } from "../../api/schema";
import {
  draftFromProfile,
  newProfileDraft,
  profileBody,
  profileChanged,
  profileProblems,
  profileSaved,
  slugFromLabel,
  titleWords,
} from "./profileForm.ts";

const entry = (slug: string, label: string, sort: number, hidden = false): ProfileResponse => ({
  slug,
  label,
  code_work: slug === "development",
  title_words: slug === "qa" ? ["qa", "test"] : [],
  sort,
  hidden,
  customized: false,
});

const CATALOG = [entry("development", "Development", 10), entry("qa", "QA and test", 20)];

test("an entry round-trips through the form, and only a real edit counts as a change", () => {
  const draft = draftFromProfile(CATALOG[1]);
  assert.deepEqual(profileBody(draft), {
    label: "QA and test",
    code_work: false,
    hidden: false,
    title_words: ["qa", "test"],
    sort: 20,
  });
  assert.equal(profileChanged(CATALOG[1], draft), false);
  assert.equal(profileChanged(CATALOG[1], { ...draft, titleWordsText: "QA,  test " }), false);
  assert.equal(profileChanged(CATALOG[1], { ...draft, label: "Quality" }), true);
  assert.equal(profileChanged(CATALOG[1], { ...draft, offered: false }), true);
});

test("title words are lower case, each once", () => {
  assert.deepEqual(titleWords("SRE, Site  Reliability, sre, "), ["sre", "site reliability"]);
});

test("a new profile gets an id from its name and starts after the others", () => {
  assert.equal(slugFromLabel("Site reliability"), "site_reliability");
  assert.equal(slugFromLabel("  2nd-line Support! "), "nd_line_support");
  assert.equal(newProfileDraft(CATALOG).sort, 30);
  assert.equal(newProfileDraft(CATALOG).offered, true);
});

test("the form says what the server would refuse", () => {
  const blank = newProfileDraft(CATALOG);
  assert.deepEqual(profileProblems(blank, "", CATALOG, true), [
    "Give it a name of 1 to 40 characters.",
    "Its id is lower-case letters, digits or underscores, starting with a letter.",
  ]);
  assert.deepEqual(profileProblems({ ...blank, label: "QA" }, "qa", CATALOG, true), [
    "There is a profile qa already. Change that one instead.",
  ]);
  assert.deepEqual(
    profileProblems(
      { ...draftFromProfile(CATALOG[1]), label: "Development" },
      "qa",
      CATALOG,
      false,
    ),
    ["Another profile is already called Development."],
  );
  const onlyOne = [entry("qa", "QA and test", 20), entry("development", "Development", 10, true)];
  assert.deepEqual(
    profileProblems({ ...draftFromProfile(onlyOne[0]), offered: false }, "qa", onlyOne, false),
    ["Keep at least one profile offered."],
  );
  assert.deepEqual(
    profileProblems(
      { ...blank, label: "Compliance", titleWordsText: "x".repeat(41) },
      "compliance",
      CATALOG,
      true,
    ),
    ["Name at most 12 title words, each at most 40 characters."],
  );
});

test("the saved line says what hiding does", () => {
  assert.equal(
    profileSaved("Compliance", true, true),
    "Compliance added. It is offered wherever a profile is picked.",
  );
  assert.equal(
    profileSaved("Design", false, false),
    "Design hidden. People who hold it keep it; nobody can pick it.",
  );
});
