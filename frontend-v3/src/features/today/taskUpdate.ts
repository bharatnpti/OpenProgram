// What a developer's task list shows and what one task's Update sends. Pure, and
// only type imports plus modules by their .ts path, so `node --test` runs it as
// written. The rules are the ones `POST /me/tasks/{task_id}/update` holds
// (backend task_update_service), said before the round trip.
import type {
  BlockerDetailDto,
  FocusResponse,
  FocusTaskDto,
  TaskStatementDto,
  TaskTrackerResultDto,
  TaskUpdateRequest,
} from "../../api/schema";
import { pastDue } from "../../components/ui/dateStripWords.ts";
import { plural } from "../../lib/words.ts";
import { blockerKey } from "./checkin.ts";

/** The five states a person can give a task: the tracker write-back's canonical targets. */
export const TASK_STATES = ["todo", "in_progress", "in_review", "blocked", "done"] as const;
export type TaskState = (typeof TASK_STATES)[number];

export const STATE_WORDS: Record<TaskState, string> = {
  todo: "To do",
  in_progress: "In progress",
  in_review: "In review",
  blocked: "Blocked",
  done: "Done",
};

/** Backend limits (TaskUpdateRequest): a note or a blocker is at most 500 characters. */
export const MAX_TASK_TEXT = 500;

export type WriteBack = FocusResponse["write_back"];

/**
 * The state a tracker's own status name stands for, or null when it names none
 * of the five. Only a starting point for the status control: what the person
 * last said comes first.
 */
export function trackerState(trackerStatus: string | null | undefined): TaskState | null {
  const status = (trackerStatus ?? "").trim().toLowerCase();
  if (!status) return null;
  if (/^(done|closed|resolved|released|completed?|in production)$/.test(status)) return "done";
  if (/block|impeded|on hold/.test(status)) return "blocked";
  if (/review|\bqa\b|uat|test/.test(status)) return "in_review";
  if (/progress|doing|develop|started/.test(status)) return "in_progress";
  if (/^(to ?do|open|new|backlog|selected.*|ready.*|planned)$/.test(status)) return "todo";
  return null;
}

/** Where the status control starts: the person's last statement, else the tracker's status. */
export function startState(task: Pick<FocusTaskDto, "last_update" | "tracker_status">) {
  return task.last_update?.state ?? trackerState(task.tracker_status);
}

/** The order the list is in: blocked, past due, ETA after due, in progress, to do, then done. */
export type TaskGroup = "blocked" | "past_due" | "eta_late" | "in_progress" | "todo" | "done";
const GROUP_ORDER: TaskGroup[] = ["blocked", "past_due", "eta_late", "in_progress", "todo", "done"];

/** One row of the list: the task, its own open blockers, and where it sorts. */
export type TaskRow = {
  task: FocusTaskDto;
  blockers: BlockerDetailDto[];
  group: TaskGroup;
  /** Due before the day shown, and not done. */
  late: boolean;
  /** The person's ETA is later than the due date. */
  etaLate: boolean;
};

/** Whether the person's ETA for a task falls after its due date. */
export function etaAfterDue(task: Pick<FocusTaskDto, "my_eta" | "deadline">): boolean {
  return Boolean(
    task.my_eta && task.deadline && task.my_eta.slice(0, 10) > task.deadline.slice(0, 10),
  );
}

/**
 * Each task with its open blockers, sorted for someone deciding what to do
 * next. A task is blocked when a blocker of the person's is open on it, or they
 * said it is; done when they or the tracker say so, unless a blocker is still
 * open on it. Within a group the nearest due date comes first.
 */
