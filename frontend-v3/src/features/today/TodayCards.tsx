import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { ApiError, apiClient } from "../../api/client";
import type { TodayCountsResponse } from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { plural } from "../../lib/words";
import { deviceTimezone } from "../../lib/zones";
import { isOff, SEND_OFF, sendWords } from "../checkin/schedule";
import { isNoCheckin, useMyCheckinPreference } from "../checkin/useMyCheckinPreference";
import { VizCard } from "../overall/viz";
import { useMyJiraChanges } from "./queries";
import { checkinStateWords, repliesWords, warningsWords } from "./todayWords";

/** A count card: a figure, what it counts, one more line, and where to go next. */
function CountCard({
  title,
  figure,
  lines,
  link,
  label,
  isLoading,
  error,
}: {
  title: string;
  figure: string | null;
  lines: (string | null)[];
  link?: { to: string; text: string } | null;
  label: string;
  isLoading: boolean;
  error: unknown;
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
 * the day's check-in waits for their yes on a Jira change), when Ora asks, and
 * the way to the Check-in page. Not drawn for someone with no member record,
 * who is never asked. The full card, with Confirm and Correct, is on Check-in.
 */
export function CheckinAction({ tasks = null }: { tasks?: number | null }) {
  const status = useQuery({ queryKey: ["me", "status"], queryFn: () => apiClient.myStatus() });
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const preference = useMyCheckinPreference(true);
  const changes = useMyJiraChanges();
  const { readOnly } = useReadOnly();
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
      <Link
        to="/check-in"
        className="mt-1 inline-flex min-h-11 w-fit items-center rounded-full bg-magenta px-5 text-[14px] font-bold text-white no-underline hover:bg-magenta-hover"
      >
        {state.replied || readOnly ? "Open check-in" : "Start check-in"}
      </Link>
    </VizCard>
  );
}

/** Replies today: "8/14", and the ones the tracker filled in, which are never green. */
export function RepliesCard({
  counts,
  isLoading,
  error,
  tracker,
}: {
  counts: TodayCountsResponse | undefined;
  isLoading: boolean;
  error: unknown;
  tracker?: string;
}) {
  const words = counts ? repliesWords(counts.replies, tracker) : null;
  return (
    <CountCard
      title="Replies today"
      label="Replies today"
      figure={words?.count ?? null}
      lines={[words?.headline ?? null, words?.inferred ?? null, words?.waiting ?? null]}
      isLoading={isLoading}
      error={error}
    />
  );
}

/** Open warnings over the person's part, with the way to read them. */
export function WarningsCard({
  counts,
  isLoading,
  error,
  link,
}: {
  counts: TodayCountsResponse | undefined;
  isLoading: boolean;
  error: unknown;
  link: { to: string; text: string } | null;
}) {
  return (
    <CountCard
      title="Open warnings"
      label="Open warnings"
      figure={counts ? String(counts.open_warnings) : null}
      lines={[
        counts
          ? `${warningsWords(counts.open_warnings)} across ${plural(counts.project_count, "project", "projects")}.`
          : null,
        "What Jira and Git say, whatever anyone reports.",
      ]}
      link={link}
      isLoading={isLoading}
      error={error}
    />
  );
}
