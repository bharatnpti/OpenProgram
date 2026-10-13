import assert from "node:assert/strict";
import { test } from "node:test";

import { workPods } from "./workPods.ts";

const POD = (id: string, rag: "red" | "green") => ({ id, name: id, rag });

test("a person's own pods open as the tree lists them; a name-only pod has no colour or link", () => {
  assert.deepEqual(
    workPods(
      undefined,
      [],
      [
        { ...POD("pod-payments", "red"), opens: true },
        { ...POD("pod-storefront", "green"), opens: false },
      ],
      true,
    ),
    [
      { id: "pod-payments", name: "pod-payments", rag: "red", opens: true },
      { id: "pod-storefront", name: "pod-storefront", rag: null, opens: false },
    ],
  );
});

test("everyone else gets the project's pods from the directory, opening where they have Pods", () => {
  const project = { pod_ids: ["pod-payments"] };
  const pods = [POD("pod-payments", "red"), POD("pod-data", "green")];
  assert.deepEqual(workPods(project, pods, null, true), [
    { id: "pod-payments", name: "pod-payments", rag: "red", opens: true },
  ]);
  // An executive has no Pods: the names, nothing to open.
  assert.equal(workPods(project, pods, null, false)[0].opens, false);
  assert.deepEqual(workPods(undefined, pods, null, true), []);
});
