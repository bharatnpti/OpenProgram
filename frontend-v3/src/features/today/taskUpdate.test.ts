import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { BlockerDetailDto, FocusTaskDto } from "../../api/schema";
import {
  buildTaskUpdate,
  doneCount,
  hasChanges,
  initialDraft,
  issueKey,
  lastSaidLine,
  MAX_TASK_TEXT,
  offersTrackerMove,
  sameAsOpenBlocker,
  savedLine,
  startState,
  statementWords,
  taskRows,
  trackerMoveLabel,
  trackerState,
  type TaskDraft,
} from "./taskUpdate.ts";

const TODAY = "2026-10-08";
const day = (iso: string) => `day(${iso.slice(0, 10)})`;

function task(id: string, extra: Partial<FocusTaskDto> = {}): FocusTaskDto {
  return {
    id,
    name: `Work on ${id}`,
    rag: "unknown",
    source: "unknown",
    confidence: null,
    deadline: null,
    tracker_status: "In Progress",
    my_eta: null,
    my_eta_label: null,
    last_update: null,
    blocker_ids: [],
    can_move_in_tracker: false,
    ...extra,
  };
}

function blocker(id: string, workItem: string, description = `Blocker ${id}`): BlockerDetailDto {
  return {
    blocker_id: id,
    description,
    work_item_id: workItem,
    work_item_name: null,
    pod_id: null,
    unattributed: false,
    first_seen_on: "2026-10-01",
    age_days: 7,
  };
}

const said = (statement: NonNullable<FocusTaskDto["last_update"]>) => statement;

describe("where a task starts and sorts", () => {
  test("a tracker's own status names a state, or none", () => {
    assert.equal(trackerState("In Progress"), "in_progress");
    assert.equal(trackerState("In Review"), "in_review");
    assert.equal(trackerState("UAT"), "in_review");
    assert.equal(trackerState("Done"), "done");
    assert.equal(trackerState("Blocked"), "blocked");
    assert.equal(trackerState("To Do"), "todo");
    assert.equal(trackerState("Backlog"), "todo");
    assert.equal(trackerState("Waiting for spec"), null);
    assert.equal(trackerState(null), null);
  });

  test("the person's last statement comes before the tracker's status", () => {
    const stated = task("CHK-4", {
      last_update: { state: "in_review", note: null, at: `${TODAY}T09:00:00Z`, via: "chat" },
    });
    assert.equal(startState(stated), "in_review");
    assert.equal(startState(task("CHK-5", { tracker_status: "To Do" })), "todo");
    // A note alone states no state: the tracker's stands.
    const noted = task("CHK-6", {
      last_update: { state: null, note: "MR open", at: `${TODAY}T09:00:00Z`, via: "console" },
    });
    assert.equal(startState(noted), "in_progress");
  });

  test("blocked, past due, ETA after due, in progress, to do, then done", () => {
    const rows = taskRows(
      [
        task("CHK-9", { tracker_status: "Done" }),
        task("CHK-8", { tracker_status: "To Do" }),
        task("CHK-7", { tracker_status: "In Progress" }),
        task("CHK-6", { deadline: "2026-10-16", my_eta: "2026-10-19" }),
        task("CHK-5", { deadline: "2026-10-05" }),
        task("CHK-4", { blocker_ids: ["b1"] }),
      ],
      [blocker("b1", "CHK-4"), blocker("b-other", "CHK-77")],
      TODAY,
    );
    assert.deepEqual(
      rows.map((row) => [row.task.id, row.group]),
      [
        ["CHK-4", "blocked"],
        ["CHK-5", "past_due"],
        ["CHK-6", "eta_late"],
        ["CHK-7", "in_progress"],
        ["CHK-8", "todo"],
        ["CHK-9", "done"],
      ],
    );
    // Each row carries only its own blockers.
    assert.deepEqual(
      rows[0].blockers.map((b) => b.blocker_id),
      ["b1"],
    );
    assert.equal(rows[1].late, true);
    assert.equal(rows[2].etaLate, true);
    assert.equal(doneCount(rows), 1);
  });

  test("said blocked is blocked, said done folds away, and done is never late", () => {
    const rows = taskRows(
      [
        task("CHK-1", {
          deadline: "2026-10-01",
          last_update: { state: "done", note: null, at: `${TODAY}T08:00:00Z`, via: "console" },
        }),
        task("CHK-2", {
          last_update: { state: "blocked", note: null, at: `${TODAY}T08:00:00Z`, via: "chat" },
        }),
      ],
      [],
      TODAY,
    );
    assert.deepEqual(
      rows.map((row) => [row.task.id, row.group, row.late]),
      [
        ["CHK-2", "blocked", false],
        ["CHK-1", "done", false],
      ],
    );
  });

  test("within a group the nearest due date comes first", () => {
    const rows = taskRows(
      [
        task("CHK-2", { deadline: "2026-10-20" }),
        task("CHK-3"),
        task("CHK-1", { deadline: "2026-10-12" }),
      ],
      [],
      TODAY,
    );
    assert.deepEqual(
      rows.map((row) => row.task.id),
      ["CHK-1", "CHK-2", "CHK-3"],
    );
  });
});

