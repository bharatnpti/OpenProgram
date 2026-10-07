import assert from "node:assert/strict";
import { test } from "node:test";

import type { GateTemplateDto } from "../../api/schema";
import {
  appliesToLine,
  draftFromTemplate,
  draftProblems,
  kindKey,
  newKindDraft,
  newTemplateDraft,
  readFromLine,
  signOffLine,
  splitList,
  templateFromDraft,
  toggleRole,
  withLabel,
} from "./gateForm.ts";

// The default "Business acceptance" gate, as GET /config/gates returns it.
const ACCEPTANCE: GateTemplateDto = {
  template_id: "business-acceptance",
  name: "Business acceptance",
  guards_stage: "production",
  kinds: [
    {
      key: "acceptance",
      label: "Acceptance criterion",
      sign_off_roles: ["po", "mgr"],
      evidence_required: false,
      headings: ["Acceptance criteria", "AC"],
      gherkin: false,
    },
  ],
  issue_types: [],
  enabled: true,
};

test("a gate survives the editor unchanged", () => {
  assert.deepEqual(templateFromDraft(draftFromTemplate(ACCEPTANCE)), ACCEPTANCE);
});

test("a new item's key follows its name; a saved item keeps its key", () => {
  assert.equal(withLabel(newKindDraft(), "Security review").key, "security_review");
  const saved = draftFromTemplate(ACCEPTANCE).kinds[0];
  assert.equal(withLabel(saved, "Criterion").key, "acceptance");
  assert.equal(kindKey("  Données / tests "), "données_tests");
});

test("lists split on commas and lines, without repeats", () => {
  assert.deepEqual(splitList("Story, story,  Bug\nEpic,"), ["Story", "Bug", "Epic"]);
});

test("sign-off roles toggle on and off", () => {
  const kind = toggleRole(newKindDraft(), "sm");
  assert.deepEqual(kind.signOffRoles, ["po", "sm"]);
  assert.deepEqual(toggleRole(kind, "po").signOffRoles, ["sm"]);
});

test("the editor says what the server would refuse", () => {
  const draft = newTemplateDraft();
  assert.deepEqual(draftProblems(draft), ["Give the gate a name.", "Item 1 needs a name."]);

  const twice = {
    ...draftFromTemplate(ACCEPTANCE),
    kinds: [
      withLabel(newKindDraft(), "Test case"),
      { ...withLabel(newKindDraft(), "Test case"), signOffRoles: [] },
    ],
  };
  assert.deepEqual(draftProblems(twice), [
    "Two items are both called “Test case”.",
    "Say who signs off Test case.",
  ]);

  const many = {
    ...draftFromTemplate(ACCEPTANCE),
    kinds: Array.from({ length: 11 }, (_, index) => withLabel(newKindDraft(), `Item ${index}`)),
  };
  assert.ok(draftProblems(many).includes("A gate checks at most 10 kinds of item."));
});

test("a gate reads as sentences", () => {
  const [kind] = ACCEPTANCE.kinds;
  assert.equal(signOffLine(kind), "Signed off by the product owner or the manager.");
  assert.equal(
    signOffLine({ ...kind, sign_off_roles: ["dev", "sm"], evidence_required: true }),
    "Signed off by the developer or the scrum master, with a link to the evidence.",
  );
  assert.equal(
    readFromLine({ ...kind, gherkin: true }),
    "Suggested from Jira under “Acceptance criteria” or “AC”, and from Given/When/Then scenarios.",
  );
  assert.equal(readFromLine({ ...kind, headings: [] }), null);
  assert.equal(appliesToLine(ACCEPTANCE), "Every requirement");
  assert.equal(
    appliesToLine({ ...ACCEPTANCE, issue_types: ["Story", "Epic"] }),
    "Story and Epic only",
  );
});
