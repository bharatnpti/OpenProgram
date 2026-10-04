import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { ALL_CLEAR, heroDetailLine, type HeroDetailInput, type RollupTree } from "./heroDetail.ts";

type TreeNode = Extract<RollupTree, { status: "ready" }>["nodes"][number];
type Factor = TreeNode["factors"][number];
type Tile = HeroDetailInput["tiles"]["pods"][number];

function status(personId: string, contributes: Factor["contributes"] = "amber"): Factor {
  return {
    kind: "status",
    contributes,
    description: "Status reported.",
    blocker_id: null,
    source_ref: { id: personId },
    work_item_ref: null,
  };
}

function blocker(blockerId: string, personId: string, workItem: string | null): Factor {
  return {
    kind: "blocker",
    contributes: "amber",
    description: `Blocker: waiting on review of ${workItem ?? "a change"}.`,
    blocker_id: blockerId,
    source_ref: { id: workItem ?? personId },
    work_item_ref: workItem ? { id: workItem } : null,
  };
}

function node(
  id: string,
  kind: TreeNode["kind"],
  name: string,
  source: TreeNode["source"],
  factors: Factor[],
): TreeNode {
  return { id, kind, name, source, factors };
}

function tiles(prefix: string, rags: Tile["rag"][]): Tile[] {
  return rags.map((rag, index) => ({ id: `${prefix}-${index + 1}`, rag }));
}

// Shaped like a live tenant: one blocker on a ticket, partial updates in every
// amber pod and project, two people only inferred, no risk record, and two
// tickets merged while the tracker still shows them open.
const people = [
  node("u-ana", "developer", "Ana Silva", "partial", [status("u-ana")]),
  node("u-ben", "developer", "Ben Ortiz", "inferred", [status("u-ben")]),
  node("u-cara", "developer", "Cara Diaz", "confirmed", [blocker("b-1", "u-cara", "PAY-12")]),
  node("u-dan", "developer", "Dan Kim", "confirmed", [status("u-dan", "green")]),
];
const liveTree: RollupTree = {
  status: "ready",
  nodes: [
    ...people,
    node("pod-1", "pod", "Data Pod", "partial", [status("u-ben"), status("u-ana")]),
    node("pod-2", "pod", "Identity Pod", "partial", [status("u-ben"), status("u-ana")]),
    node("pod-3", "pod", "Payments Pod", "partial", [
      status("u-ana"),
      blocker("b-1", "u-cara", "PAY-12"),
    ]),
    node("pod-4", "pod", "Platform Pod", "partial", [status("u-ana")]),
    node("pod-5", "pod", "Storefront Pod", "confirmed", [
      { ...status("pod-5", "green"), kind: "aggregate" },
    ]),
    node("project-1", "project", "Checkout", "partial", [
      status("u-ana"),
      blocker("b-1", "u-cara", "PAY-12"),
    ]),
    node("project-2", "project", "Identity", "partial", [status("u-ben"), status("u-ana")]),
    node("project-3", "project", "Insights", "partial", [status("u-ben"), status("u-ana")]),
  ],
};
const mergedButOpen = [{ kind: "merged_issue_open" }, { kind: "merged_issue_open" }];

function input(overrides: Partial<HeroDetailInput> = {}): HeroDetailInput {
  return {
    worstRag: "amber",
    heatLoading: false,
    heatFailed: false,
    risksLoading: false,
    risksFailed: false,
    topRiskReason: undefined,
    drift: mergedButOpen,
    tiles: {
      pods: tiles("pod", ["amber", "amber", "amber", "amber", "green"]),
      projects: tiles("project", ["amber", "amber", "amber"]),
      workstreams: tiles("ws", ["unknown", "unknown", null]),
    },
    tree: liveTree,
    ...overrides,
  };
}