export function taskRows(
  tasks: FocusTaskDto[],
  blockers: BlockerDetailDto[],
  shownDay: string | null,
): TaskRow[] {
  const rows = tasks.map((task) => {
    const own = blockers.filter(
      (blocker) =>
        task.blocker_ids.includes(blocker.blocker_id) || blocker.work_item_id === task.id,
    );
    const state = startState(task);
    const done = state === "done";
    const late = !done && pastDue(task.deadline, shownDay, task.tracker_status);
    const etaLate = !done && etaAfterDue(task);
    const group: TaskGroup =
      own.length > 0 || state === "blocked"
        ? "blocked"
        : done
          ? "done"
          : late
            ? "past_due"
            : etaLate
              ? "eta_late"
              : state === "in_progress" || state === "in_review"
                ? "in_progress"
                : "todo";
    return { task, blockers: own, group, late, etaLate };
  });
  return rows.sort(
    (a, b) =>
      GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) ||
      (a.task.deadline ?? "9999").localeCompare(b.task.deadline ?? "9999") ||
      a.task.name.localeCompare(b.task.name),
  );
}

/**
 * "Last said Wed 7 Oct in chat: in progress" or "… in OpenProgram: in review ·
 * MR !3 open": what the person last stated on the task, a state or a note.
 * `day` writes the statement's time as a day in the viewer's words.
 */
export function lastSaidLine(
  statement: TaskStatementDto | null | undefined,
  day: (iso: string) => string,
): string | null {
  const said = statementWords(statement);
  if (!statement || !said) return null;
  return `Last said ${day(statement.at)} ${viaWords(statement.via)}: ${said}`;
}

