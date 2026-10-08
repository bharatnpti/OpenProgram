import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { MyStatusResponse, StatusCorrectionRequest } from "../../api/schema";
import { useDayWords, useReadOnly } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { BlockerChip, Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { actionError } from "../../lib/errors";
import { formatDay, formatTime } from "../../lib/format";
import { cn } from "../../lib/utils";
import { plural } from "../../lib/words";
import {
  blockerRows,
  buildCorrection,
  checkinHint,
  checkinNote,
  checkinProvenance,
  confirmCaption,
  isOwnWords,
  MAX_BLOCKER_TEXT,
  noStatusWords,
  type BlockerRow,
} from "./checkin";

/**
 * The person's own check-in, to confirm or correct, on every Today: anyone with
 * a member record is asked in chat, and the scrum master, product owner,
 * manager and executive could not confirm or correct theirs in the console.
 * The developer's Today shows it in full (with the advice and the caption under
 * the buttons); the others show it `compact`, and leave it out for someone with
 * no member record.
 *
 * It reads `/me/status`, and the server's day from `/me/focus`, the same two
 * reads (and query keys) the developer's Today uses, so nothing is asked twice.
 */
export function CheckinCard({
  compact = false,
  podNames = [],
  shownOnTasks = NONE,
}: {
  compact?: boolean;
  /** The person's pods, which the advice under a missing reply names. */
  podNames?: string[];
  /** Tasks listed with their own blockers on the page: the card leaves those blockers out. */
  shownOnTasks?: Set<string>;
}) {
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  // The day the server reads (focus answers with it), so "earlier day" is
  // judged by the same calendar the pod board uses. Never the browser's day:
  // it can be a day off the server's.
  return (
    <CheckinPanel
      status={status.data}
      isLoading={status.isLoading}
      error={status.error}
      today={focus.data?.as_of ?? null}
      dayUnknown={focus.isError}
      podNames={podNames}
      shownOnTasks={shownOnTasks}
      compact={compact}
    />
  );
}

const NONE = new Set<string>();

function CheckinPanel({
  status,
  isLoading,
  error,
  today,
  dayUnknown,
  podNames,
  shownOnTasks,
  compact,
}: {
  status: MyStatusResponse | undefined;
  isLoading: boolean;
  error: unknown;
  /** The day the server reads, or null until /me/focus has said it. */
  today: string | null;
  /** /me/focus failed, so the status cannot be judged against the server's day. */
  dayUnknown: boolean;
  podNames: string[];
  /** Tasks listed with their own blockers below: the card leaves those blockers out. */
  shownOnTasks: Set<string>;
  compact: boolean;
}) {
  const ownBlockers = (status?.blocker_details ?? []).filter(
    (blocker) => !blocker.work_item_id || !shownOnTasks.has(blocker.work_item_id),
  );
  const queryClient = useQueryClient();
  const { readOnly, reason } = useReadOnly();
  const dayWords = useDayWords();
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
  // with no member record, and its detail says which: only the second is told to
  // ask an admin.
  const noStatus = error instanceof ApiError && error.status === 404;
  // Anyone with a member record is asked in chat; someone with none has no check-in,
  // and a Today that is not about their own work leaves the card out.
  const noMember = noStatus && /no member record/i.test((error as ApiError).message);

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
              summary: status.summary,
            },
            { day: formatDay, time: formatTime, today: dayWords },
          )
        : status.status_as_of
          ? `status from ${formatDay(status.status_as_of)}`
          : "no check-in on record"
      : undefined;
  // Both are advice for today: with a past day shown the buttons beside them are off.
  const hint =
    shown && judged && status
      ? checkinHint({
          state: shown.state,
          source: status.source,
          statusAsOf: status.status_as_of,
          summary: status.summary,
          today: today ?? "",
          podNames,
          pastDay: readOnly,
        })
      : null;
  const caption =
    shown && judged
      ? confirmCaption(
          shown,
          formatDay,
          readOnly,
          status?.source === "confirmed" && !status?.developer_confirmed,
        )
      : null;

  if (compact && noMember) return null;
  const size = compact ? "sm" : "md";

  return (
    <Panel title="Your check-in" variant="grey" note={note}>
      <PanelState
        isLoading={isLoading || (status !== undefined && !judged && !dayUnknown)}
        error={noStatus ? null : error}
        isEmpty={noStatus}
        emptyText={noStatusWords(noStatus ? (error as ApiError).message : null)}
      >
        {status && shown ? (
          <div className="grid gap-3">
            <p
              className={cn(
                compact ? "text-[14px]" : "text-[16px]",
                isOwnWords(status.source) ? "text-ink" : "text-grey-body",
              )}
            >
              {/* An earlier day's words carry their day, so "after a nudge" is not read as today's. */}
              {judged && shown.kind === "carried" && shown.from && status.summary ? (
                <span className="font-bold text-grey-secondary">{formatDay(shown.from)}: </span>
              ) : null}
              {status.summary || `Nothing reported yet ${dayWords}.`}
            </p>
            {status.eta_change_days ? (
              <p className="text-[13px] text-grey-body">
                ETA {status.eta_change_days > 0 ? "slips" : "moves in"} by{" "}
                {plural(Math.abs(status.eta_change_days), "day", "days")}
              </p>
            ) : null}
            {ownBlockers.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {ownBlockers.map((blocker) => (
                  <BlockerChip
                    key={blocker.blocker_id}
                    description={blocker.description}
                    detail={blocker.work_item_id}
                    ageDays={blocker.age_days}
                  />
                ))}
              </div>
            ) : null}
            {hint && !compact ? <p className="text-[13px] text-grey-body">{hint}</p> : null}
            <div className="flex flex-wrap gap-2 pt-1">
              {shown.confirmedToday ? (
                <span
                  className={cn(
                    "inline-flex items-center rounded-full bg-rag-green-bg font-bold text-rag-green",
                    compact ? "h-9 px-4 text-[13px]" : "h-11 px-5 text-[15px]",
                  )}
                >
                  ✓ Check-in confirmed
                </span>
              ) : (
                <Pill
                  size={size}
                  onClick={() => confirm.mutate()}
                  disabled={confirm.isPending || readOnly}
                  title={reason ?? undefined}
                >
                  {confirm.isPending ? "Confirming…" : "Confirm check-in"}
                </Pill>
              )}
              <CorrectDialog status={status} onDone={refresh} size={size} />
            </div>
            {caption && !shown.confirmedToday && !compact ? (
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
function CorrectDialog({
  status,
  onDone,
  size,
}: {
  status: MyStatusResponse;
  onDone: () => void;
  size: "sm" | "md";
}) {
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
        <Pill variant="ghost" size={size} disabled={readOnly} title={reason ?? undefined}>
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
