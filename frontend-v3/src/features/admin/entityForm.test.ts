import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConfigNodeResponse } from "../../api/schema";
import {
  buildUpdate,
  confidenceValue,
  formFromNode,
  hasChanges,
  isIsoDay,
  nodeDetails,
  podRepoProblem,
  projectRepoProblem,
  repoList,
  validate,
} from "./entityForm.ts";

const node = (
  kind: ConfigNodeResponse["kind"],
  extra: Partial<ConfigNodeResponse> = {},
): ConfigNodeResponse => ({
  id: `${kind}-x`,
  kind,
  name: "Thing",
  github_repos: [],
  metadata: {},
  ...extra,
});

const project = node("project", {
  name: "Checkout Revamp",
  code: "CHK",
  jira_project_key: "CHK",
  github_repos: ["acme/api", "acme/web"],
  metadata: { code: "CHK", jira_project_key: "CHK" },
});

test("a repository list takes lines, commas and spaces, once each", () => {
  assert.deepEqual(repoList("acme/api\nacme/web, acme/api  acme/x\n"), [
    "acme/api",
    "acme/web",
    "acme/x",
  ]);
  assert.deepEqual(repoList("  \n "), []);
});

test("an untouched form changes nothing", () => {
  const update = buildUpdate("project", project, formFromNode(project));
  assert.deepEqual(update, {});
  assert.equal(hasChanges(update), false);
});

test("only the field that changed is sent", () => {
  const form = { ...formFromNode(project), name: "  Checkout 2  " };
  assert.deepEqual(buildUpdate("project", project, form), { name: "Checkout 2" });
});

test("clearing a field sends null, and a blank stays blank", () => {
  const form = { ...formFromNode(project), jiraProjectKey: "  ", description: "" };
  assert.deepEqual(buildUpdate("project", project, form), { jira_project_key: null });
});

test("a repository change sends the whole new list", () => {
  const form = { ...formFromNode(project), repos: "acme/web\nacme/new" };
  assert.deepEqual(buildUpdate("project", project, form), {
    github_repos: ["acme/web", "acme/new"],
  });
  const cleared = { ...formFromNode(project), repos: "" };
  assert.deepEqual(buildUpdate("project", project, cleared), { github_repos: [] });
});

test("a program has no Jira, Git or workstream fields to send", () => {
  const program = node("program", { name: "Platform", description: "All delivery" });
  const form = {
    ...formFromNode(program),
    repos: "acme/x",
    jiraProjectKey: "X",
    type: "feature",
    description: "Everything",
  };
  assert.deepEqual(buildUpdate("program", program, form), { description: "Everything" });
});

test("a pod sends its Jira filter and repositories, never a project key", () => {
  const pod = node("pod", { name: "Payments Pod" });
  const form = { ...formFromNode(pod), jiraFilterJql: "labels = payments", jiraProjectKey: "ZZ" };
  assert.deepEqual(buildUpdate("pod", pod, form), { jira_filter_jql: "labels = payments" });
});

test("a code is only sent for a project or a node that already has one", () => {
  const pod = node("pod");
  assert.deepEqual(buildUpdate("pod", pod, { ...formFromNode(pod), code: "PAY" }), {});
  const coded = node("pod", { code: "OLD" });
  assert.deepEqual(buildUpdate("pod", coded, { ...formFromNode(coded), code: "NEW" }), {
    code: "NEW",
  });
});

test("a workstream sends only the metadata keys that changed", () => {
  const ws = node("workstream", {
    metadata: { type: "feature", phase: "build", owner_id: "U1001", confidence: 0.5 },
  });
  const form = { ...formFromNode(ws), phase: "rollout", ownerId: "", confidence: "0.8" };
  assert.deepEqual(buildUpdate("workstream", ws, form), {
    metadata: { phase: "rollout", owner_id: null, confidence: 0.8 },
  });
});