describe("what the row says", () => {
  test("the last statement, where it was made", () => {
    const chat = said({
      state: "in_progress",
      note: null,
      at: "2026-10-07T09:00:00Z",
      via: "chat",
    });
    assert.equal(lastSaidLine(chat, day), "Last said day(2026-10-07) in chat: in progress");
    const typed = said({
      state: "in_review",
      note: " MR !3 open ",
      at: `${TODAY}T10:00:00Z`,
      via: "console",
    });
    assert.equal(
      lastSaidLine(typed, day),
      "Last said day(2026-10-08) in OpenProgram: in review · MR !3 open",
    );
    assert.equal(lastSaidLine(null, day), null);
    assert.equal(statementWords({ state: null, note: "  ", at: TODAY, via: "chat" }), null);
  });

  test("an issue key only where the id is one", () => {
    assert.equal(issueKey(task("CHK-104")), "CHK-104");
    assert.equal(issueKey(task("task-chk-103")), null);
  });
});

describe("what Update sends", () => {
  const ctx = { today: TODAY, writeBack: "ask" as const };
  const open = (t: FocusTaskDto, patch: Partial<TaskDraft> = {}) => ({
    ...initialDraft(t, ctx.writeBack),
    ...patch,
  });

  test("the form opens at the task's state and ETA, with nothing changed", () => {
    const t = task("CHK-4", { my_eta: "2026-10-12" });
    const draft = open(t);
    assert.deepEqual(draft, {
      state: "in_progress",
      eta: "2026-10-12",
      resolved: [],
      added: "",
      note: "",
      moveInTracker: false,
    });
    assert.equal(hasChanges(t, draft), false);
    assert.deepEqual(buildTaskUpdate(t, draft, ctx), {
      ok: false,
      message:
        "Nothing to save yet: change the status or the ETA, resolve or add a blocker, or add a note.",
    });
  });

  test("only the fields that changed are sent", () => {
    const t = task("CHK-4", { my_eta: "2026-10-12" });
    assert.deepEqual(buildTaskUpdate(t, open(t, { state: "in_review" }), ctx), {
      ok: true,
      body: { state: "in_review" },
    });
    assert.deepEqual(buildTaskUpdate(t, open(t, { eta: "2026-10-14", note: "  MR !3  " }), ctx), {
      ok: true,
      body: { eta: "2026-10-14", note: "MR !3" },
    });
  });

  test("an emptied ETA clears it, and one before today is refused", () => {
    const t = task("CHK-4", { my_eta: "2026-10-12" });
    assert.deepEqual(buildTaskUpdate(t, open(t, { eta: "" }), ctx), {
      ok: true,
      body: { eta: null },
    });
    assert.deepEqual(buildTaskUpdate(t, open(t, { eta: "2026-10-07" }), ctx), {
      ok: false,
      message: "The ETA can't be before today.",
    });
    assert.ok(buildTaskUpdate(t, open(t, { eta: TODAY }), ctx).ok);
  });

  test("blockers: resolve by id, add one, at most 500 characters", () => {
    const t = task("CHK-4", { blocker_ids: ["b1"] });
    assert.deepEqual(
      buildTaskUpdate(
        t,
        open(t, { resolved: ["b1", "not-this-task"], added: " Staging down " }),
        ctx,
      ),
      { ok: true, body: { resolve_blocker_ids: ["b1"], add_blocker: "Staging down" } },
    );
    assert.deepEqual(buildTaskUpdate(t, open(t, { added: "x".repeat(MAX_TASK_TEXT + 1) }), ctx), {
      ok: false,
      message: `A blocker is at most ${MAX_TASK_TEXT} characters.`,
    });
    assert.deepEqual(buildTaskUpdate(t, open(t, { note: "x".repeat(MAX_TASK_TEXT + 1) }), ctx), {
      ok: false,
      message: `A note is at most ${MAX_TASK_TEXT} characters.`,
    });
  });

  test("Blocked needs a blocker: an open one kept, or one added", () => {
    const refused = {
      ok: false,
      message: "Blocked needs a blocker: add one, or keep one of the task's open blockers.",
    };
    const bare = task("CHK-4");
    assert.deepEqual(buildTaskUpdate(bare, open(bare, { state: "blocked" }), ctx), refused);
    assert.deepEqual(
      buildTaskUpdate(bare, open(bare, { state: "blocked", added: "Waiting on finance" }), ctx),
      { ok: true, body: { state: "blocked", add_blocker: "Waiting on finance" } },
    );
    const held = task("CHK-5", { blocker_ids: ["b1"] });
    assert.ok(buildTaskUpdate(held, open(held, { state: "blocked" }), ctx).ok);
    assert.deepEqual(
      buildTaskUpdate(held, open(held, { state: "blocked", resolved: ["b1"] }), ctx),
      refused,
    );
    // Said blocked before, and its last blocker resolved now: something must still block it.
    const stated = task("CHK-6", {
      blocker_ids: ["b2"],
      last_update: { state: "blocked", note: null, at: `${TODAY}T08:00:00Z`, via: "console" },
    });
    assert.deepEqual(buildTaskUpdate(stated, open(stated, { resolved: ["b2"] }), ctx), refused);
    assert.ok(buildTaskUpdate(stated, open(stated, { note: "chased finance" }), ctx).ok);
  });

  test("a blocker worded like one still open on the task is the same blocker", () => {
    const blockers = [blocker("b1", "CHK-4", "Waiting on sandbox credentials")];
    assert.equal(sameAsOpenBlocker("  waiting on Sandbox credentials. ", blockers, []), true);
    assert.equal(sameAsOpenBlocker("waiting on sandbox credentials", blockers, ["b1"]), false);
    assert.equal(sameAsOpenBlocker("", blockers, []), false);
  });
});

