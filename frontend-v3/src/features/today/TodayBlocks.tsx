import { useQueries, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, PodCheckinsResponse } from "../../api/schema";
import { podsOfPerson, useMemberId, usePods } from "../../app/directory";
import { podTabPath, portfolioPath, projectTabPath } from "../../app/links";
import { useDayWords, useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { RagDot } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { boardRag, followUpWords } from "../../lib/checkinWords";
import { formatDate } from "../../lib/format";
import { plural } from "../../lib/words";
import { answerChip } from "../overall/answerWords";
import { usePortfolioIndex } from "../hub/useHub";
import { VizCard } from "../overall/viz";
import { verdictPill } from "../portfolio/portfolioWords";
import { useProjectOptions } from "../project/useProjectOptions";
import { ageWords, itemLine, ownerWords } from "../warnings/warningWords";
import { issueKey, taskRows } from "./taskUpdate";
import {
  nextTaskWhy,
  nextTasks,
  oldestWarnings,
  podLineWords,
  portfolioLine,
  scopeActions,
} from "./todayMore";

/** A small titled block on Today, on the viz surface so it turns with the theme. */
function Block({
  title,
  link,
  children,
}: {
  title: string;
  link?: { to: string; text: string } | null;
  children: ReactNode;
}) {
  return (
    <VizCard aria-label={title} className="content-start gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[13px] font-bold uppercase tracking-wider text-grey-secondary">
          {title}
        </h2>
        {link ? (
          <Link
            to={link.to}
            className="inline-flex min-h-11 items-center gap-1 text-[13px] font-bold sm:min-h-0"
          >
            {link.text} →
          </Link>
        ) : null}
      </div>
      {children}
    </VizCard>
  );
}

/**
 * Under the worst project's answer, for the readers of programmes: every
 * project's verdict in one line, and the way to all of them. One bulk read of
 * the kept rows, one project asked for: only the counts are wanted.
 */
export function PortfolioLine() {
  const read = usePortfolioIndex();
  if (!read.data) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-grey-body">
      <span>{portfolioLine(read.data.counts)}</span>
      <Link
        to={portfolioPath("date")}
        className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
      >
        All projects →
      </Link>
    </p>
  );
}

