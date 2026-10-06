import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type {
  AskResponse,
  BriefKind,
  CrossPersonRequestResponse,
  CrossPersonRequestStatus,
} from "../api/schema";
import { useMemberId, useNames } from "../app/directory";
import { useRole } from "../app/role";
import { useReadOnly } from "../app/viewingDate";
import { PanelState, SectionHeader } from "../components/PanelState";
import { ChipPicker, Panel } from "../components/ui/Bits";
import { Pill } from "../components/ui/Pill";
import { RagChip } from "../components/ui/RagChip";
import { actionError } from "../lib/errors";
import { daysBetween, formatDate } from "../lib/format";
import {
  daysLabel,
  deliveryNote,
  plural,
  requestKindLabel,
  requestStatusLabel,
  spaced,
} from "../lib/words";

const NEEDS = "a scrum master, product owner, manager, executive or admin";

/**
 * Who is waiting on whom, the narrative briefs, and a plain-language question
 * answered from the graph. Team and executive readers see the portfolio-wide
 * board; everyone sees what waits on them and what they raised.
 */
export function CoordinationPage() {
  return (
    <>
      <SectionHeader
        title="Coordination"
        meta="Requests between people, briefs, and Ask the graph."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8">
        <Requests />
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Briefs />
          <AskTheGraph />
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

function Requests() {
  const { canReadAggregate } = useRole();
  const board = useQuery({
    queryKey: ["requests", "portfolio"],
    queryFn: () => apiClient.portfolioCrossPersonRequests(null),
    enabled: canReadAggregate,
  });
  const waiting = useQuery({
    queryKey: ["requests", "mine", "waiting"],
    queryFn: () => apiClient.myCrossPersonRequests("waiting"),
  });
  const raised = useQuery({
    queryKey: ["requests", "mine", "raised"],
    queryFn: () => apiClient.myCrossPersonRequests("raised"),
  });
  const live = (list: CrossPersonRequestResponse[] | undefined) =>
    (list ?? []).filter((r) => r.status === "open" || r.status === "acknowledged");

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {canReadAggregate ? (
        <Panel title="Requests board" note="every open ask across the portfolio">
          <PanelState needs={NEEDS} isLoading={board.isLoading} error={board.error}>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-3">
              {COLUMNS.map((col) => {
                const items = (board.data?.requests ?? []).filter((r) => r.status === col.status);
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
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
        <Panel title="Waiting on you">
          <PanelState
            needs="anyone with a member record"
            isLoading={waiting.isLoading}
            error={waiting.error}
            isEmpty={live(waiting.data?.requests).length === 0}
            emptyText="Nothing waiting on you."
          >
            <ul className="grid gap-2">
              {live(waiting.data?.requests).map((r) => (
                <RequestCard key={r.id} request={r} showStatus />
              ))}
            </ul>
          </PanelState>
        </Panel>
        <Panel title="Raised by you" note="and where each one has got to">
          <PanelState
            needs="anyone with a member record"
            isLoading={raised.isLoading}
            error={raised.error}
            isEmpty={live(raised.data?.requests).length === 0}
            emptyText="You have no open asks of others."
          >
            <ul className="grid gap-2">
              {live(raised.data?.requests).map((r) => (
                <RequestCard key={r.id} request={r} showStatus />
              ))}
            </ul>
          </PanelState>
        </Panel>
      </div>
    </div>
  );
}

/**
 * One request. The person asking and the person asked are named (you, when it
 * is the viewer); the board's column already says where it stands, the two
 * personal lists say it on the card (`showStatus`).
 */
function RequestCard({
  request,
  showStatus = false,
}: {
  request: CrossPersonRequestResponse;
  showStatus?: boolean;
}) {
  const names = useNames();
  const memberId = useMemberId();
  const { readOnly, reason } = useReadOnly();
  const queryClient = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const age = daysBetween(request.created_at, today);
  const update = useMutation({
    mutationFn: (status: CrossPersonRequestStatus) =>
      apiClient.updateCrossPersonRequestStatus(request.id, { status }),
    onSuccess: (_r, status) => {
      toast.success(status === "resolved" ? "Request resolved." : "Request acknowledged.");
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
    onError: (e: unknown) => {
      toast.error(actionError(e, "update this request"));
      // Someone else may have closed or removed it: show the lists as they are now.
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
  });

  const raisedByViewer = Boolean(memberId && request.requester_id === memberId);
  const askedOfViewer = Boolean(memberId && request.counterpart_id === memberId);
  const requester = raisedByViewer ? "You" : names.or(request.requester_id, "Someone");
  const counterpart = askedOfViewer
    ? "you"
    : (request.counterpart_display_name ?? request.raw_name ?? "someone not matched yet");
  const delivery = deliveryNote(request.delivery, raisedByViewer);
  const live = request.status === "open" || request.status === "acknowledged";
  // Acknowledging is the asked person's: someone who only raised the request
  // can resolve it, not acknowledge their own ask.
  const canAcknowledge = request.status === "open" && (!raisedByViewer || askedOfViewer);

  return (
    <li className="rounded-2xl border border-grey-border bg-white p-3">
      <p className="text-[13px] font-bold">
        {requester} → {counterpart}
      </p>
      <p className="mt-0.5 text-[13px] text-grey-body">{request.note || "No note."}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-grey-secondary">
        <RagChip tone="neutral" className="h-5 px-2 text-[11px]">
          {requestKindLabel(request.kind)}
        </RagChip>
        {showStatus ? (
          <RagChip
            tone={request.status === "acknowledged" ? "success" : "warning"}
            className="h-5 px-2 text-[11px]"
          >
            {requestStatusLabel(request.status)}
          </RagChip>
        ) : null}
        <span>{age <= 0 ? "raised today" : `waiting ${daysLabel(age)}`}</span>
        {delivery ? (
          <span className={delivery.bad ? "font-bold text-rag-red" : ""}>· {delivery.text}</span>
        ) : null}
      </div>
      {request.status === "needs_resolution" ? (
        <p className="mt-2 text-[12px] text-grey-secondary">
          The name wasn't matched to anyone. OpenProgram asked the requester who was meant; it stays
          here until they answer.
        </p>
      ) : null}
      {live || request.status === "needs_resolution" ? (
        <div className="mt-2 flex gap-1.5">
          {canAcknowledge ? (
            <Pill
              size="sm"
              variant="ghost"
              disabled={update.isPending || readOnly}
              title={reason ?? undefined}
              aria-label={`Acknowledge: ${request.note || requestKindLabel(request.kind)}`}
              onClick={() => update.mutate("acknowledged")}
            >
              Acknowledge
            </Pill>
          ) : null}
          <Pill
            size="sm"
            variant="dark"
            disabled={update.isPending || readOnly}
            title={reason ?? undefined}
            aria-label={`Resolve: ${request.note || requestKindLabel(request.kind)}`}
            onClick={() => update.mutate("resolved")}
          >
            Resolve
          </Pill>
        </div>
      ) : null}
    </li>
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

function Briefs() {
  const { canReadAggregate } = useRole();
  const [search, setSearch] = useSearchParams();
  const [showAll, setShowAll] = useState(false);
  const raw = search.get("brief");
  const kind = BRIEF_FILTERS.some((f) => f.value === raw) ? (raw as "all" | BriefKind) : "all";
  const briefs = useQuery({
    queryKey: ["briefs", kind],
    queryFn: () => apiClient.personaBriefs(kind === "all" ? undefined : kind, 20),
    enabled: canReadAggregate,
  });
  const all = briefs.data?.briefs ?? [];
  const shown = showAll ? all : all.slice(0, BRIEFS_SHOWN);

  return (
    <Panel title="Briefs" note="written from facts only, no raw chat">
      <PanelState
        locked={!canReadAggregate}
        needs={NEEDS}
        isLoading={briefs.isLoading}
        error={briefs.error}
      >
        <ChipPicker
          label="Brief kind"
          value={kind}
          onChange={(next) => {
            setShowAll(false);
            setSearch(
              (current) => {
                // Other parameters (the day being viewed) are the shell's; leave them.
                const params = new URLSearchParams(current);
                if (next === "all") params.delete("brief");
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
  const { canReadAggregate } = useRole();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const ask = useMutation({
    mutationFn: (q: string) => apiClient.ask({ question: q }),
    onSuccess: setAnswer,
    onError: (e: unknown) => toast.error(actionError(e, "answer that")),
  });

  return (
    <Panel title="Ask the graph" note="answered from the delivery graph, not a guess">
      {!canReadAggregate ? (
        <p className="text-[14px] text-grey-body">
          Questions are answered from team and portfolio reads, which open for {NEEDS}. Your own
          work is on Today.
        </p>
      ) : (
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
      )}
    </Panel>
  );
}
