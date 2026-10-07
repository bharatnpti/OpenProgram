import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { FocusResponse, MyStatusResponse, StatusCorrectionRequest } from "../../api/schema";
import { usePods, usePrograms, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useReadOnly, useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Greeting, Panel, RagBadge, RagDot, Row } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { actionError } from "../../lib/errors";
import { formatDay, formatTime } from "../../lib/format";
import { daysLabel, greetingTitle, plural, sourceLine, todayEyebrow } from "../../lib/words";
import {
  blockerRows,
  buildCorrection,
  checkinHint,
  checkinNote,
  checkinProvenance,
  confirmCaption,
  isNoReplyPlaceholder,
  isOwnWords,
  MAX_BLOCKER_TEXT,
  whyCheckinMatters,
  type BlockerRow,
} from "./checkin";
import { useMyCheckinPreference } from "../checkin/useMyCheckinPreference";
import { WaitingOnYou } from "./WaitingOnYou";

/**
 * A developer's day: the check-in to confirm or correct, what to work on next,
 * what others are waiting on, their tasks, and where their status rolls up.
 * Most of a developer's OpenProgram happens in chat; this is the console side.
 */
export function DeveloperToday() {
  const shownDay = useShownDay();
  const { readOnly } = useReadOnly();
  const { roleLabel, greetingName } = useRole();
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rollup = useRollUp(focus.data?.developer_id);
  // The day the server reads (focus answers with it), so "earlier day" is
  // judged by the same calendar the pod board uses. Never the browser's day:
  // it can be a day off the server's.
  const today = focus.data?.as_of ?? null;

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          rollup.programs.map((program) => program.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Confirm today's check-in and clear what's blocking you."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard
            status={status.data}
            isLoading={status.isLoading}
            error={status.error}
            today={today}
            dayUnknown={focus.isError}
            podNames={rollup.pods.map((pod) => pod.name)}
          />
          <Panel title="Focus today" note="ranked by urgency">
            <PanelState
              needs="anyone with a member record"
              isLoading={focus.isLoading}
              error={focus.error}
              isEmpty={(focus.data?.focus ?? []).length === 0}
              emptyText="Nothing ranked for today."
            >
              <ul>
                {(focus.data?.focus ?? []).map((item, i) => {
                  const line = focusLine(item, focus.data);
                  return (
                    <Row
                      key={`${item.kind}-${item.source_ref.id}-${i}`}
                      rag={line.urgent ? "red" : null}
                      title={line.title}
                      meta={line.meta}
                      right={line.tag}
                    />
                  );
                })}
              </ul>
            </PanelState>
          </Panel>
          <Panel title="Your tasks" note="source-linked · silence is never green">
            <PanelState
              needs="anyone with a member record"
              isLoading={focus.isLoading}
              error={focus.error}
              isEmpty={(focus.data?.tasks ?? []).length === 0}
              emptyText="No tasks are assigned to you."
            >
              <ul>
                {(focus.data?.tasks ?? []).map((task) => (
                  <Row
                    key={task.id}
                    title={task.name}
                    meta={`${task.id} · ${sourceLine(task.source, task.confidence)}${task.deadline ? ` · due ${formatDay(task.deadline)}` : ""}`}
                    right={<RagBadge rag={task.rag} />}
                  />
                ))}
              </ul>
            </PanelState>
          </Panel>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <WaitingOnYou />
          <RollsUpInto rollup={rollup} loading={focus.isLoading} />
          <Panel title="Why this matters">
            <p className="text-[14px] text-grey-body">{whyCheckinMatters(readOnly)}</p>
          </Panel>
        </div>
      </div>
    </>
  );
}

/**
 * One Focus row. A blocker names its work item and how long it has stood (the
 * status source it carries is the owner's, not worth saying); a task its
 * deadline, else where its status came from. The "blocker" the backend puts
 * there when nobody answered a check-in is no blocker: it says the check-in
 * wants confirming, and is not red.
 */
