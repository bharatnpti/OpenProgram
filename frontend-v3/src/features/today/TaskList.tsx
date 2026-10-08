import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ApiError, apiClient } from "../../api/client";
import type { BlockerDetailDto, FocusTaskDto, TaskUpdateRequest } from "../../api/schema";
import { useReadOnly, useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { BlockerChip, DueDate, Panel, Row } from "../../components/ui/Bits";
import { DayInput } from "../../components/ui/DayInput";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { actionError } from "../../lib/errors";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import { plural } from "../../lib/words";
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
  STATE_WORDS,
  TASK_STATES,
  taskRows,
  trackerMoveLabel,
  type TaskDraft,
  type TaskRow,
  type WriteBack,
} from "./taskUpdate";

/**
 * The developer's tasks, one list: blocked first, then past due, then an ETA
 * later than the due date, then in progress and to do; done tasks fold away.
 * Each row says the tracker's status, the due date, the person's own ETA, the
 * task's open blockers and what they last said about it, and opens in place to
 * update that one task (status, ETA, blockers, a note, and the Jira issue when
 * they may move it). On a past day the rows are read-only.
 *
 * What a save did is said on its row until the page is left. A task saved as
 * done stays in the open list with that line, rather than folding away unseen.
 *
 * It reads `/me/focus` under the key the rest of Today uses, so nothing is asked twice.
 */
export function TaskList() {
  const shownDay = useShownDay();
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const [showDone, setShowDone] = useState(false);
  const [saved, setSaved] = useState<Record<string, Outcome>>({});
  const rows = taskRows(focus.data?.tasks ?? [], focus.data?.blocker_details ?? [], shownDay);
  const open = rows.filter((row) => row.group !== "done" || saved[row.task.id]);
  const folded = rows.filter((row) => row.group === "done" && !saved[row.task.id]);
  const done = doneCount(rows);
  const item = (row: TaskRow) => (
    <TaskItem
      key={row.task.id}
      row={row}
      writeBack={focus.data?.write_back ?? "off"}
      today={focus.data?.as_of ?? ""}
      outcome={saved[row.task.id] ?? null}
      onOutcome={(line) =>
        setSaved((now) => {
          const next = { ...now };
          if (line) next[row.task.id] = line;
          else delete next[row.task.id];
          return next;
        })
      }
    />
  );

  return (
    <Panel
      title="Your tasks"
      note={
        focus.data && rows.length > 0
          ? `${rows.length - done} open${done > 0 ? ` · ${done} done` : ""}`
          : undefined
      }
    >
      <PanelState
        isLoading={focus.isLoading}
        error={focus.error}
        onRetry={() => void focus.refetch()}
        isEmpty={rows.length === 0}
        emptyText="No tasks are assigned to you."
      >
        {open.length > 0 ? (
          <ul>{open.map(item)}</ul>
        ) : (
          <p className="py-2 text-[14px] text-grey-body">Every task of yours is done.</p>
        )}
        {folded.length > 0 ? (
          <>
            <button
              type="button"
              aria-expanded={showDone}
              onClick={() => setShowDone((on) => !on)}
              className="mt-2 text-[13px] font-bold text-grey-body underline-offset-2 hover:underline"
            >
              {showDone ? "Hide done tasks" : `${folded.length} done`}
            </button>
            {showDone ? <ul className="mt-1">{folded.map(item)}</ul> : null}
          </>
        ) : null}
      </PanelState>
    </Panel>
  );
}

type Outcome = { text: string; tone: "success" | "warning" };

