import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryTreeResponse } from "../../api/schema";
import { developerTree, productOwnerTree, scrumMasterTree } from "./ownTree.fixture.ts";
import { fullPodIds, ownGroups, ownLanding, ownLinks, ownProjects, trailOf } from "./ownTree.ts";

/** What the navigator draws: each project and its pods, with dot (or none) and whether it opens. */
const drawn = (tree: DeliveryTreeResponse) =>
  ownGroups(tree).map((group) => ({
    heading: group.program?.name ?? null,
    projects: group.projects.map((project) => ({
      project: [project.name, project.rag],
      pods: project.pods.map((pod) => [pod.name, pod.rag, pod.opens ? "opens" : "name only"]),
    })),
  }));

test("a developer sees every pod of their project, data and colour only for their own", () => {
  assert.deepEqual(drawn(developerTree), [
    {
      heading: "Digital Platform Program",
      projects: [
        {
          project: ["Checkout Revamp", null],
          pods: [
            ["Payments Pod", "red", "opens"],
            ["Storefront Pod", null, "name only"],
          ],
        },
      ],
    },
  ]);
  const [project] = ownProjects(developerTree);
  assert.equal(project.full, false, "no project-level data for a developer");
  assert.equal(project.opens, true, "it still opens, on its name and pods");
});

test("a scrum master sees their projects worst first, each with all its pods, own pods first", () => {
  assert.deepEqual(drawn(scrumMasterTree), [
    {
      heading: "Digital Platform Program",
      projects: [
        {
          project: ["Checkout Revamp", "red"],
          pods: [
            ["Payments Pod", "red", "opens"],
            ["Storefront Pod", "green", "opens"],
          ],
        },
        { project: ["Identity Platform", "amber"], pods: [["Identity Pod", "amber", "opens"]] },
      ],
    },
  ]);
  // The whole panel only for the pods they run; another pod opens on its dates.
  assert.deepEqual([...fullPodIds(scrumMasterTree)].sort(), ["pod-identity", "pod-payments"]);
});

test("a product owner's pods all open on their dates, none on its people", () => {
  const [project] = ownProjects(productOwnerTree);
  assert.equal(project.full, true);
  assert.deepEqual(
    project.pods.map((pod) => [pod.name, pod.opens, pod.full]),
    [
      ["Payments Pod", true, false],
      ["Storefront Pod", true, false],
    ],
  );
  assert.equal(fullPodIds(productOwnerTree).size, 0);
});

test("Delivery opens on the first project the person reads, else a developer's own pod", () => {
  assert.equal(ownLanding(scrumMasterTree), "/delivery/project/project-checkout");
  assert.equal(ownLanding(productOwnerTree), "/delivery/project/project-checkout");
  assert.equal(ownLanding(developerTree), "/delivery/pod/pod-payments");
  assert.equal(ownLanding({ as_of: "2026-10-06", programs: [], projects: [], pods: [] }), null);
});

test("a project in no program comes last, under no heading", () => {
  const tree: DeliveryTreeResponse = {
    ...productOwnerTree,
    projects: [
      ...productOwnerTree.projects,
      {
        id: "project-loose",
        kind: "project",
        name: "Loose Ends",
        rag: "red",
        access: "panel",
        own: true,
        parent_ids: [],
      },
    ],
  };
  assert.deepEqual(
    ownGroups(tree).map((group) => [
      group.program?.name ?? null,
      group.projects.map((p) => p.name),
    ]),
    [
      ["Digital Platform Program", ["Checkout Revamp"]],
      [null, ["Loose Ends"]],
    ],
  );
});

test("the links a redirect needs: listed projects, and whether a listed pod opens", () => {
  const links = ownLinks(developerTree);
  assert.deepEqual(links.projects, ["project-checkout"]);
  assert.deepEqual(links.pod("pod-payments"), { opens: true, projectIds: ["project-checkout"] });
  assert.deepEqual(links.pod("pod-storefront"), { opens: false, projectIds: ["project-checkout"] });
  assert.equal(links.pod("pod-data"), null);
});

test("the trail above a panel names the program, and links the project from a pod", () => {
  assert.deepEqual(trailOf(developerTree, "project", "project-checkout"), [
    { label: "Digital Platform Program", to: null },
  ]);
  assert.deepEqual(trailOf(developerTree, "pod", "pod-payments"), [
    { label: "Digital Platform Program", to: null },
    { label: "Checkout Revamp", to: "/delivery/project/project-checkout" },
  ]);
  assert.deepEqual(trailOf(developerTree, "pod", "pod-nowhere"), []);
});
