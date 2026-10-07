import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConfigNodeResponse, DirectoryItemResponse } from "../../api/schema";
import {
  deleteImpact,
  deriveLinks,
  linkDone,
  listNames,
  mergePodTasks,
  newlyOutsideScope,
  parentsOf,
  plural,
  podRepoScope,
  podRoleLabel,
  podsOutsideScope,
  suggestPodRole,
  type Links,
  type RepoScope,
  unlinkWords,
  withoutLink,
} from "./structure.ts";

const item = (
  kind: DirectoryItemResponse["kind"],
  id: string,
  extra: Partial<DirectoryItemResponse> = {},
): DirectoryItemResponse => ({
  id,
  kind,
  name: id,
  description: null,
  code: null,
  metadata: {},
  rag: null,
  source: null,
  program_ids: [],
  project_ids: [],
  workstream_ids: [],
  pod_ids: [],
  member_ids: [],
  task_ids: [],
  people: [],
  in_use: true,
  ...extra,
});

const node = (
  kind: ConfigNodeResponse["kind"],
  id: string,
  extra: Partial<ConfigNodeResponse> = {},
): ConfigNodeResponse => ({ id, kind, name: id, github_repos: [], metadata: {}, ...extra });

const names = (id: string) => `<${id}>`;

test("counts and lists read as sentences", () => {
  assert.equal(plural(1, "project"), "1 project");
  assert.equal(plural(0, "workstream"), "0 workstreams");
  assert.equal(plural(2, "person", "people"), "2 people");
  assert.equal(listNames([]), "");
  assert.equal(listNames(["A"]), "A");
  assert.equal(listNames(["A", "B"]), "A and B");
  assert.equal(listNames(["A", "B", "C"]), "A, B and C");
  assert.equal(listNames(["A", "B", "C", "D", "E"]), "A, B, C and 2 more");
});

test("a link is found from either end, and an empty workstream still counts", () => {
  const links = deriveLinks({
    programs: [item("program", "prog", { project_ids: ["p1"] })],
    projects: [
      item("project", "p1", { program_ids: ["prog"], pod_ids: ["pod1"] }),
      item("project", "p2", { program_ids: ["prog"] }),
    ],
    pods: [
      item("pod", "pod1", { project_ids: ["p1"], member_ids: ["U1", "U2"] }),
      // Says it works on p2 although p2's own list does not name it.
      item("pod", "pod2", { project_ids: ["p2"] }),
    ],
    workstreams: [
      // The directory lists leave this one out; a direct read of it names its project and pod.
      item("workstream", "ws-empty", { project_ids: ["p1"], pod_ids: ["pod1"], in_use: false }),
      item("workstream", "ws-used", { project_ids: ["p2"], task_ids: ["T-1"] }),
    ],
  });
  assert.deepEqual(links.programProjects, { prog: ["p1", "p2"] });
  assert.deepEqual(links.projectPods, { p1: ["pod1"], p2: ["pod2"] });
  assert.deepEqual(links.projectWorkstreams, { p1: ["ws-empty"], p2: ["ws-used"] });
  assert.deepEqual(links.podWorkstreams, { pod1: ["ws-empty"], pod2: [] });
  assert.deepEqual(links.podMembers, { pod1: ["U1", "U2"], pod2: [] });
  assert.deepEqual(links.workstreamTasks, { "ws-empty": [], "ws-used": ["T-1"] });
});

test("the parents of a child, and the links with one taken out", () => {
  const map = { pod1: ["U1", "U2"], pod2: ["U2"] };
  assert.deepEqual(parentsOf(map, "U2"), ["pod1", "pod2"]);
  assert.deepEqual(parentsOf(map, "U3"), []);
  assert.deepEqual(withoutLink(map, "pod1", "U2"), { pod1: ["U1"], pod2: ["U2"] });
});

test("a pod role reads in words; an unknown role is still readable", () => {
  assert.equal(podRoleLabel("scrum_master"), "Scrum master");
  assert.equal(podRoleLabel("tech_lead"), "Tech lead");
  assert.equal(podRoleLabel(null), "");
});