function TaskItem({
  row,
  writeBack,
  today,
  outcome,
  onOutcome,
}: {
  row: TaskRow;
  writeBack: WriteBack;
  today: string;
  /** What the last save of this task did, said on the row. */
  outcome: Outcome | null;
  onOutcome: (line: Outcome | null) => void;
}) {
  const { task, blockers, group, late, etaLate } = row;
  const { readOnly, reason } = useReadOnly();
  const [editing, setEditing] = useState(false);
  const key = issueKey(task);
  const meta = [key, task.tracker_status].filter(Boolean).join(" · ");
  const said = lastSaidLine(task.last_update, formatDay);

  return (
    <Row
      title={task.name}
      meta={meta || undefined}
      accent={group === "blocked" ? "red" : late || etaLate ? "amber" : undefined}
      right={
        editing ? null : (
          <Pill
            variant="ghost"
            size="sm"
            disabled={readOnly}
            title={reason ?? undefined}
            aria-label={`Update ${task.name}`}
            onClick={() => {
              onOutcome(null);
              setEditing(true);
            }}
          >
            Update
          </Pill>
        )
      }
    >
      <p className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
        {task.deadline ? (
          <DueDate prefix="Due " deadline={task.deadline} trackerStatus={task.tracker_status} />
        ) : (
          <span className="text-grey-secondary">No due date</span>
        )}
        {task.my_eta ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="font-bold text-ink">Your ETA {formatDay(task.my_eta)}</span>
            {etaLate ? (
              <RagChip tone="warning" className="h-5 px-2 text-[11px]">
                ETA after due
              </RagChip>
            ) : null}
          </span>
        ) : null}
      </p>
      {blockers.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {blockers.map((blocker) => (
            <BlockerChip
              key={blocker.blocker_id}
              description={blocker.description}
              ageDays={blocker.age_days}
            />
          ))}
        </div>
      ) : null}
      {said ? <p className="mt-1 text-[12px] text-grey-secondary">{said}</p> : null}
      {outcome ? (
        <p
          role="status"
          className={cn(
            "mt-2 text-[13px] font-bold",
            outcome.tone === "success" ? "text-rag-green" : "text-rag-amber",
          )}
        >
          {outcome.text}
        </p>
      ) : null}
      {editing ? (
        <TaskEditor
          task={task}
          blockers={blockers}
          writeBack={writeBack}
          today={today}
          onCancel={() => setEditing(false)}
          onSaved={(line) => {
            onOutcome(line);
            setEditing(false);
          }}
        />
      ) : null}
    </Row>
  );
}

const LABEL = "mb-1.5 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

