import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { FocusResponse, MyStatusResponse, StatusCorrectionRequest } from "../../api/schema";
import { usePods, usePrograms, useProjects } from "../../app/directory";
import { useReadOnly } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Greeting, Panel, RagBadge, RagDot, Row } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { actionError } from "../../lib/errors";
import { formatDay, formatTime } from "../../lib/format";
import { daysLabel, greetingWord, plural, sourceLine, todayEyebrow } from "../../lib/words";
import {
  blockerRows,
  buildCorrection,
  checkinHint,
  checkinNote,
  checkinProvenance,
  confirmCaption,
  isOwnWords,
  MAX_BLOCKER_TEXT,
  type BlockerRow,
} from "./checkin";
import { WaitingOnYou } from "./WaitingOnYou";

/** The day in the viewer's own calendar, for when the server has not said which day it reads as today. */
function localDay(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/**
 * A developer's day: the check-in to confirm or correct, what to work on next,
 * what others are waiting on, their tasks, and where their status rolls up.
 * Most of a developer's OpenProgram happens in chat; this is the console side.
 */
export function DeveloperToday() {
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rollup = useRollUp(focus.data?.developer_id);
  // The day the server reads as today (focus answers with it), so "earlier day"
  // is judged by the same calendar the pod board uses.
  const today = focus.data?.as_of ?? localDay();

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(rollup.programs.map((program) => program.name))}
        title={`${greetingWord()}, Developer`}
        sub="Confirm today's check-in and clear what's blocking you."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard
            status={status.data}
            isLoading={status.isLoading}
            error={status.error}
            today={today}
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
                {(focus.data?.focus ?? []).map((item, i) => (
                  <Row
                    key={`${item.kind}-${item.source_ref.id}-${i}`}
                    rag={item.kind === "blocker" ? "red" : null}
                    title={item.label}
                    meta={focusMeta(item, focus.data)}
                    right={item.kind.toUpperCase()}
                  />
                ))}
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
            <p className="text-[14px] text-grey-body">
              Your check-in feeds every rollup above you: pod, project and program. Silence is never
              read as green: an unconfirmed status stays visible as stale until you confirm or
              correct it, so leaders see what is real.
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}

/**
 * What a focus item says under it. A blocker names its work item and how long
 * it has stood (the status source it carries is the owner's, not worth saying);
 * a task its deadline, else where its status came from.
 */
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
  podNames,
}: {
  status: MyStatusResponse | undefined;
  isLoading: boolean;
  error: unknown;
  today: string;
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
  // Only a configured member has a check-in: anyone else is told, not shown a failure.
  const noRecord = error instanceof ApiError && error.status === 404;

  const provenance = status
    ? checkinProvenance({
        source: status.source,
        statusAsOf: status.status_as_of,
        today,
        developerConfirmed: status.developer_confirmed,
      })
    : null;
  const note =
    status && provenance
      ? checkinNote(
          provenance,
          {
            source: status.source,
            confirmedAt: status.confirmed_at,
            developerConfirmed: status.developer_confirmed,
          },
          { day: formatDay, time: formatTime },
        )
      : undefined;
  const hint = provenance ? checkinHint(provenance.state, podNames) : null;
  const caption = provenance ? confirmCaption(provenance, formatDay) : null;

  return (
    <Panel title="Your check-in" variant="grey" note={note}>
      <PanelState
        needs="anyone with a member record"
        isLoading={isLoading}
        error={noRecord ? null : error}
        isEmpty={noRecord}
        emptyText="No check-in is on record for you. Only members are asked to check in: an admin adds you under Admin → Entities."
      >
        {status && provenance ? (
          <div className="grid gap-3">
            <p
              className={
                isOwnWords(status.source) ? "text-[16px] text-ink" : "text-[16px] text-grey-body"
              }
            >
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
              {provenance.confirmedToday ? (
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
            {caption && !provenance.confirmedToday ? (
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