describe("moving the Jira issue", () => {
  const mine = task("CHK-4", { can_move_in_tracker: true });

  test("offered only to the assignee, with write-back on, when the state changed", () => {
    const changed = { ...initialDraft(mine, "ask"), state: "in_review" as const };
    assert.equal(offersTrackerMove(mine, "ask", changed), true);
    assert.equal(offersTrackerMove(mine, "off", changed), false);
    assert.equal(offersTrackerMove(task("CHK-5"), "auto", changed), false);
    assert.equal(offersTrackerMove(mine, "auto", initialDraft(mine, "auto")), false);
    assert.equal(trackerMoveLabel(mine, "in_review"), "Also move CHK-4 to In review in Jira");
    assert.equal(
      trackerMoveLabel(task("task-chk-103", { can_move_in_tracker: true }), "done"),
      "Also move this issue to Done in Jira",
    );
  });

  test("ticked by default under auto, unticked under ask; sent only while offered", () => {
    assert.equal(initialDraft(mine, "auto").moveInTracker, true);
    assert.equal(initialDraft(mine, "ask").moveInTracker, false);
    const auto = { ...initialDraft(mine, "auto"), state: "done" as const };
    assert.deepEqual(buildTaskUpdate(mine, auto, { today: TODAY, writeBack: "auto" }), {
      ok: true,
      body: { state: "done", move_in_tracker: true },
    });
    const ask = { ...initialDraft(mine, "ask"), state: "done" as const };
    assert.deepEqual(buildTaskUpdate(mine, ask, { today: TODAY, writeBack: "ask" }), {
      ok: true,
      body: { state: "done" },
    });
    // A tick left on from an earlier state change is not sent once the state is back.
    const back = { ...initialDraft(mine, "auto"), eta: "2026-10-12" };
    assert.deepEqual(buildTaskUpdate(mine, back, { today: TODAY, writeBack: "auto" }), {
      ok: true,
      body: { eta: "2026-10-12" },
    });
  });
});

describe("the line a saved row says", () => {
  test("what changed, then the tracker's own words", () => {
    assert.deepEqual(
      savedLine(
        { state: "in_review", eta: "2026-10-09", resolve_blocker_ids: ["b1"], note: "MR" },
        { outcome: "applied", detail: "Moved to In Review in Jira.", merge_requests: [] },
        day,
      ),
      {
        text: "Saved: in review · ETA day(2026-10-09) · 1 blocker resolved · note added. Moved to In Review in Jira.",
        tone: "success",
      },
    );
    assert.deepEqual(
      savedLine(
        { state: "done", move_in_tracker: true },
        {
          outcome: "held_open_mr",
          detail: "Not moved in Jira: insights-pipeline !1 is still open.",
          merge_requests: ["insights-pipeline !1"],
        },
        day,
      ),
      {
        text: "Saved: done. Not moved in Jira: insights-pipeline !1 is still open.",
        tone: "warning",
      },
    );
    assert.deepEqual(savedLine({ eta: null, add_blocker: "x" }, null, day), {
      text: "Saved: ETA cleared · blocker added.",
      tone: "success",
    });
  });
});