/** The in-place Update of one task: only what changed is sent. */
function TaskEditor({
  task,
  blockers,
  writeBack,
  today,
  onCancel,
  onSaved,
}: {
  task: FocusTaskDto;
  blockers: BlockerDetailDto[];
  writeBack: WriteBack;
  /** The server's day, from /me/focus: the earliest ETA it takes. */
  today: string;
  onCancel: () => void;
  onSaved: (line: Outcome) => void;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const { readOnly, reason } = useReadOnly();
  const [draft, setDraft] = useState<TaskDraft>(() => initialDraft(task, writeBack));
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<TaskDraft>) => setDraft((now) => ({ ...now, ...patch }));
  const changed = hasChanges(task, draft);
  const offerMove = offersTrackerMove(task, writeBack, draft);
  // Only the task's own blockers the server lists by id can be resolved here.
  const resolvable = blockers.filter((blocker) => task.blocker_ids.includes(blocker.blocker_id));

  const save = useMutation({
    mutationFn: (body: TaskUpdateRequest) => apiClient.updateMyTask(task.id, body),
    onSuccess: (response, body) => {
      onSaved(savedLine(body, response.tracker, formatDay));
      // The person's own reads, and every pod, project and date strip the update reaches.
      for (const key of ["me", "pod", "delivery", "pod-delivery"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (e: unknown) => {
      // A blocker resolved elsewhere (another tab, chat) since the form opened. The server names
      // it by id; say it in words, and reload the task, so a second Save sends only what is open.
      if (e instanceof ApiError && e.status === 422 && /^Not an open blocker/.test(e.message)) {
        setProblem(
          "That blocker was already resolved, so nothing was saved. Check the task and save again.",
        );
        void queryClient.invalidateQueries({ queryKey: ["me"] });
        return;
      }
      setProblem(actionError(e, "update this task"));
    },
  });

  return (
    <form
      className="mt-3 grid gap-4 rounded-2xl bg-grey-fill p-4"
      noValidate
      aria-label={`Update ${task.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (sameAsOpenBlocker(draft.added, blockers, draft.resolved)) {
          setProblem("That blocker is already open on this task.");
          return;
        }
        const built = buildTaskUpdate(task, draft, { today, writeBack });
        if (!built.ok) {
          setProblem(built.message);
          return;
        }
        setProblem(null);
        save.mutate(built.body);
      }}
    >
      <fieldset>
        <legend className={LABEL}>Status</legend>
        <div
          role="radiogroup"
          aria-label="Status"
          className="inline-flex flex-wrap gap-1 rounded-2xl border border-grey-border bg-white p-1 sm:rounded-full"
        >
          {TASK_STATES.map((state) => (
            <label
              key={state}
              className={cn(
                "inline-flex h-8 cursor-pointer items-center rounded-full px-3 text-[13px] font-bold",
                "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-magenta",
                draft.state === state ? "bg-ink text-white" : "text-grey-body hover:bg-grey-fill",
              )}
            >
              <input
                type="radio"
                name={`${id}-state`}
                value={state}
                className="sr-only"
                checked={draft.state === state}
                onChange={() => set({ state })}
              />
              {STATE_WORDS[state]}
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <label htmlFor={`${id}-eta`} className={LABEL}>
          Your ETA
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <div className="w-44">
            <DayInput
              id={`${id}-eta`}
              value={draft.eta}
              min={today || undefined}
              placeholder="No ETA"
              onChange={(eta) => set({ eta })}
              className="h-10 rounded-xl border border-grey-border bg-white px-3 text-[14px]"
            />
          </div>
          {draft.eta ? (
            <Pill type="button" variant="ghost" size="sm" onClick={() => set({ eta: "" })}>
              Clear
            </Pill>
          ) : null}
          {task.deadline ? (
            <span className="text-[13px] text-grey-body">
              Due {formatDay(task.deadline)}
              {task.tracker_status ? " (Jira)" : ""}
            </span>
          ) : null}
        </div>
      </div>

      <fieldset className="grid gap-2">
        <legend className={LABEL}>Blockers</legend>
        {resolvable.map((blocker) => (
          <label key={blocker.blocker_id} className="flex items-start gap-2 text-[14px]">
            <input
              type="checkbox"
              className="mt-1"
              checked={draft.resolved.includes(blocker.blocker_id)}
              onChange={(e) =>
                set({
                  resolved: e.target.checked
                    ? [...draft.resolved, blocker.blocker_id]
                    : draft.resolved.filter((key) => key !== blocker.blocker_id),
                })
              }
            />
            <span>
              Resolved: {blocker.description}
              <span className="text-grey-secondary">
                {" "}
                ({blocker.age_days > 0 ? plural(blocker.age_days, "day", "days") : "new"})
              </span>
            </span>
          </label>
        ))}
        <input
          aria-label="Add a blocker"
          className="h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px]"
          value={draft.added}
          maxLength={MAX_TASK_TEXT + 100}
          placeholder="Add a blocker, such as: waiting on sandbox credentials"
          onChange={(e) => set({ added: e.target.value })}
        />
      </fieldset>

      <div>
        <label htmlFor={`${id}-note`} className={LABEL}>
          Note (optional)
        </label>
        <textarea
          id={`${id}-note`}
          className="min-h-16 w-full rounded-2xl border border-grey-border bg-white p-3 text-[14px]"
          value={draft.note}
          maxLength={MAX_TASK_TEXT + 100}
          placeholder="MR !3 is open, waiting on a review"
          onChange={(e) => set({ note: e.target.value })}
        />
      </div>

      {offerMove && draft.state ? (
        <label className="flex items-start gap-2 text-[14px] font-bold">
          <input
            type="checkbox"
            className="mt-1"
            checked={draft.moveInTracker}
            onChange={(e) => set({ moveInTracker: e.target.checked })}
          />
          {trackerMoveLabel(task, draft.state)}
        </label>
      ) : null}

      {problem ? (
        <p role="alert" className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red">
          {problem}
        </p>
      ) : null}

      <div className="flex flex-wrap justify-end gap-2">
        <Pill type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Pill>
        <Pill
          type="submit"
          size="sm"
          disabled={!changed || save.isPending || readOnly}
          title={reason ?? undefined}
        >
          {save.isPending ? "Saving…" : "Save"}
        </Pill>
      </div>
    </form>
  );
}
