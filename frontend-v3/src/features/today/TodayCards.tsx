import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError, apiClient } from "../../api/client";
import type { PortfolioWarningsResponse, TodayCountsResponse } from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { actionError } from "../../lib/errors";
import { plural } from "../../lib/words";
import { deviceTimezone } from "../../lib/zones";
import { isOff, SEND_OFF, sendWords } from "../checkin/schedule";
import { isNoCheckin, useMyCheckinPreference } from "../checkin/useMyCheckinPreference";
import { VizCard } from "../overall/viz";
import { checkinProvenance } from "./checkin";
import { useMyJiraChanges } from "./queries";
import { checkinStateWords, openWarningsLine, repliesWords } from "./todayWords";

/** A count card: a figure, what it counts, one more line, and where to go next. */
function CountCard({
  title,
  figure,
  lines,
  link,
  label,
  isLoading,
  error,
  flag = null,
}: {
  title: string;
  figure: string | null;
  lines: (string | null)[];
  link?: { to: string; text: string } | null;
  label: string;
  isLoading: boolean;
  error: unknown;
  /** Something that asks for a look, under the lines: a scrum master's follow-up flag. */
  flag?: ReactNode;
}) {
  return (
    <VizCard aria-label={label} className="content-start gap-2">
      <h2 className="text-[13px] font-bold uppercase tracking-wider text-grey-secondary">
        {title}
      </h2>
      <PanelState isLoading={isLoading} error={error}>
        {figure ? <p className="text-[32px] font-extrabold leading-none">{figure}</p> : null}
        {lines
          .filter((line): line is string => Boolean(line))
          .map((line) => (
            <p key={line} className="text-[13px] text-grey-body">
              {line}
            </p>
          ))}
        {flag}
        {link ? (
          <Link
            to={link.to}
            className="mt-1 inline-flex min-h-11 items-center text-[13px] font-bold sm:min-h-0"
          >
            {link.text} →
          </Link>
        ) : null}
      </PanelState>
    </VizCard>
  );
}

/**
 * The person's own check-in, as an action card: whether they replied today (or
 * the day's check-in waits for their yes on a Jira change), when Ora asks,
 * Confirm when nothing changed since the status on record, and the way to the
 * Check-in page, where it is corrected. Not drawn for someone with no member
 * record, who is never asked.
 */
export function CheckinAction({ tasks = null }: { tasks?: number | null }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const preference = useMyCheckinPreference(true);
  const changes = useMyJiraChanges();
  const { readOnly, reason } = useReadOnly();
  const confirm = useMutation({
    mutationFn: () => apiClient.confirmMyStatus(),
    onSuccess: () => {
      toast.success("Check-in confirmed.");
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (e: unknown) => toast.error(actionError(e, "confirm this check-in")),
  });
  const noMember =
    status.error instanceof ApiError &&
    status.error.status === 404 &&
    /no member record/i.test(status.error.message);
  if (noMember || isNoCheckin(preference.error)) return null;

  const waiting = (changes.data?.items ?? []).filter((item) => item.holds_checkin).length;
  const state = checkinStateWords({
    status: status.data ?? null,
    today: focus.data?.as_of ?? null,
    waitingOnYes: readOnly ? 0 : waiting,
  });
  // Confirm stands for "what is on record is still true": offered for a status
  // that is there and not yet confirmed today, never on a past day, and not while
  // the day's check-in waits for a yes on a Jira change.
  const today = focus.data?.as_of ?? null;
  const provenance =
    status.data && today
      ? checkinProvenance({
          source: status.data.source,
          statusAsOf: status.data.status_as_of,
          today,
          developerConfirmed: status.data.developer_confirmed,
        })
      : null;
  const offersConfirm =
    provenance !== null &&
    provenance.kind !== "none" &&
    !provenance.confirmedToday &&
    !readOnly &&
    waiting === 0;
  const send = preference.data?.send;
  const when = send
    ? isOff(send)
      ? SEND_OFF
      : `Ora asks ${sendWords(send, preference.data?.timezone ?? deviceTimezone() ?? null, new Date())}. It takes about a minute.`
    : null;

  return (
    <VizCard aria-label="Your check-in" className="content-start gap-2">
      <h2 className="text-[13px] font-bold uppercase tracking-wider text-grey-secondary">
        Your check-in
      </h2>
      <p className="text-[17px] font-extrabold">{state.line}</p>
      {when ? <p className="text-[13px] text-grey-body">{when}</p> : null}
      {tasks !== null && tasks > 0 ? (
        <p className="text-[13px] text-grey-body">
          {plural(tasks, "task", "tasks")} to say where they stand.
        </p>
      ) : null}
      <div className="mt-1 flex flex-wrap gap-2">
        {offersConfirm ? (
          <Pill
            onClick={() => confirm.mutate()}
            disabled={confirm.isPending}
            title={reason ?? undefined}
          >
            {confirm.isPending ? "Confirming…" : "Confirm, nothing changed"}
          </Pill>
        ) : null}
        <Link
          to="/check-in"
          className={
            offersConfirm
              ? "inline-flex min-h-11 w-fit items-center rounded-full border border-ink px-5 text-[14px] font-bold text-ink no-underline hover:bg-grey-fill"
              : "inline-flex min-h-11 w-fit items-center rounded-full bg-magenta px-5 text-[14px] font-bold text-white no-underline hover:bg-magenta-hover"
          }
        >
          {state.replied || readOnly
            ? "Open check-in"
            : offersConfirm
              ? "Correct it"
              : "Start check-in"}
        </Link>
      </div>
    </VizCard>
  );
}

/**
 * Replies today: "8/14", and the ones the tracker filled in, which are never
 * green; `flag` is a scrum master's follow-up flag, under them.
 */
export function RepliesCard({
  counts,
  isLoading,
  error,
  tracker,
  flag = null,
}: {
  counts: TodayCountsResponse | undefined;
  isLoading: boolean;
  error: unknown;
  tracker?: string;
  flag?: ReactNode;
}) {
  const words = counts?.replies ? repliesWords(counts.replies, tracker) : null;
  return (
    <CountCard
      title="Replies today"
      label="Replies today"
      figure={words?.count ?? null}
      lines={[words?.headline ?? null, words?.inferred ?? null, words?.waiting ?? null]}
      isLoading={isLoading}
      error={error}
      flag={flag}
    />
  );
}

/**
 * Open warnings over the person's projects, from the read All projects' Early
 * warnings says (blockers people reported included), with the way to read them.
 */
export function WarningsCard({
  view,
  isLoading,
  error,
  link,
}: {
  view: Pick<PortfolioWarningsResponse, "total" | "projects"> | undefined;
  isLoading: boolean;
  error: unknown;
  link: { to: string; text: string } | null;
}) {
  return (
    <CountCard
      title="Open warnings"
      label="Open warnings"
      figure={view ? String(view.total) : null}
      lines={[
        view ? openWarningsLine(view.total, view.projects) : null,
        "What Jira and Git say, whatever anyone reports.",
      ]}
      link={link}
      isLoading={isLoading}
      error={error}
    />
  );
}