/** The newest executive brief: its title and bullets, when and from how much it was written. */
export function NewestBrief() {
  const brief = useQuery({
    queryKey: ["briefs", "exec", 1],
    queryFn: () => apiClient.personaBriefs("exec", 1),
  });
  const newest = brief.data?.briefs[0];
  return (
    <Block
      title="Newest executive brief"
      link={{ to: `${portfolioPath("reports")}?brief=exec`, text: "All briefs" }}
    >
      <PanelState
        isLoading={brief.isLoading}
        error={brief.error}
        isEmpty={!newest}
        emptyText="No executive brief has been written yet. It is written weekly from the week's facts."
      >
        {newest ? (
          <div className="text-[14px] text-grey-body">
            <p className="font-bold text-ink">{newest.title}</p>
            {newest.bullets && newest.bullets.length > 0 ? (
              <ul className="mt-2 grid list-disc gap-1 pl-5">
                {newest.bullets.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2">{newest.body}</p>
            )}
            <p className="mt-2 text-[12px] text-grey-secondary">
              {formatDate(newest.generated_at)} · from{" "}
              {plural(newest.sources.length, "source", "sources")}
            </p>
          </div>
        ) : null}
      </PanelState>
    </Block>
  );
}

/**
 * The three oldest open warnings across the reader's projects, each opening its
 * project's Early warnings; the count and every warning are on Early warnings.
 */
export function OldestWarnings() {
  const read = useQuery({
    queryKey: ["portfolio", "warnings", ""],
    queryFn: () => apiClient.portfolioWarnings({}),
  });
  const view = read.data;
  const items = view ? oldestWarnings(view) : [];
  return (
    <Block
      title="Oldest open warnings"
      link={{ to: portfolioPath("warnings"), text: "All early warnings" }}
    >
      <PanelState
        isLoading={read.isLoading}
        error={read.error}
        isEmpty={items.length === 0}
        emptyText="Nothing open: Jira and Git agree with what was reported."
      >
        <ul>
          {items.map((item) => (
            <li
              key={item.id}
              className="flex min-w-0 items-start gap-3 border-t border-grey-border py-2.5 first:border-t-0"
            >
              <RagDot rag={item.severity} className="mt-1.5" />
              <div className="min-w-0 flex-1">
                <p className="text-[14px]">
                  <Link
                    to={projectTabPath(item.project_id, "warnings")}
                    className="font-extrabold text-ink no-underline hover:underline"
                  >
                    {item.key}
                  </Link>{" "}
                  <span className="text-grey-body">{itemLine(item)}</span>
                </p>
                <p className="mt-0.5 text-[12px] text-grey-secondary">
                  {item.title} · {ownerWords(item, item.group)} · {item.project_name}
                </p>
              </div>
              <span className="flex-none text-[12px] font-bold text-grey-secondary tabular-nums">
                {ageWords(item, view?.seen_since)}
              </span>
            </li>
          ))}
        </ul>
      </PanelState>
    </Block>
  );
}

/** The pods a scrum master runs or belongs to, beside the one Today leads with. */
function useOwnPods() {
  const memberId = useMemberId();
  const pods = usePods();
  const { pods: mine, own } = podsOfPerson(pods.data ?? [], memberId);
  return { pods: own ? mine : [], loading: pods.isLoading };
}

/** Each of a scrum master's pods' boards, under the key the pod pages use. */
function useBoards(pods: DirectoryItemResponse[]) {
  return useQueries({
    queries: pods.map((pod) => ({
      queryKey: ["pod", pod.id, "checkins"],
      queryFn: () => apiClient.podCheckins(pod.id),
    })),
  });
}

/**
 * The follow-up flag on a scrum master's replies: how many people on their
 * pods' boards are not green today (no reply, a partial one, a status inferred
 * or carried forward), counted once each, with the way to the worst pod's
 * Who needs a nudge.
 */
export function FollowUpFlag({ lead }: { lead: DirectoryItemResponse }) {
  const { pods } = useOwnPods();
  const boards = useBoards(pods);
  const day = useDayWords();
  const people = new Map<string, boolean>();
  boards.forEach((board) =>
    (board.data?.developers ?? []).forEach((person) =>
      people.set(
        person.developer_id,
        (people.get(person.developer_id) ?? false) || boardRag(person) !== "green",
      ),
    ),
  );
  const count = [...people.values()].filter(Boolean).length;
  if (count === 0) return null;
  const projectId = lead.project_ids[0];
  const to = projectId ? `${podTabPath(projectId, lead.id, "warnings")}#checkins` : null;
  const flag = (
    <>
      <span
        aria-hidden
        className="inline-flex h-6 min-w-6 flex-none items-center justify-center rounded-full bg-rag-red px-1.5 text-[14px] font-extrabold leading-none text-white animate-op-pulse"
      >
        !
      </span>
      <span className="font-bold text-rag-red">
        {followUpWords(count)} {day}
      </span>
    </>
  );
  return to ? (
    <Link
      to={to}
      className="inline-flex min-h-11 items-center gap-2 text-[13px] no-underline sm:min-h-0"
    >
      {flag}
    </Link>
  ) : (
    <p className="inline-flex items-center gap-2 text-[13px]">{flag}</p>
  );
}

/**
 * A scrum master's other pods, one line each: the date verdict of its worst
 * part, how many replied today and its open blockers, and the way to its
 * morning. The pod Today leads with is said above, so it is not repeated.
 */
export function OtherPods({ lead }: { lead: DirectoryItemResponse }) {
  const { pods } = useOwnPods();
  const others = pods.filter((pod) => pod.id !== lead.id);
  const boards = useBoards(others);
  const blockers = useQueries({
    queries: others.map((pod) => ({
      queryKey: ["pod", pod.id, "blockers"],
      queryFn: () => apiClient.podBlockers(pod.id),
    })),
  });
  const parts = useQueries({
    queries: others.map((pod) => ({
      queryKey: ["pod-delivery", pod.id],
      queryFn: () => apiClient.podDelivery(pod.id),
    })),
  });
  if (others.length === 0) return null;
  return (
    <Block title={others.length === 1 ? "Your other pod" : "Your other pods"}>
      <ul>
        {others.map((pod, index) => {
          const board: PodCheckinsResponse | undefined = boards[index]?.data;
          const people = board
            ? board.confirmed + board.partial + board.stale + board.missing
            : null;
          const chip = worstChip(parts[index]?.data?.projects ?? []);
          const projectId = pod.project_ids[0];
          return (
            <li
              key={pod.id}
              className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-grey-border py-2.5 first:border-t-0"
            >
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-[14px] font-extrabold text-ink">{pod.name}</span>
                {chip ? (
                  <RagChip tone={chip.tone} className="h-6 px-2.5 text-[12px]">
                    {chip.label}
                  </RagChip>
                ) : null}
                <span className="text-[13px] text-grey-body">
                  {podLineWords({
                    replied: board ? board.confirmed : null,
                    people,
                    blockers: blockers[index]?.data ? blockers[index].data.blockers.length : null,
                  })}
                </span>
              </span>
              {projectId ? (
                <Link
                  to={podTabPath(projectId, pod.id, "warnings")}
                  className="inline-flex min-h-11 items-center gap-1 text-[13px] font-bold sm:min-h-0"
                >
                  Open <span aria-hidden>→</span>
                  <span className="sr-only"> {pod.name}</span>
                </Link>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Block>
  );
}

/** The worst of a pod's parts, as its chip: what is late beats what is at risk. */
function worstChip(
  parts: { pod: Parameters<typeof answerChip>[0] }[],
): ReturnType<typeof answerChip> {
  const chips = parts.map((part) => answerChip(part.pod)).filter(Boolean);
  const order = ["danger", "warning", "neutral", "info", "success"];
  return (
    chips.sort((a, b) => order.indexOf(a?.tone ?? "") - order.indexOf(b?.tone ?? ""))[0] ?? null
  );
}

/**
 * A product owner's other projects, one line each: the date verdict and its
 * reason, and the way to the project. The one Today leads with is said above.
 */
export function OtherProjects({ leadId }: { leadId: string }) {
  const choice = useProjectOptions();
  const mine = new Set(choice.options.filter((option) => option.mine).map((option) => option.id));
  mine.delete(leadId);
  const read = usePortfolioIndex(mine.size > 0);
  const rows = (read.data?.projects ?? []).filter((project) => mine.has(project.id));
  if (mine.size === 0 || rows.length === 0) return null;
  return (
    <Block title={rows.length === 1 ? "Your other project" : "Your other projects"}>
      <ul>
        {rows.map((project) => {
          const pill = verdictPill(project);
          return (
            <li
              key={project.id}
              className="flex min-w-0 items-start justify-between gap-3 border-t border-grey-border py-2.5 first:border-t-0"
            >
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-2">
                  <Link
                    to={projectTabPath(project.id, "date")}
                    className="text-[14px] font-extrabold text-ink no-underline hover:underline"
                  >
                    {project.name}
                  </Link>
                  <RagChip tone={pill.tone} className="h-6 px-2.5 text-[12px]">
                    {pill.label}
                  </RagChip>
                </span>
                <span className="mt-0.5 block text-[13px] text-grey-body">{project.reason}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </Block>
  );
}

/**
 * What a product owner's own projects ask of them on Scope control: a
 * planning baseline to take, requirements that joined without the change
 * label, and the Jira hygiene findings still open, each opening the project's
 * Scope control where it is done.
 */
export function ScopeActions() {
  const choice = useProjectOptions();
  const mine = new Set(choice.options.filter((option) => option.mine).map((option) => option.id));
  const read = useQuery({
    queryKey: ["portfolio", "scope"],
    queryFn: () => apiClient.portfolioScope(),
    enabled: mine.size > 0,
  });
  const actions = read.data
    ? scopeActions(read.data.projects, mine, read.data.reads_baselines)
    : [];
  if (mine.size === 0) return null;
  return (
    <Block title="Needs your attention">
      <PanelState
        isLoading={read.isLoading}
        error={read.error}
        isEmpty={actions.length === 0}
        emptyText="Your projects' plans and Jira hygiene ask nothing of you today."
      >
        <ul>
          {actions.map((action) => (
            <li
              key={action.key}
              className="flex min-w-0 items-start justify-between gap-3 border-t border-grey-border py-2.5 first:border-t-0"
            >
              <span className="min-w-0 text-[14px]">
                <span className="font-extrabold text-ink">{action.project}</span>{" "}
                <span className="text-grey-body">{action.text}</span>
              </span>
              <Link
                to={`${projectTabPath(action.projectId, "scope")}#${action.anchor}`}
                className="inline-flex min-h-11 flex-none items-center gap-1 text-[13px] font-bold sm:min-h-0"
              >
                Open <span aria-hidden>→</span>
                <span className="sr-only"> {action.project}&apos;s scope control</span>
              </Link>
            </li>
          ))}
        </ul>
      </PanelState>
    </Block>
  );
}

/**
 * A developer's next three tasks, as the task list sorts them (blocked, past
 * due, an ETA after the due date, then in progress and to do), each opening
 * its update on Check-in.
 */
export function NextTasks() {
  const shownDay = useShownDay();
  const focus = useQuery({ queryKey: ["me", "focus"], queryFn: () => apiClient.focus() });
  const rows = focus.data
    ? nextTasks(taskRows(focus.data.tasks, focus.data.blocker_details, shownDay))
    : [];
  if (!focus.data || rows.length === 0) return null;
  return (
    <Block title="Your next tasks" link={{ to: "/check-in", text: "All your tasks" }}>
      <ul>
        {rows.map((row) => (
          <li
            key={row.task.id}
            className="flex min-w-0 items-start justify-between gap-3 border-t border-grey-border py-2.5 first:border-t-0"
          >
            <span className="min-w-0">
              <span className="block text-[14px] font-extrabold text-ink">{row.task.name}</span>
              <span className="mt-0.5 block text-[12px] text-grey-secondary">
                {[issueKey(row.task), nextTaskWhy(row)].filter(Boolean).join(" · ")}
              </span>
            </span>
            <Link
              to={`/check-in?task=${encodeURIComponent(row.task.id)}`}
              className="inline-flex min-h-11 flex-none items-center gap-1 text-[13px] font-bold sm:min-h-0"
            >
              Update <span aria-hidden>→</span>
              <span className="sr-only"> {row.task.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Block>
  );
}
