import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { AskResponse, BriefKind, CrossPersonRequestStatus } from "../api/schema";
import type { Scope } from "../app/access";
import {
  podsOfPerson,
  projectsOfPerson,
  useMemberId,
  usePods,
  useProjects,
} from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { ChipPicker, Panel } from "../components/ui/Bits";
import { Pill } from "../components/ui/Pill";
import { RagChip } from "../components/ui/RagChip";
import { actionError } from "../lib/errors";
import { formatDate } from "../lib/format";
import { plural, spaced } from "../lib/words";
import { RequestCard } from "../features/coordination/RequestCard";
import { requestsAmong, scopePeople } from "../features/coordination/raised";

/**
 * Who is waiting on whom across the teams, the narrative briefs, and a
 * plain-language question answered from the graph. What waits on the viewer,
 * and what they raised, is on their Today (Your asks). The board is for those
 * who may act on some of its cards: not an executive, whom the waits reach
 * through the exec brief and the Daily report.
 */
export function CoordinationPage() {
  const { coordination } = useRole().access;
  return (
    <>
      <SectionHeader
        title="Coordination"
        meta={
          coordination.board
            ? "Requests between people, briefs, and Ask the graph. Your own asks are on Today."
            : "Briefs, and Ask the graph. Your own asks are on Today."
        }
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8">
        {coordination.board ? <Requests defaultScope={coordination.boardScope} /> : null}
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          {coordination.briefs ? <Briefs fallback={coordination.defaultBrief} /> : null}
          {coordination.ask ? <AskTheGraph /> : null}
        </div>
      </div>
    </>
  );
}

const COLUMNS: { status: CrossPersonRequestStatus; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "acknowledged", label: "Acknowledged" },
  { status: "needs_resolution", label: "Needs resolution" },
];

const SCOPE_WORDS: Record<Exclude<Scope, "all">, string> = {
  pods: "Your pods",
  projects: "Your projects",
};

/**
 * The board opens on the viewer's own part of it: a scrum master's pods, a
 * product owner's projects (the people of every pod on them), and everything
 * for a manager or admin, who can narrow it to the pods they run. Someone with
 * no pod of their own sees everything, with no choice to make.
 */
function useBoardScope(defaultScope: Scope) {
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const own = podsOfPerson(pods.data ?? [], memberId);
  const ownProjects = projectsOfPerson(projects.data ?? [], own.pods, own.own);
  const narrow: Exclude<Scope, "all"> | null =
    defaultScope === "projects" ? (ownProjects.own ? "projects" : null) : own.own ? "pods" : null;
  const people =
    narrow === "projects"
      ? scopePeople(pods.data ?? [], { projectIds: ownProjects.projects.map((p) => p.id) })
      : narrow === "pods"
        ? scopePeople(pods.data ?? [], { podIds: own.pods.map((p) => p.id) })
        : null;
  return {
    narrow,
    people,
    opensOn: (narrow && defaultScope !== "all" ? narrow : "all") as Scope,
    loading: pods.isLoading || projects.isLoading,
  };
}