test("a workstream keeps a value the lists do not offer", () => {
  const ws = node("workstream", { metadata: { phase: "Rollout" } });
  assert.equal(formFromNode(ws).phase, "Rollout");
  assert.deepEqual(buildUpdate("workstream", ws, formFromNode(ws)), {});
});

test("a name is required, a date must be a real day, a confidence between 0 and 1", () => {
  const form = formFromNode(project);
  assert.deepEqual(validate(form), {});
  assert.equal(validate({ ...form, name: "   " }).name, "Give it a name.");
  assert.equal(
    validate({ ...form, targetDate: "30/10/2026" }).targetDate,
    "Use a date such as 2026-10-30.",
  );
  assert.equal(
    validate({ ...form, targetDate: "2026-02-31" }).targetDate,
    "Use a date such as 2026-10-30.",
  );
  assert.equal(validate({ ...form, targetDate: "2026-10-30" }).targetDate, undefined);
  assert.equal(
    validate({ ...form, confidence: "1.5" }).confidence,
    "Use a number from 0 to 1, for example 0.7.",
  );
  assert.equal(
    validate({ ...form, confidence: "high" }).confidence,
    "Use a number from 0 to 1, for example 0.7.",
  );
  assert.equal(validate({ ...form, confidence: "" }).confidence, undefined);
});

test("dates and confidences are read the way the form writes them", () => {
  assert.equal(isIsoDay("2026-10-06"), true);
  assert.equal(isIsoDay("2026-13-01"), false);
  assert.equal(confidenceValue(""), null);
  assert.equal(confidenceValue("0"), 0);
  assert.equal(confidenceValue("0.65"), 0.65);
  assert.equal(confidenceValue("-0.1"), "invalid");
});

test("a repository problem blocks the save with the rule's own words", () => {
  const form = formFromNode(project);
  assert.equal(validate(form, { repoProblem: "x" }).repos, "x");
});

test("a pod can only list the repositories of its projects", () => {
  assert.equal(podRepoProblem(["acme/api"], ["acme/api", "acme/web"], ["Checkout Revamp"]), null);
  assert.equal(
    podRepoProblem(["acme/api", "acme/other"], ["acme/api"], ["Checkout Revamp"]),
    "acme/other is not in Checkout Revamp. A pod can only read repositories its projects use, so add it to the project first.",
  );
  assert.equal(
    podRepoProblem(["acme/a", "acme/b"], [], []),
    "A pod can only read repositories its projects use. Put it on a project first, under Links.",
  );
});

test("a project cannot give up a repository a pod still reads", () => {
  assert.equal(projectRepoProblem([]), null);
  assert.equal(
    projectRepoProblem([{ podName: "Payments Pod", repos: ["acme/api"] }]),
    "Payments Pod still reads acme/api. Remove it from the pod first, or keep it here.",
  );
  assert.equal(
    projectRepoProblem([
      { podName: "Payments Pod", repos: ["acme/api", "acme/web"] },
      { podName: "Data Pod", repos: ["acme/x"] },
    ]),
    "Payments Pod still reads acme/api and acme/web; Data Pod still reads acme/x. Remove them from the pods first, or keep them here.",
  );
});

test("a row's facts read in the words a person would use", () => {
  assert.deepEqual(nodeDetails("project", project), ["Jira CHK", "2 repositories"]);
  assert.deepEqual(nodeDetails("project", node("project", { code: "CHK" })), ["CHK"]);
  assert.deepEqual(nodeDetails("pod", node("pod", { jira_filter_jql: "x" }), { people: 1 }), [
    "1 person",
    "Jira filter",
  ]);
  assert.deepEqual(
    nodeDetails(
      "workstream",
      node("workstream", {
        metadata: { type: "feature", phase: "build", target_date: "2026-10-30" },
      }),
    ),
    ["feature", "build", "target 30 Oct 2026"],
  );
  assert.deepEqual(nodeDetails("member", node("developer", { metadata: { title: "SRE" } })), [
    "SRE",
  ]);
  assert.deepEqual(nodeDetails("program", node("program")), []);
});