test("a person's pod role is suggested from what they do in the console", () => {
  assert.equal(suggestPodRole("sm"), "scrum_master");
  assert.equal(suggestPodRole("po"), "product_owner");
  assert.equal(suggestPodRole("mgr,admin"), "manager");
  assert.equal(suggestPodRole("dev"), "developer");
  assert.equal(suggestPodRole("exec"), "developer");
  assert.equal(suggestPodRole(undefined), "developer");
});

const scope = (overrides: Partial<RepoScope> = {}): RepoScope => ({
  links: {
    programProjects: {},
    projectPods: { checkout: ["payments"], insights: ["data"] },
    projectWorkstreams: {},
    podWorkstreams: {},
    podMembers: {},
    workstreamTasks: {},
  },
  projectRepos: { checkout: ["acme/api", "acme/web"], insights: ["acme/pipeline"] },
  podRepos: { payments: ["acme/api"], data: [] },
  ...overrides,
});

test("a pod may list the repositories of the projects it works on", () => {
  assert.deepEqual(podRepoScope("payments", scope()), ["acme/api", "acme/web"]);
  assert.deepEqual(podRepoScope("nobody", scope()), []);
  assert.deepEqual(podsOutsideScope(scope()), []);
});

test("a pod listing a repository no project of its provides is outside its scope", () => {
  assert.deepEqual(
    podsOutsideScope(scope({ podRepos: { payments: ["acme/api", "acme/other"] } })),
    [{ podId: "payments", repos: ["acme/other"] }],
  );
});

test("a change is blamed only for what it newly breaks", () => {
  const before = scope({ podRepos: { payments: ["acme/api", "acme/old"] } });
  // The project drops acme/api: the pod's acme/api is newly out of scope; acme/old already was.
  const after: RepoScope = {
    ...before,
    projectRepos: { ...before.projectRepos, checkout: ["acme/web"] },
  };
  assert.deepEqual(newlyOutsideScope(before, after), [{ podId: "payments", repos: ["acme/api"] }]);
  assert.deepEqual(newlyOutsideScope(before, before), []);
});

test("taking a pod off its project puts its repositories out of scope", () => {
  const before = scope();
  const after: RepoScope = {
    ...before,
    links: {
      ...before.links,
      projectPods: withoutLink(before.links.projectPods, "checkout", "payments"),
    },
  };
  assert.deepEqual(newlyOutsideScope(before, after), [{ podId: "payments", repos: ["acme/api"] }]);
});

test("a link made or taken away is reported as a plain sentence", () => {
  assert.equal(linkDone("pod-member", "Payments Pod", "Kai", true), "Kai is now in Payments Pod.");
  assert.equal(
    linkDone("project-pod", "Checkout", "Data Pod", false),
    "Data Pod is no longer on Checkout.",
  );
  assert.equal(linkDone("member-task", "Kai", "CHK-1", true), "CHK-1 is now assigned to Kai.");
  assert.equal(linkDone("pod-workstream", "Data Pod", "Cart", true), "Data Pod now works on Cart.");
  assert.equal(
    linkDone("pod-workstream", "Data Pod", "Cart", false),
    "Data Pod no longer works on Cart.",
  );
});

test("every link says what taking it away does", () => {
  assert.deepEqual(unlinkWords("pod-member", "Payments Pod", "Kai Thompson"), {
    title: "Take Kai Thompson out of Payments Pod?",
    effect:
      "Kai Thompson's check-in stops counting toward Payments Pod, and if they miss one, Payments Pod's scrum master and manager are no longer told. With no other pod, they show as in no team. The link ends today; earlier days still show it. Adding it back starts a new link today.",
  });
  assert.match(
    unlinkWords("member-task", "Kai", "CHK-1").effect,
    /next Jira sync assigns it again/,
  );
  assert.match(unlinkWords("program-project", "Program", "Checkout").effect, /portfolio views/);
  assert.match(
    unlinkWords("workstream-task", "Cart", "CHK-2").effect,
    /hidden from Delivery and Today/,
  );
});