function Requests({ defaultScope }: { defaultScope: Scope }) {
  const [search, setSearch] = useSearchParams();
  const scope = useBoardScope(defaultScope);
  const board = useQuery({
    queryKey: ["requests", "portfolio"],
    queryFn: () => apiClient.portfolioCrossPersonRequests(null),
  });
  const asked = search.get("requests");
  const chosen: Scope =
    asked === "all" ? "all" : asked === "own" && scope.narrow ? scope.narrow : scope.opensOn;
  const all = board.data?.requests ?? [];
  const shown = chosen !== "all" && scope.people ? requestsAmong(all, scope.people) : all;
  const pick = (next: Scope) =>
    setSearch(
      (current) => {
        // Other parameters (the day being viewed, the brief kind) are not the board's.
        const params = new URLSearchParams(current);
        if (next === scope.opensOn) params.delete("requests");
        else params.set("requests", next === "all" ? "all" : "own");
        return params;
      },
      { replace: true },
    );

  return (
    <Panel
      title="Requests board"
      note={
        chosen === "all"
          ? "every open ask across the teams"
          : `asks made or received by the people of ${chosen === "pods" ? "your pods" : "your projects"}`
      }
    >
      {scope.narrow ? (
        <ChipPicker
          label="Requests of"
          value={chosen}
          onChange={pick}
          options={[
            ...(scope.opensOn === "all" ? [{ value: "all" as Scope, label: "Everything" }] : []),
            { value: scope.narrow as Scope, label: SCOPE_WORDS[scope.narrow] },
            ...(scope.opensOn === "all" ? [] : [{ value: "all" as Scope, label: "Everything" }]),
          ]}
        />
      ) : null}
      <PanelState
        isLoading={board.isLoading || scope.loading}
        error={board.error}
        onRetry={() => void board.refetch()}
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-3">
          {COLUMNS.map((col) => {
            const items = shown.filter((r) => r.status === col.status);
            return (
              <div key={col.status} className="min-w-0 rounded-2xl bg-grey-fill p-3">
                <p className="mb-2 text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
                  {col.label} · {items.length}
                </p>
                {items.length === 0 ? (
                  <p className="text-[13px] text-grey-secondary">None.</p>
                ) : (
                  <ul className="grid gap-2">
                    {items.map((r) => (
                      <RequestCard key={r.id} request={r} />
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </PanelState>
    </Panel>
  );
}

const BRIEF_FILTERS: { value: "all" | BriefKind; label: string }[] = [
  { value: "all", label: "All" },
  { value: "exec", label: "Exec" },
  { value: "weekly_project", label: "Weekly project" },
  { value: "daily_pod", label: "Daily pod" },
];

/** The newest few; real briefs are paragraphs, and a page of twenty buries the rest. */
const BRIEFS_SHOWN = 5;

/**
 * The briefs, newest first, opening on the kind the role reads: the daily pod
 * brief for a scrum master, the weekly project brief for a product owner, the
 * exec brief for a manager, executive or admin. "All" is a pick like the others
 * (`?brief=all`); a link with no kind opens on the role's own.
 */
function Briefs({ fallback }: { fallback: BriefKind }) {
  const [search, setSearch] = useSearchParams();
  const [showAll, setShowAll] = useState(false);
  const raw = search.get("brief");
  const kind = BRIEF_FILTERS.some((f) => f.value === raw) ? (raw as "all" | BriefKind) : fallback;
  const briefs = useQuery({
    queryKey: ["briefs", kind],
    queryFn: () => apiClient.personaBriefs(kind === "all" ? undefined : kind, 20),
  });
  const all = briefs.data?.briefs ?? [];
  const shown = showAll ? all : all.slice(0, BRIEFS_SHOWN);

  return (
    <Panel title="Briefs" note="written from facts only, no raw chat">
      <PanelState isLoading={briefs.isLoading} error={briefs.error}>
        <ChipPicker
          label="Brief kind"
          value={kind}
          onChange={(next) => {
            setShowAll(false);
            setSearch(
              (current) => {
                // Other parameters (the day being viewed) are the shell's; leave them.
                const params = new URLSearchParams(current);
                if (next === fallback) params.delete("brief");
                else params.set("brief", next);
                return params;
              },
              { replace: true },
            );
          }}
          options={BRIEF_FILTERS}
        />
        {all.length === 0 ? (
          <p className="text-[13px] text-grey-secondary">
            No briefs of this kind yet. They are written from the day's facts on a schedule.
          </p>
        ) : (
          <>
            <ul className="grid gap-3">
              {shown.map((b) => (
                <li
                  key={`${b.kind}-${b.scope_id}-${b.generated_at}`}
                  className="rounded-2xl border border-grey-border p-4"
                >
                  <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                    {spaced(b.kind)} · {formatDate(b.generated_at)}
                  </p>
                  <p className="mt-1 text-[15px] font-extrabold">{b.title}</p>
                  {b.bullets && b.bullets.length > 0 ? (
                    <ul className="mt-2 grid list-disc gap-1 pl-5 text-[14px] text-grey-body">
                      {b.bullets.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[14px] text-grey-body">{b.body}</p>
                  )}
                  <p className="mt-2 text-[11px] text-grey-secondary">
                    From {plural(b.sources.length, "source", "sources")}
                  </p>
                </li>
              ))}
            </ul>
            {all.length > BRIEFS_SHOWN ? (
              <div className="mt-3">
                <Pill size="sm" variant="ghost" onClick={() => setShowAll((on) => !on)}>
                  {showAll ? "Show fewer" : `Show ${all.length - BRIEFS_SHOWN} older`}
                </Pill>
              </div>
            ) : null}
          </>
        )}
      </PanelState>
    </Panel>
  );
}

const EXAMPLES = [
  "Which projects are at risk because of the payments API?",
  "Who has not checked in this week?",
  "What changed in Checkout Revamp in the last 7 days?",
];

function AskTheGraph() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const ask = useMutation({
    mutationFn: (q: string) => apiClient.ask({ question: q }),
    onSuccess: setAnswer,
    onError: (e: unknown) => toast.error(actionError(e, "answer that")),
  });

  return (
    <Panel title="Ask the graph" note="answered from the delivery graph, not a guess">
      <div className="grid gap-3">
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim()) ask.mutate(question.trim());
          }}
        >
          <label htmlFor="ask-q" className="sr-only">
            Question
          </label>
          <textarea
            id="ask-q"
            className="min-h-20 w-full rounded-2xl border border-grey-border p-3 text-[14px]"
            placeholder={EXAMPLES[0]}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Pill type="submit" size="sm" disabled={!question.trim() || ask.isPending}>
              {ask.isPending ? "Asking…" : "Ask"}
            </Pill>
            {EXAMPLES.slice(1).map((ex) => (
              <button
                key={ex}
                type="button"
                className="text-[12px] font-bold text-magenta"
                onClick={() => setQuestion(ex)}
              >
                {ex}
              </button>
            ))}
          </div>
        </form>
        {answer ? (
          <div className="rounded-2xl bg-grey-fill p-4 text-[14px]">
            <p className="whitespace-pre-line">{answer.answer}</p>
            {(answer.sources ?? []).length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(answer.sources ?? []).map((s) => (
                  <RagChip key={s.id} tone="info" className="h-6 px-2.5 text-[11px]">
                    {s.label ?? s.id}
                    {s.kind ? ` · ${spaced(s.kind)}` : ""}
                  </RagChip>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
