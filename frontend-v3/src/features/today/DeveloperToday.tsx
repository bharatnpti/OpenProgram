import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { MyStatusResponse, StatusCorrectionRequest } from "../../api/schema";
import { usePods, useProgram, usePrograms, useProjects } from "../../app/directory";
import { PanelState } from "../../components/PanelState";
import { Greeting, Panel, RagBadge, RagDot, Row } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay, formatTime } from "../../lib/format";
import { greetingWord, sourceLine, todayEyebrow } from "../../lib/words";
import { WaitingOnYou } from "./WaitingOnYou";

const SOURCE_SAID: Record<MyStatusResponse["source"], string> = {
  confirmed: "answered",
  partial: "partly answered",
  inferred: "inferred from delivery signals",
  stale: "stale: not confirmed today",
  unknown: "no check-in yet today",
};

/**
 * A developer's day: the check-in to confirm or correct, what to work on next,
 * what others are waiting on, their tasks, and where their status rolls up.
 * Most of a developer's OpenProgram happens in chat; this is the console side.
 */
export function DeveloperToday() {
  const { program } = useProgram();
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(program?.name)}
        title={`${greetingWord()}, Developer`}
        sub="Confirm today's check-in and clear what's blocking you."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard status={status.data} isLoading={status.isLoading} error={status.error} />
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
                    meta={
                      item.deadline
                        ? `due ${formatDay(item.deadline)}`
                        : sourceLine(item.source, item.confidence)
                    }
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
          <RollsUpInto developerId={focus.data?.developer_id} />
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

function CheckinCard({
  status,
  isLoading,
  error,
}: {
  status: MyStatusResponse | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["me"] });
  const confirm = useMutation({
    mutationFn: () => apiClient.confirmMyStatus(),
    onSuccess: () => {
      toast.success("Check-in confirmed.");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Panel
      title="Your check-in"
      variant="grey"
      note={
        status
          ? `${SOURCE_SAID[status.source]}${status.confirmed_at ? ` · ${formatTime(status.confirmed_at)}` : ""}`
          : undefined
      }
    >
      <PanelState needs="anyone with a member record" isLoading={isLoading} error={error}>
        {status ? (
          <div className="grid gap-3">
            <p className="text-[16px] text-ink">
              {status.summary || "Nothing reported yet today."}
            </p>
            {status.eta_change_days ? (
              <p className="text-[13px] text-grey-body">
                ETA {status.eta_change_days > 0 ? "slips" : "moves in"} by{" "}
                {Math.abs(status.eta_change_days)} days
              </p>
            ) : null}
            {(status.blocker_details ?? []).length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {(status.blocker_details ?? []).map((blocker) => (
                  <RagChip key={blocker.blocker_id} tone="danger" dot>
                    {blocker.description}
                    {blocker.work_item_id ? ` · ${blocker.work_item_id}` : ""} · {blocker.age_days}d
                  </RagChip>
                ))}
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2 pt-1">
              {status.developer_confirmed ? (
                <span className="inline-flex h-11 items-center rounded-full bg-rag-green-bg px-5 text-[15px] font-bold text-rag-green">
                  ✓ Check-in confirmed
                </span>
              ) : (
                <Pill size="md" onClick={() => confirm.mutate()} disabled={confirm.isPending}>
                  {confirm.isPending ? "Confirming…" : "Confirm check-in"}
                </Pill>
              )}
              <CorrectDialog status={status} onDone={refresh} />
            </div>
          </div>
        ) : null}
      </PanelState>
    </Panel>
  );
}

/**
 * A correction is a full statement: summary, ETA change and every blocker,
 * each either still open or resolved, plus any new one.
 */
function CorrectDialog({ status, onDone }: { status: MyStatusResponse; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState(status.summary);
  const [eta, setEta] = useState(String(status.eta_change_days ?? ""));
  const [resolved, setResolved] = useState<string[]>([]);
  const [added, setAdded] = useState("");

  const save = useMutation({
    mutationFn: () => {
      const body: StatusCorrectionRequest = {
        summary: summary.trim(),
        eta_change_days: eta.trim() === "" ? null : Number(eta),
        blocker_items: [
          ...(status.blocker_details ?? []).map((b) => ({
            blocker_id: b.blocker_id,
            description: b.description,
            work_item_id: b.work_item_id,
            pod_id: b.pod_id,
            resolved: resolved.includes(b.blocker_id),
          })),
          ...(added.trim() ? [{ description: added.trim(), resolved: false }] : []),
        ],
      };
      return apiClient.correctMyStatus(body);
    },
    onSuccess: () => {
      toast.success("Check-in corrected.");
      setOpen(false);
      onDone();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSummary(status.summary);
          setEta(String(status.eta_change_days ?? ""));
          setResolved([]);
          setAdded("");
        }
      }}
    >
      <Dialog.Trigger asChild>
        <Pill variant="ghost" size="md">
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
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <div>
              <label
                htmlFor="cc-summary"
                className="mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary"
              >
                What you did and what's next
              </label>
              <textarea
                id="cc-summary"
                className="min-h-24 w-full rounded-2xl border border-grey-border p-3 text-[14px]"
                value={summary}
                required
                onChange={(e) => setSummary(e.target.value)}
              />
            </div>
            <div>
              <label
                htmlFor="cc-eta"
                className="mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary"
              >
                ETA change in days (negative if earlier)
              </label>
              <input
                id="cc-eta"
                type="number"
                className="h-10 w-32 rounded-xl border border-grey-border px-3 text-[14px]"
                value={eta}
                onChange={(e) => setEta(e.target.value)}
              />
            </div>
            {(status.blocker_details ?? []).length > 0 ? (
              <fieldset className="grid gap-2">
                <legend className="mb-1 text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
                  Blockers
                </legend>
                {(status.blocker_details ?? []).map((b) => (
                  <label key={b.blocker_id} className="flex items-start gap-2 text-[14px]">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={resolved.includes(b.blocker_id)}
                      onChange={(e) =>
                        setResolved((r) =>
                          e.target.checked
                            ? [...r, b.blocker_id]
                            : r.filter((id) => id !== b.blocker_id),
                        )
                      }
                    />
                    <span>
                      Resolved: {b.description}{" "}
                      <span className="text-grey-secondary">({b.age_days}d)</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ) : null}
            <div>
              <label
                htmlFor="cc-new"
                className="mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary"
              >
                New blocker (optional)
              </label>
              <input
                id="cc-new"
                className="h-10 w-full rounded-xl border border-grey-border px-3 text-[14px]"
                value={added}
                placeholder="Waiting on sandbox credentials"
                onChange={(e) => setAdded(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <Pill type="button" variant="ghost" size="sm">
                  Cancel
                </Pill>
              </Dialog.Close>
              <Pill type="submit" size="sm" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save correction"}
              </Pill>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Pods the developer is in, the projects those pods work on, and their programs. */
function RollsUpInto({ developerId }: { developerId: string | undefined }) {
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
  const chain = [...mine, ...myProjects, ...myPrograms];

  return (
    <Panel title="Where you roll up" note="your check-in feeds each of these">
      {chain.length === 0 ? (
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
