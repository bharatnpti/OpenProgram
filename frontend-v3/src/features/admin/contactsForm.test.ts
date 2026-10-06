import assert from "node:assert/strict";
import { test } from "node:test";

import type { EscalationCandidateResponse, PodEscalationContactsResponse } from "../../api/schema";
import {
  KEEP_SAVED,
  buildContactsUpdate,
  candidateLabel,
  contactChoice,
  contactName,
  contactState,
  groupCandidates,
  initialPick,
  initialPicks,
  picksChanged,
} from "./contactsForm.ts";

const candidate = (
  id: string,
  extra: Partial<EscalationCandidateResponse> = {},
): EscalationCandidateResponse => ({
  member_id: id,
  name: id,
  chat_user_id: id,
  in_pod: false,
  pod_role: null,
  ...extra,
});

const saved = (
  extra: Partial<PodEscalationContactsResponse> = {},
): PodEscalationContactsResponse => ({
  pod_id: "pod-payments",
  scrum_master: null,
  manager: null,
  ...extra,
});

test("a saved contact shows as its member, or as kept when no member matches", () => {
  assert.equal(initialPick(null), "");
  assert.equal(initialPick({ chat_external_id: "U1", member_id: "U1", display_name: "Ira" }), "U1");
  assert.equal(
    initialPick({ chat_external_id: "U9", member_id: null, display_name: null }),
    KEEP_SAVED,
  );
});

test("picking a member sends the member; keeping sends the saved chat id; nobody clears", () => {
  assert.deepEqual(contactChoice("U1006", null), { member_id: "U1006" });
  assert.deepEqual(
    contactChoice(KEEP_SAVED, { chat_external_id: "U9", member_id: null, display_name: null }),
    { chat_external_id: "U9" },
  );
  assert.equal(contactChoice(KEEP_SAVED, null), null);
  assert.equal(contactChoice("", null), null);
});

test("a save always sends both contacts, because the server clears one that is left out", () => {
  const before = saved({
    scrum_master: { chat_external_id: "U1006", member_id: "U1006", display_name: "Ira Novak" },
  });
  const picks = { ...initialPicks(before), manager: "U1001" };
  assert.deepEqual(buildContactsUpdate(picks, before), {
    scrum_master: { member_id: "U1006" },
    manager: { member_id: "U1001" },
  });
  assert.deepEqual(buildContactsUpdate({ scrum_master: "", manager: "" }, before), {
    scrum_master: null,
    manager: null,
  });
});

test("saving is only offered once a pick differs from what is saved", () => {
  const before = saved({
    manager: { chat_external_id: "U1001", member_id: "U1001", display_name: "Asha Rao" },
  });
  assert.equal(picksChanged(initialPicks(before), before), false);
  assert.equal(picksChanged({ scrum_master: "U1006", manager: "U1001" }, before), true);
  assert.equal(picksChanged({ scrum_master: "", manager: "" }, before), true);
  assert.equal(picksChanged({ scrum_master: "", manager: "" }, undefined), false);
});

test("a candidate says its role in the pod and why it cannot be picked", () => {
  assert.equal(
    candidateLabel(candidate("Ira Novak", { in_pod: true, pod_role: "scrum_master" })),
    "Ira Novak · Scrum master",
  );
  assert.equal(candidateLabel(candidate("Raj", { chat_user_id: null })), "Raj · no chat id yet");
});

test("pod members come first, the ones holding the role at the very top", () => {
  const list = [
    candidate("Zed"),
    candidate("Dev", { in_pod: true, pod_role: "developer" }),
    candidate("Ira", { in_pod: true, pod_role: "scrum_master" }),
  ];
  const groups = groupCandidates(list, "scrum_master");
  assert.deepEqual(
    groups.inPod.map((c) => c.member_id),
    ["Ira", "Dev"],
  );
  assert.deepEqual(
    groups.others.map((c) => c.member_id),
    ["Zed"],
  );
});

test("a contact is named, or shown by the chat id it was saved with", () => {
  assert.equal(contactName(null), null);
  assert.equal(
    contactName({ chat_external_id: "U1", display_name: "Ira Novak", member_id: "U1" }),
    "Ira Novak",
  );
  assert.equal(contactName({ chat_external_id: "U9", display_name: null, member_id: null }), "U9");
});

test("a pod says whether anyone above the person would be told", () => {
  assert.deepEqual(contactState(saved()), {
    tone: "warning",
    label: "Nobody above the person is told",
  });
  const sm = { chat_external_id: "U1", member_id: "U1", display_name: "Ira" };
  assert.deepEqual(contactState(saved({ scrum_master: sm })), {
    tone: "warning",
    label: "Scrum master only",
  });
  assert.deepEqual(contactState(saved({ manager: sm })), {
    tone: "warning",
    label: "Manager only",
  });
  assert.deepEqual(contactState(saved({ scrum_master: sm, manager: sm })), {
    tone: "success",
    label: "Both set",
  });
});
