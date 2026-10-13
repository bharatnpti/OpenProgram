import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { Fragment, useId, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { CrossPersonRequestResponse } from "../api/schema";
import type { Scope } from "../app/access";
import {
  podsOfPerson,
  projectsOfPerson,
  useMemberId,
  useNames,
  usePods,
  useProjects,
} from "../app/directory";
import { useRole } from "../app/role";
import { useShownDay } from "../app/viewingDate";
import { PanelState, SectionHeader } from "../components/PanelState";
import { ChipPicker } from "../components/ui/Bits";
import { RagChip } from "../components/ui/RagChip";
import { PAGE_SURFACE } from "../features/coordination/pageSurface";
import { requestsAmong, scopePeople } from "../features/coordination/raised";
import { RequestActions } from "../features/coordination/RequestActions";
import {
  CellLabel,
  StackedTable,
  stackCell,
  stackRow,
} from "../features/coordination/StackedTable";
import { RequestTrail } from "../features/coordination/RequestTrail";
import {
  countTiles,
  countsFootnote,
  longestWaitFirst,
  matchesSearch,
  newestFirst,
  statusWords,
  type RequestState,
} from "../features/coordination/requestWords";
import { formatDay } from "../lib/format";
import { cn } from "../lib/utils";
import { deliveryNote, plural, requestKindLabel } from "../lib/words";

/*
 * Requests (`/requests`): who is waiting on whom (D85, D91). The board is the
 * Coordination tab's, as a table: Waiting · On · What · Since · Status, the
 * longest wait first, each row opening its trail. Open, or resolved in the
 * last 30 days; the viewer's own pods or projects, or everything. An executive
 * reads the waits as counts only, never who asked whom (D87). What waits on
 * the viewer, and what they raised, stays on their Today (Your asks).
 */

const SCOPE_WORDS: Record<Exclude<Scope, "all">, string> = {
  pods: "Your pods",
  projects: "Your projects",
};

export function RequestsPage() {
  const { coordination } = useRole().access;
  return (
    <div className={PAGE_SURFACE}>
      <SectionHeader
        title="Requests"
        meta="When someone is blocked on a colleague, Ora asks that colleague directly and keeps both sides informed."
      />
      {coordination.board ? <Board defaultScope={coordination.boardScope} /> : <Counts />}
    </div>
  );
}

/** An executive's Requests: how many asks wait, and how long, with no one named. */
function Counts() {
  const counts = useQuery({
    queryKey: ["requests", "counts"],
    queryFn: () => apiClient.crossPersonRequestCounts(),
  });
  const footnote = counts.data ? countsFootnote(counts.data) : null;
  return (
    <PanelState
      isLoading={counts.isLoading}
      error={counts.error}
      onRetry={() => void counts.refetch()}
    >
      {counts.data ? (
        <>
          <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {countTiles(counts.data).map((tile) => (
              <li
                key={tile.label}
                className="min-w-0 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-5 max-sm:p-4"
              >
                <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
                  {tile.label}
                </p>
                <p className="mt-1 text-[32px] font-extrabold leading-none">{tile.value}</p>
                <p className="mt-2 text-[13px] text-grey-body">{tile.note}</p>
              </li>
            ))}
          </ul>
          {footnote ? <p className="mt-3 text-[13px] text-grey-body">{footnote}</p> : null}
        </>
      ) : null}
    </PanelState>
  );
}

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

/** Sets or clears one query parameter, leaving the others (the day viewed) as they are. */
function useParam(name: string) {
  const [search, setSearch] = useSearchParams();
  const set = (value: string | null) =>
    setSearch(
      (current) => {
        const params = new URLSearchParams(current);
        if (value === null) params.delete(name);
        else params.set(name, value);
        return params;
      },
      { replace: true },
    );
  return [search.get(name), set] as const;
}

function Board({ defaultScope }: { defaultScope: Scope }) {
  const scope = useBoardScope(defaultScope);
  const [stateParam, setState] = useParam("state");
  const [scopeParam, setScope] = useParam("requests");
  const [query, setQuery] = useState("");
  const names = useNames();
  const day = useShownDay() ?? new Date().toISOString().slice(0, 10);
  const state: RequestState = stateParam === "resolved" ? "resolved" : "open";
  const open = useQuery({
    queryKey: ["requests", "portfolio"],
    queryFn: () => apiClient.portfolioCrossPersonRequests(null),
    enabled: state === "open",
  });
  const resolved = useQuery({
    queryKey: ["requests", "resolved"],
    queryFn: () => apiClient.resolvedCrossPersonRequests(),
    enabled: state === "resolved",
  });
  const read = state === "open" ? open : resolved;
  const chosen: Scope =
    scopeParam === "all"
      ? "all"
      : scopeParam === "own" && scope.narrow
        ? scope.narrow
        : scope.opensOn;
  const all = read.data?.requests ?? [];
  const inScope = chosen !== "all" && scope.people ? requestsAmong(all, scope.people) : all;
  const who = (request: CrossPersonRequestResponse) => ({
    waiting: names.or(request.requester_id, "Someone"),
    on: request.counterpart_display_name ?? request.raw_name ?? "Nobody matched yet",
  });
  const shown = inScope
    .filter((request) => {
      const { waiting, on } = who(request);
      return matchesSearch(
        `${waiting} ${on} ${request.note} ${requestKindLabel(request.kind)}`,
        query,
      );
    })
    .sort(state === "open" ? longestWaitFirst : newestFirst);

  return (
    <section aria-label="Requests board" className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 [&>div]:mb-0">
        <ChipPicker
          label="Which requests"
          value={state}
          onChange={(next) => setState(next === "open" ? null : next)}
          options={[
            { value: "open" as RequestState, label: "Open" },
            { value: "resolved" as RequestState, label: "Resolved, last 30 days" },
          ]}
        />
        {scope.narrow ? (
          <ChipPicker
            label="Requests of"
            value={chosen}
            onChange={(next) =>
              setScope(next === scope.opensOn ? null : next === "all" ? "all" : "own")
            }
            options={[
              ...(scope.opensOn === "all" ? [{ value: "all" as Scope, label: "Everything" }] : []),
              { value: scope.narrow as Scope, label: SCOPE_WORDS[scope.narrow] },
              ...(scope.opensOn === "all" ? [] : [{ value: "all" as Scope, label: "Everything" }]),
            ]}
          />
        ) : null}
        <label className="min-w-0 flex-1 basis-56">
          <span className="sr-only">Find a request by person, work or kind</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find a person, an issue or a kind…"
            className="h-10 w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] text-ink placeholder:text-grey-secondary"
          />
        </label>
      </div>
      <p className="text-[13px] text-grey-secondary" aria-live="polite">
        {read.data
          ? `${plural(shown.length, state === "open" ? "ask waits" : "ask resolved", state === "open" ? "asks wait" : "asks resolved")}${
              chosen === "all" ? "" : ` among ${chosen === "pods" ? "your pods" : "your projects"}`
            }${state === "open" ? ", the longest wait first" : ", the newest first"}.`
          : null}
      </p>
      <PanelState
        isLoading={read.isLoading || scope.loading}
        error={read.error}
        onRetry={() => void read.refetch()}
        isEmpty={shown.length === 0}
        emptyText={
          query.trim()
            ? `No request matches “${query.trim()}”.`
            : state === "open"
              ? "Nobody is waiting on anyone."
              : "No request was resolved in the last 30 days."
        }
      >
        <RequestTable requests={shown} day={day} who={who} />
      </PanelState>
    </section>
  );
}

function RequestTable({
  requests,
  day,
  who,
}: {
  requests: CrossPersonRequestResponse[];
  day: string;
  who: (request: CrossPersonRequestResponse) => { waiting: string; on: string };
}) {
  const [open, setOpen] = useState<string | null>(null);
  const base = useId();
  return (
    <StackedTable
      caption="Requests: who is waiting on whom, and since when"
      columns={["Waiting", "On", "What", "Since", "Status"]}
    >
      {requests.map((request) => {
        const people = who(request);
        const status = statusWords(request, day);
        const delivery = deliveryNote(request.delivery, false);
        const expanded = open === request.id;
        const trailId = `${base}-${request.id}`;
        const titleId = `${trailId}-title`;
        return (
          <Fragment key={request.id}>
            <tr className={cn(stackRow, expanded && "max-md:rounded-b-none max-md:border-b-0")}>
              <td className={cn(stackCell, "font-bold")}>
                <CellLabel>Waiting</CellLabel>
                {people.waiting}
              </td>
              <td className={cn(stackCell, "font-bold")}>
                <CellLabel>On</CellLabel>
                {people.on}
              </td>
              <td className={cn(stackCell, "max-w-[28rem]")}>
                <button
                  type="button"
                  id={titleId}
                  aria-expanded={expanded}
                  aria-controls={trailId}
                  onClick={() => setOpen(expanded ? null : request.id)}
                  className="-mx-1 flex min-h-11 w-[calc(100%+0.5rem)] items-start gap-2 rounded-xl px-1 py-1 text-left hover:bg-grey-fill md:min-h-0"
                >
                  <ChevronDown
                    size={16}
                    aria-hidden
                    className={cn("mt-0.5 flex-none text-grey-secondary", expanded && "rotate-180")}
                  />
                  <span className="min-w-0">
                    <span className="block text-ink">{request.note || "No note."}</span>
                    <span className="block text-[12px] text-grey-secondary">
                      {requestKindLabel(request.kind)} ·{" "}
                      {expanded ? "Hide the trail" : "Show the trail"}
                    </span>
                  </span>
                </button>
              </td>
              <td className={cn(stackCell, "whitespace-nowrap")}>
                <CellLabel>Since</CellLabel>
                {/* How long it has waited is the status's to say. */}
                {formatDay(request.created_at)}
              </td>
              <td className={stackCell}>
                <CellLabel>Status</CellLabel>
                <RagChip tone={status.tone} className="h-6 px-2.5 text-[12px]">
                  {status.word}
                </RagChip>
                {delivery && delivery.bad ? (
                  <span className="mt-1 block text-[12px] font-bold text-rag-red">
                    {delivery.text}
                  </span>
                ) : null}
              </td>
            </tr>
            {expanded ? (
              <tr className="max-md:-mt-3 max-md:block max-md:rounded-b-2xl max-md:border max-md:border-t-0 max-md:border-grey-border max-md:bg-(--op-viz-surface) max-md:px-3 max-md:pb-3">
                <td
                  colSpan={5}
                  id={trailId}
                  className={cn(stackCell, "md:bg-grey-fill md:px-6 md:py-4")}
                >
                  <RequestTrail requestId={request.id} labelledBy={titleId} />
                  <RequestActions request={request} />
                </td>
              </tr>
            ) : null}
          </Fragment>
        );
      })}
    </StackedTable>
  );
}