function focusLine(item: FocusResponse["focus"][number], focus: FocusResponse | undefined) {
  if (item.kind === "blocker" && isNoReplyPlaceholder(item.label)) {
    return {
      title: "Confirm your check-in",
      meta: "no confirmed reply yet",
      tag: "CHECK-IN",
      urgent: false,
    };
  }
  return {
    title: item.label,
    meta: focusMeta(item, focus),
    tag: item.kind.toUpperCase(),
    urgent: item.kind === "blocker",
  };
}

function focusMeta(item: FocusResponse["focus"][number], focus: FocusResponse | undefined) {
  if (item.deadline) return `due ${formatDay(item.deadline)}`;
  if (item.kind === "blocker") {
    const detail = focus?.blocker_details.find((blocker) => blocker.description === item.label);
    if (detail) {
      const age = detail.age_days > 0 ? `open ${daysLabel(detail.age_days)}` : "raised today";
      return `${detail.work_item_id ?? "no work item"} · ${age}`;
    }
  }
  return sourceLine(item.source, item.confidence);
}

function CheckinCard({
  status,
  isLoading,
  error,
  today,
  dayUnknown,
  podNames,
}: {
  status: MyStatusResponse | undefined;
  isLoading: boolean;
  error: unknown;
  /** The day the server reads, or null until /me/focus has said it. */
  today: string | null;
  /** /me/focus failed, so the status cannot be judged against the server's day. */
  dayUnknown: boolean;
  podNames: string[];
}) {
  const queryClient = useQueryClient();
  const { readOnly, reason } = useReadOnly();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["me"] });
  const confirm = useMutation({
    mutationFn: () => apiClient.confirmMyStatus(),
    onSuccess: () => {
      toast.success("Check-in confirmed.");
      refresh();
    },
    onError: (e: unknown) => toast.error(actionError(e, "confirm this check-in")),
  });
  // The server answers 404 both for a member with no status yet and for someone
  // with no member record. Only the second is told to ask an admin, so the
  // member check (the own preference read, 404 only without a record) runs then.
  const noStatus = error instanceof ApiError && error.status === 404;
  const preference = useMyCheckinPreference(noStatus);
  const noMember = preference.error instanceof ApiError && preference.error.status === 404;

  const judged = today !== null;
  const provenance = status
    ? checkinProvenance({
        source: status.source,
        statusAsOf: status.status_as_of,
        // Without the server's day nothing is judged against today: the status
        // reads as of its own day, with no tick, carried-forward line or hint.
        today: today ?? status.status_as_of ?? "",
        developerConfirmed: status.developer_confirmed,
      })
    : null;
  const shown = provenance && !judged ? { ...provenance, confirmedToday: false } : provenance;
  const note =
    status && shown
      ? judged
        ? checkinNote(
            shown,
            {
              source: status.source,
              confirmedAt: status.confirmed_at,
              developerConfirmed: status.developer_confirmed,
            },
            { day: formatDay, time: formatTime },
          )
        : status.status_as_of
          ? `status from ${formatDay(status.status_as_of)}`
          : "no check-in on record"
      : undefined;
  // Both are advice for today: with a past day shown the buttons beside them are off.
  const hint = shown && judged ? checkinHint(shown.state, podNames, readOnly) : null;
  const caption = shown && judged ? confirmCaption(shown, formatDay, readOnly) : null;

  return (
    <Panel title="Your check-in" variant="grey" note={note}>
      <PanelState
        needs="anyone with a member record"
        isLoading={
          isLoading || (status !== undefined && !judged && !dayUnknown) || preference.isLoading
        }
        error={noStatus ? null : error}
        isEmpty={noStatus}
        emptyText={
          noMember
            ? "You have no member record yet, and only members are asked to check in. An admin adds you under Admin → Directory."
            : "No check-in yet. Your first one comes in chat at your check-in time."
        }
      >
        {status && shown ? (
          <div className="grid gap-3">
            <p
              className={
                isOwnWords(status.source) ? "text-[16px] text-ink" : "text-[16px] text-grey-body"
              }
            >
              {/* An earlier day's words carry their day, so "after a nudge" is not read as today's. */}
              {judged && shown.kind === "carried" && shown.from && status.summary ? (
                <span className="font-bold text-grey-secondary">{formatDay(shown.from)}: </span>
              ) : null}
              {status.summary || "Nothing reported yet today."}
            </p>
            {status.eta_change_days ? (
              <p className="text-[13px] text-grey-body">
                ETA {status.eta_change_days > 0 ? "slips" : "moves in"} by{" "}
                {plural(Math.abs(status.eta_change_days), "day", "days")}
              </p>
            ) : null}
            {(status.blocker_details ?? []).length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {(status.blocker_details ?? []).map((blocker) => (
                  <RagChip key={blocker.blocker_id} tone="danger" dot>
                    {blocker.description}
                    {blocker.work_item_id ? ` · ${blocker.work_item_id}` : ""} ·{" "}
                    {blocker.age_days > 0 ? `${blocker.age_days}d` : "new"}
                  </RagChip>
                ))}
              </div>
            ) : null}
            {hint ? <p className="text-[13px] text-grey-body">{hint}</p> : null}
            <div className="flex flex-wrap gap-2 pt-1">
              {shown.confirmedToday ? (
                <span className="inline-flex h-11 items-center rounded-full bg-rag-green-bg px-5 text-[15px] font-bold text-rag-green">
                  ✓ Check-in confirmed
                </span>
              ) : (
                <Pill
                  size="md"
                  onClick={() => confirm.mutate()}
                  disabled={confirm.isPending || readOnly}
                  title={reason ?? undefined}
                >
                  {confirm.isPending ? "Confirming…" : "Confirm check-in"}
                </Pill>
              )}
              <CorrectDialog status={status} onDone={refresh} />
            </div>
            {caption && !shown.confirmedToday ? (
              <p className="text-[12px] text-grey-secondary">{caption}</p>
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </Panel>
  );
}

const LABEL = "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

/**
 * A correction is a full statement: summary, ETA change and every blocker,
 * each either still open or resolved, plus any new one. A summary the system
 * wrote (nobody answered, or it was inferred) is not prefilled as if the
 * person had said it.
 */
function CorrectDialog({ status, onDone }: { status: MyStatusResponse; onDone: () => void }) {
  const { readOnly, reason } = useReadOnly();
  const rows = blockerRows(status);
  const ownWords = isOwnWords(status.source);
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState("");
  const [eta, setEta] = useState("");
  const [resolved, setResolved] = useState<string[]>([]);
  const [added, setAdded] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: StatusCorrectionRequest) => apiClient.correctMyStatus(body),
    onSuccess: () => {
      toast.success("Check-in corrected.");
      setOpen(false);
      onDone();
    },
    onError: (e: unknown) => setProblem(actionError(e, "save the correction")),
  });

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSummary(ownWords ? status.summary : "");
          setEta(status.eta_change_days == null ? "" : String(status.eta_change_days));
          setResolved([]);
          setAdded("");
          setProblem(null);
        }
      }}
    >
      <Dialog.Trigger asChild>
        <Pill variant="ghost" size="md" disabled={readOnly} title={reason ?? undefined}>
          Correct details
        </Pill>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop">
          <Dialog.Title className="text-[20px] font-extrabold">
            Correct today's check-in
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] text-grey-body">
            This replaces today's status. A blocker you mark resolved is closed.
          </Dialog.Description>
          <form
            className="mt-4 grid gap-4"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              const built = buildCorrection(rows, { summary, eta, resolved, added });
              if (!built.ok) {
                setProblem(built.message);
                return;
              }
              setProblem(null);
              save.mutate(built.body);
            }}
          >
            <div>
              <label htmlFor="cc-summary" className={LABEL}>
                What you did and what's next
              </label>
              <textarea
                id="cc-summary"
                className="min-h-24 w-full rounded-2xl border border-grey-border p-3 text-[14px]"
                value={summary}
                placeholder="In your own words"
                onChange={(e) => setSummary(e.target.value)}
              />
              {!ownWords && status.summary ? (
                <p className="mt-1 text-[12px] text-grey-secondary">
                  The text on the card was written by OpenProgram, not by you, so this starts empty.
                </p>
              ) : null}
            </div>
            <div>
              <label htmlFor="cc-eta" className={LABEL}>
                ETA change in days (negative if earlier)
              </label>
              <input
                id="cc-eta"
                type="number"
                step={1}
                className="h-10 w-32 rounded-xl border border-grey-border px-3 text-[14px]"
                value={eta}
                onChange={(e) => setEta(e.target.value)}
              />
            </div>
            {rows.length > 0 ? (
              <fieldset className="grid gap-2">
                <legend className={`${LABEL} mb-1`}>Blockers</legend>
                {rows.map((row) => (
                  <BlockerCheckbox
                    key={row.key}
                    row={row}
                    checked={resolved.includes(row.key)}
                    onChange={(on) =>
                      setResolved((r) =>
                        on ? [...r, row.key] : r.filter((key) => key !== row.key),
                      )
                    }
                  />
                ))}
              </fieldset>
            ) : null}
            <div>
              <label htmlFor="cc-new" className={LABEL}>
                New blocker (optional)
              </label>
              <input
                id="cc-new"
                className="h-10 w-full rounded-xl border border-grey-border px-3 text-[14px]"
                value={added}
                maxLength={MAX_BLOCKER_TEXT + 100}
                placeholder="Waiting on sandbox credentials"
                onChange={(e) => setAdded(e.target.value)}
              />
            </div>
            {problem ? (
              <p
                role="alert"
                className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
              >
                {problem}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <Pill type="button" variant="ghost" size="sm">
                  Cancel
                </Pill>
              </Dialog.Close>
              <Pill type="submit" size="sm" disabled={save.isPending || readOnly}>
                {save.isPending ? "Saving…" : "Save correction"}
              </Pill>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function BlockerCheckbox({
  row,
  checked,
  onChange,
}: {
  row: BlockerRow;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-2 text-[14px]">
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        Resolved: {row.description}
        {row.age_days === null ? null : (
          <span className="text-grey-secondary"> ({row.age_days}d)</span>
        )}
      </span>
    </label>
  );
}

/** Pods the developer is in, the projects those pods work on, and their programs. */
function useRollUp(developerId: string | undefined) {
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const mine = developerId
    ? (pods.data ?? []).filter((pod) => pod.member_ids.includes(developerId))
    : [];
  const projectIds = new Set(mine.flatMap((pod) => pod.project_ids));
  const myProjects = (projects.data ?? []).filter((p) => projectIds.has(p.id));
  const programIds = new Set(myProjects.flatMap((p) => p.program_ids));
  const myPrograms = (programs.data ?? []).filter((p) => programIds.has(p.id));
  return {
    pods: mine,
    projects: myProjects,
    programs: myPrograms,
    isLoading: pods.isLoading || projects.isLoading || programs.isLoading,
  };
}

function RollsUpInto({
  rollup,
  loading,
}: {
  rollup: ReturnType<typeof useRollUp>;
  loading: boolean;
}) {
  const chain = [...rollup.pods, ...rollup.projects, ...rollup.programs];
  return (
    <Panel title="Where you roll up" note="your check-in feeds each of these">
      {loading || rollup.isLoading ? (
        <p className="text-[14px] text-grey-body">Loading…</p>
      ) : chain.length === 0 ? (
        <p className="text-[14px] text-grey-body">
          You are in no pod yet, so your check-in counts toward no pod, project or program.
        </p>
      ) : (
        <ul className="mt-2 flex flex-wrap items-center gap-2">
          {chain.map((item) => (
            <li
              key={`${item.kind}-${item.id}`}
              className="inline-flex items-center gap-2 rounded-full border border-grey-border px-3 py-1.5 text-[13px] font-bold"
            >
              <RagDot rag={item.rag} />
              {item.name}
              <span className="text-[11px] font-medium text-grey-secondary">{item.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