/** "in review · MR !3 open": a statement's state and note, or null when it has neither. */
export function statementWords(statement: TaskStatementDto | null | undefined): string | null {
  if (!statement) return null;
  const parts = [
    statement.state ? STATE_WORDS[statement.state].toLowerCase() : null,
    statement.note?.trim() || null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function viaWords(via: TaskStatementDto["via"]): string {
  return via === "chat" ? "in chat" : "in OpenProgram";
}

/** An issue key ("CHK-4") when the task's id is one; a seeded or hand-made task has none. */
export function issueKey(task: Pick<FocusTaskDto, "id">): string | null {
  return /^[A-Z][A-Z0-9]*-\d+$/.test(task.id) ? task.id : null;
}

/** What the Update form holds, as typed. */
export type TaskDraft = {
  /** The state picked, or null while none is (a task with no statement and no tracker status). */
  state: TaskState | null;
  /** An ISO day, or "" for no ETA. */
  eta: string;
  /** Ids of the task's open blockers ticked Resolved. */
  resolved: string[];
  /** A blocker to add, as typed. */
  added: string;
  note: string;
  /** The "Also move … in Jira" tick, read only while it is shown. */
  moveInTracker: boolean;
};

/** The form as it opens: the task's current state and ETA, nothing resolved or added. */
export function initialDraft(task: FocusTaskDto, writeBack: WriteBack): TaskDraft {
  return {
    state: startState(task),
    eta: task.my_eta ?? "",
    resolved: [],
    added: "",
    note: "",
    // Under auto_apply the person's update moves the issue anyway; under ask the tick is the yes.
    moveInTracker: writeBack === "auto",
  };
}

/**
 * Whether "Also move CHK-4 to In review in Jira" is offered: the person is the
 * synced issue's assignee, write-back is not off for them, and the state
 * changed. Otherwise it is not shown at all.
 */
export function offersTrackerMove(task: FocusTaskDto, writeBack: WriteBack, draft: TaskDraft) {
  return (
    task.can_move_in_tracker &&
    writeBack !== "off" &&
    draft.state !== null &&
    draft.state !== startState(task)
  );
}

/** "Also move CHK-4 to In review in Jira", or "this issue" for a task whose id is no key. */
export function trackerMoveLabel(task: FocusTaskDto, state: TaskState): string {
  return `Also move ${issueKey(task) ?? "this issue"} to ${STATE_WORDS[state]} in Jira`;
}

/** Whether the form differs from the task as it stands: Save stays off until it does. */
export function hasChanges(task: FocusTaskDto, draft: TaskDraft): boolean {
  return (
    (draft.state !== null && draft.state !== startState(task)) ||
    draft.eta !== (task.my_eta ?? "") ||
    draft.resolved.length > 0 ||
    draft.added.trim() !== "" ||
    draft.note.trim() !== ""
  );
}

/**
 * The request Save sends, only the fields that changed, or what to tell the
 * person is wrong first. `today` is the server's day (from /me/focus): an ETA
 * before it is refused, as the server refuses it.
 */
export function buildTaskUpdate(
  task: FocusTaskDto,
  draft: TaskDraft,
  context: { today: string; writeBack: WriteBack },
): { ok: true; body: TaskUpdateRequest } | { ok: false; message: string } {
  const body: TaskUpdateRequest = {};
  const stateChanged = draft.state !== null && draft.state !== startState(task);
  if (stateChanged) body.state = draft.state;

  if (draft.eta !== (task.my_eta ?? "")) {
    if (draft.eta && draft.eta < context.today) {
      return { ok: false, message: "The ETA can't be before today." };
    }
    // An emptied field clears the ETA; the server reads null as "no ETA now".
    body.eta = draft.eta || null;
  }

  const note = draft.note.trim();
  if (note.length > MAX_TASK_TEXT) {
    return { ok: false, message: `A note is at most ${MAX_TASK_TEXT} characters.` };
  }
  if (note) body.note = note;

  const resolved = draft.resolved.filter((id) => task.blocker_ids.includes(id));
  if (resolved.length > 0) body.resolve_blocker_ids = resolved;

  const added = draft.added.trim();
  if (added.length > MAX_TASK_TEXT) {
    return { ok: false, message: `A blocker is at most ${MAX_TASK_TEXT} characters.` };
  }
  if (added) body.add_blocker = added;

  // Said blocked now, or kept blocked while its last blocker is resolved: something must block it.
  const leftBlocked = draft.state === "blocked" && (stateChanged || resolved.length > 0);
  if (leftBlocked && !added && task.blocker_ids.every((id) => resolved.includes(id))) {
    return {
      ok: false,
      message: "Blocked needs a blocker: add one, or keep one of the task's open blockers.",
    };
  }

  if (Object.keys(body).length === 0) {
    return {
      ok: false,
      message:
        "Nothing to save yet: change the status or the ETA, resolve or add a blocker, or add a note.",
    };
  }
  if (offersTrackerMove(task, context.writeBack, draft) && draft.moveInTracker) {
    body.move_in_tracker = true;
  }
  return { ok: true, body };
}

/** A blocker to add that reads like one already open on the task (the server would keep that one). */
export function sameAsOpenBlocker(
  added: string,
  blockers: Pick<BlockerDetailDto, "blocker_id" | "description">[],
  resolved: string[],
): boolean {
  const key = blockerKey(added);
  return (
    key !== "" &&
    blockers.some(
      (blocker) =>
        !resolved.includes(blocker.blocker_id) && blockerKey(blocker.description) === key,
    )
  );
}

/**
 * The line a saved row says: what changed, then what the tracker did. "Saved:
 * in review · ETA Fri 9 Oct · 1 blocker resolved. Moved to In Review in Jira."
 * The tracker's words are the server's (`detail`), which name the tracker it
 * reached. `day` writes an ISO day in the viewer's words.
 */
export function savedLine(
  body: TaskUpdateRequest,
  tracker: TaskTrackerResultDto | null | undefined,
  day: (iso: string) => string,
): { text: string; tone: "success" | "warning" } {
  const parts = [
    body.state ? STATE_WORDS[body.state].toLowerCase() : null,
    body.eta === undefined ? null : body.eta === null ? "ETA cleared" : `ETA ${day(body.eta)}`,
    body.resolve_blocker_ids?.length
      ? `${plural(body.resolve_blocker_ids.length, "blocker", "blockers")} resolved`
      : null,
    body.add_blocker ? "blocker added" : null,
    body.note ? "note added" : null,
  ].filter((part): part is string => part !== null);
  const saved = `Saved: ${parts.join(" · ")}.`;
  if (!tracker) return { text: saved, tone: "success" };
  const moved = tracker.outcome === "applied" || tracker.outcome === "no_change";
  return { text: `${saved} ${tracker.detail}`, tone: moved ? "success" : "warning" };
}

/** "4 done", for the collapsed tail of the list. */
export function doneCount(rows: TaskRow[]): number {
  return rows.filter((row) => row.group === "done").length;
}