describe("heroDetailLine", () => {
  test("an amber headline with no risk records names what drives it, never the all-clear", () => {
    const line = heroDetailLine(input());

    assert.equal(
      line,
      "1 open blocker (Cara Diaz on PAY-12); 4 of 5 pods and all 3 projects amber, mostly from " +
        "partial updates; 2 tickets merged but still open in Jira.",
    );
    assert.doesNotMatch(line, /No material risks/);
  });

  test("a green headline with no risk records is the all-clear", () => {
    const line = heroDetailLine(
      input({
        worstRag: "green",
        drift: [],
        tiles: {
          pods: tiles("pod", ["green", "green"]),
          projects: tiles("project", ["green"]),
          workstreams: [],
        },
        // The component reads the tree only for an amber or red headline.
        tree: { status: "loading" },
      }),
    );

    assert.equal(line, ALL_CLEAR);
  });

  test("a red headline with a risk record shows the risk's reason", () => {
    const reason = "PAY-12 has had no pull request for 6 days.";
    const line = heroDetailLine(
      input({
        worstRag: "red",
        topRiskReason: reason,
        tiles: {
          pods: tiles("pod", ["red", "amber"]),
          projects: tiles("project", ["red"]),
          workstreams: [],
        },
      }),
    );

    assert.equal(line, reason);
  });

  test("a green headline still names open drift after the all-clear", () => {
    const line = heroDetailLine(
      input({
        worstRag: "green",
        drift: [{ kind: "merged_issue_open" }],
        tiles: { pods: tiles("pod", ["green"]), projects: [], workstreams: [] },
      }),
    );

    assert.equal(line, `${ALL_CLEAR} 1 ticket merged but still open in Jira.`);
  });

  test("loading, failure and silence keep their lines and are never an all-clear", () => {
    assert.equal(
      heroDetailLine(input({ worstRag: "green", risksFailed: true })),
      "Risks could not be loaded, so this is not an all-clear.",
    );
    assert.equal(heroDetailLine(input({ risksLoading: true })), "Checking for open risks…");
    const nothing = "Nothing has reported a status yet.";
    assert.equal(heroDetailLine(input({ heatLoading: true })), nothing);
    assert.equal(heroDetailLine(input({ heatFailed: true })), nothing);
    assert.equal(heroDetailLine(input({ worstRag: "unknown", drift: [] })), nothing);
  });

  test("an amber headline waits for the rollup tree, and says when it could not be read", () => {
    assert.equal(
      heroDetailLine(input({ tree: { status: "loading" } })),
      "Checking what is behind this status…",
    );
    assert.equal(
      heroDetailLine(input({ tree: { status: "failed" } })),
      "4 of 5 pods and all 3 projects amber; 2 tickets merged but still open in Jira. " +
        "Blockers and reasons could not be loaded.",
    );
  });

  test("names at most two blockers, gives each kind its own colour, and leaves blockers unrepeated", () => {
    const tree: RollupTree = {
      status: "ready",
      nodes: [
        node("u-cara", "developer", "Cara Diaz", "confirmed", [
          blocker("b-1", "u-cara", "PAY-12"),
          blocker("b-2", "u-cara", null),
        ]),
        node("u-ben", "developer", "Ben Ortiz", "confirmed", [blocker("b-3", "u-ben", "IDN-4")]),
        node("pod-1", "pod", "Payments Pod", "confirmed", [
          blocker("b-1", "u-cara", "PAY-12"),
          blocker("b-2", "u-cara", null),
        ]),
        node("pod-2", "pod", "Identity Pod", "confirmed", [blocker("b-3", "u-ben", "IDN-4")]),
        node("project-1", "project", "Checkout", "confirmed", [
          blocker("b-1", "u-cara", "PAY-12"),
          blocker("b-2", "u-cara", null),
          blocker("b-3", "u-ben", "IDN-4"),
        ]),
      ],
    };

    const line = heroDetailLine(
      input({
        worstRag: "red",
        drift: [],
        tree,
        tiles: {
          pods: tiles("pod", ["amber", "amber", "green", "green", "green"]),
          projects: tiles("project", ["red"]),
          workstreams: [],
        },
      }),
    );

    assert.equal(
      line,
      "3 open blockers (Cara Diaz on PAY-12, Ben Ortiz on IDN-4 and 1 more); " +
        "2 of 5 pods amber and the only project red.",
    );
  });

  test("tiles amber only from drift say so, beside the drift they come from", () => {
    // Omar's CHK-17 merged while the tracker kept it open: his cell, Platform
    // and the projects holding Platform are amber, though everyone confirmed.
    const drift: Factor = {
      kind: "drift",
      contributes: "amber",
      description: "Signals disagree: CHK-17 merged but still open in Jira.",
      blocker_id: null,
      source_ref: { id: "u-omar" },
      work_item_ref: { id: "CHK-17" },
    };
    const tree: RollupTree = {
      status: "ready",
      nodes: [
        node("u-omar", "developer", "Omar Haddad", "confirmed", [drift]),
        node("pod-1", "pod", "Platform Pod", "confirmed", [drift]),
        node("project-1", "project", "Checkout", "confirmed", [drift]),
        node("project-2", "project", "Identity", "confirmed", [drift]),
      ],
    };

    const line = heroDetailLine(
      input({
        drift: [{ kind: "merged_issue_open" }],
        tree,
        tiles: {
          pods: tiles("pod", ["amber", "green", "green", "green", "green"]),
          projects: tiles("project", ["amber", "amber", "green"]),
          workstreams: [],
        },
      }),
    );

    assert.equal(
      line,
      "1 of 5 pods and 2 of 3 projects amber, mostly from signals that disagree; " +
        "1 ticket merged but still open in Jira.",
    );
  });

  test("on a tie with the named blockers, the other reason is the one given", () => {
    const tree: RollupTree = {
      status: "ready",
      nodes: [
        node("u-ana", "developer", "Ana Silva", "stale", [status("u-ana")]),
        node("u-cara", "developer", "Cara Diaz", "confirmed", [blocker("b-1", "u-cara", null)]),
        node("pod-1", "pod", "Data Pod", "confirmed", [blocker("b-1", "u-cara", null)]),
        node("pod-2", "pod", "Platform Pod", "stale", [status("u-ana")]),
      ],
    };

    const line = heroDetailLine(
      input({
        drift: [{ kind: "said_done_no_pr" }, { kind: "something_new" }],
        tree,
        tiles: { pods: tiles("pod", ["amber", "amber", "green"]), projects: [], workstreams: [] },
      }),
    );

    assert.equal(
      line,
      "1 open blocker (Cara Diaz); 2 of 3 pods amber, mostly from stale updates; " +
        "1 item marked done with no pull request; 1 other drift signal.",
    );
  });
});
