import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { ProjectProgressResponse } from "../../api/schema";
import { attentionNote, attentionTasks } from "./attentionTasks.ts";

type Task = ProjectProgressResponse["tasks"][number];

const task = (overrides: Partial<Task>): Task => ({
  id: "CHK-1",
  name: "A task",
  rag: "amber",
  source: "confirmed",
  confidence: null,
  deadline: null,
  tracker_status: null,
  ...overrides,
});

describe("needs your attention", () => {
  test("every task that is not green, worst first, then the nearest due date, then A to Z", () => {
    const tasks = [
      task({ id: "a", name: "Green", rag: "green" }),
      task({ id: "b", name: "Amber late", deadline: "2026-11-01" }),
      task({ id: "c", name: "Amber soon", deadline: "2026-10-20" }),
      task({ id: "d", name: "Quiet", rag: "unknown" }),
      task({ id: "e", name: "Blocked", rag: "red" }),
      task({ id: "f", name: "Amber undated" }),
    ];
    assert.deepEqual(
      attentionTasks(tasks).map((item) => item.id),
      ["e", "c", "b", "f", "d"],
    );
    assert.deepEqual(attentionTasks([task({ rag: "green" })]), []);
  });

  test("the note says how many are left out", () => {
    assert.equal(attentionNote(11), "worst 8 of 11 tasks");
    assert.equal(attentionNote(3), "3 tasks");
    assert.equal(attentionNote(1), "1 task");
  });
});