const links: Links = {
  programProjects: { prog: ["p1", "p2"] },
  projectPods: { p1: ["pod1"], p2: [] },
  projectWorkstreams: { p1: ["ws1"], p2: [] },
  podWorkstreams: { pod1: ["ws1"] },
  podMembers: { pod1: ["U1", "U2"] },
  workstreamTasks: { ws1: ["T-1", "T-2", "T-3"] },
};

test("deleting a program says its projects stay but leave the portfolio", () => {
  const impact = deleteImpact("program", node("program", "prog"), links, names);
  assert.equal(
    impact.lines[0],
    "2 projects (<p1> and <p2>) stay, but they are no longer in a program, so they drop out of the portfolio views until they join one again.",
  );
  assert.equal(
    impact.lines.at(-1),
    "Nothing else is deleted. Its links end today, and earlier days still show it with them.",
  );
  assert.deepEqual(impact.warnings, []);
});

test("deleting a project or pod counts the tasks that lose it", () => {
  const impact = deleteImpact("pod", node("pod", "pod-a"), links, names, { tasks: 3 });
  assert.ok(
    impact.lines.includes(
      "3 tasks and any Jira sprints under it lose their pod: they stay, but nothing groups them under it any more.",
    ),
  );
  const none = deleteImpact("pod", node("pod", "pod-a"), links, names, { tasks: 0 });
  assert.ok(!none.lines.some((line) => line.includes("Jira sprints")));
});

test("deleting a project counts its pods, workstreams and reports", () => {
  const impact = deleteImpact(
    "project",
    node("project", "p1", { jira_project_key: "CHK", github_repos: ["acme/api"] }),
    links,
    names,
    { dayReports: 2 },
  );
  assert.deepEqual(impact.lines.slice(0, -1), [
    "It leaves <prog>, so it drops out of that program's status.",
    "1 pod (<pod1>) stays, but no longer rolls up into it.",
    "1 workstream (<ws1>) stays, but is no longer in a project.",
    "2 day reports set up for it stay in Reports, but every send fails because the project is gone. Remove them first if you don't want that.",
    "Its Jira key and repositories go with it, so the syncs stop reading them.",
  ]);
});

test("deleting a pod says what happens to its people and its contacts", () => {
  const impact = deleteImpact("pod", node("pod", "pod1"), links, names);
  assert.ok(
    impact.lines.some((line) => line.startsWith("2 people (<U1> and <U2>) stay as members")),
  );
  assert.ok(impact.lines.some((line) => line.includes("escalation contacts")));
});

test("deleting something nothing links to says so", () => {
  const impact = deleteImpact("workstream", node("workstream", "lonely"), links, names);
  assert.equal(impact.lines[0], "Nothing is linked to it.");
});

test("a delete that would break the Git sync warns, by name", () => {
  const impact = deleteImpact("project", node("project", "p1"), links, names, {
    outsideScope: [{ podId: "pod1", repos: ["acme/api"] }],
  });
  assert.equal(
    impact.warnings[0],
    "<pod1> lists acme/api, which none of its other projects provide. The Git sync reports a configuration error until you clear it from the pod.",
  );
});

test("a task two pods hold is one, with both pods' owners and blockers", () => {
  const task = (owner: string, blocker: string) => ({
    id: "task-chk-12",
    name: "CHK-12",
    rag: "amber" as const,
    owners: [{ id: owner, name: owner }],
    open_blockers: [
      { blocker_id: blocker, description: blocker, first_seen_on: "2026-10-01", age_days: 6 },
    ],
  });
  const merged = mergePodTasks([
    [task("U1007", "b1") as never],
    [task("U1003", "b2") as never, task("U1007", "b1") as never],
  ]);
  assert.equal(merged.length, 1);
  assert.deepEqual(
    merged[0].owners.map((owner) => owner.id),
    ["U1007", "U1003"],
  );
  assert.deepEqual(
    merged[0].open_blockers.map((blocker) => blocker.blocker_id),
    ["b1", "b2"],
  );
});
