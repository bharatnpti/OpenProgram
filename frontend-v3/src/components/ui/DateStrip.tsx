import type { ReactNode } from "react";

import type { Rag, ScopeDeliveryResponse } from "../../api/schema";
import { useShownDay } from "../../app/viewingDate";
import { verdictCause } from "../../features/overall/overallWords";
import { formatDate, formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import { Card } from "./Card";
import { RagChip } from "./RagChip";
import {
  committedBy,
  compactForecast,
  counted,
  forecastGap,
  historyWords,
  teamWords,
  verdictChip,
  verdictRag,
} from "./dateStripWords";

const EDGE: Record<Rag, string> = {
  red: "border-l-rag-red",
  amber: "border-l-rag-amber",
  green: "border-l-rag-green",
  unknown: "border-l-grey-border",
};
const CAUSE: Record<Rag, string> = {
  red: "text-rag-red",
  amber: "text-rag-amber",
  green: "text-rag-green",
  unknown: "text-grey-body",
};
const BIG = "text-[26px] font-extrabold leading-tight text-ink tabular-nums";
const NONE = "text-[18px] font-bold leading-tight text-grey-secondary";
const SUB = "mt-1 text-[12px] text-grey-secondary";

/**
 * The dates first, wherever a project or pod is shown: what was committed, what
 * the completion rate forecasts against it (or that there is not enough history
 * yet), and the latest date the team itself gives. The edge and the chip carry
 * the verdict's colour, and its cause sits under the three cells; nothing else
 * in the strip is coloured but a late forecast and a past team date.
 */
export function DateStrip({
  scope,
  title,
  action,
  caption,
  children,
  className,
}: {
  scope: ScopeDeliveryResponse;
  /** The small heading at the top left; "Delivery dates" when left out. */
  title?: ReactNode;
  /** Set date / Change date, for someone who may; under the committed date. */
  action?: ReactNode;
  /** One grey line under the cells, such as the project's own date beside a pod's. */
  caption?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const shownDay = useShownDay();
  const inScope = counted(scope);
  const rag = verdictRag(scope.verdict, inScope);
  const chip = verdictChip(scope);
  const cause = inScope ? verdictCause(scope) : null;
  const { p50, p85 } = scope.history;
  const gap = forecastGap(scope.target, p50, p85);
  const by = committedBy(scope);
  const team = scope.team;
  const teamPast = Boolean(team.latest && shownDay && team.latest < shownDay);

  return (
    <Card padding="p-5" className={cn("border-l-[6px]", EDGE[rag], className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
          {title ?? "Delivery dates"}
        </h2>
        <RagChip tone={chip.tone} className="h-6 px-2.5 text-[12px]">
          {chip.label}
        </RagChip>
      </div>
      <dl className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
        <Cell label="Committed">
          {scope.target ? (
            <>
              <p className={BIG}>{formatDate(scope.target)}</p>
              {by ? <p className={SUB}>{by}</p> : null}
            </>
          ) : (
            <p className={NONE}>No committed date</p>
          )}
          {action ? <div className="mt-2">{action}</div> : null}
        </Cell>
        <Cell label="Forecast" quiet={inScope && !p50}>
          {!inScope ? (
            <>
              <p className={NONE}>Nothing to forecast</p>
              <p className={SUB}>No requirements are counted yet.</p>
            </>
          ) : p50 ? (
            <>
              <p className={BIG}>{formatDate(p50)}</p>
              <p className={cn(SUB, "flex flex-wrap items-center gap-2")}>
                <span>{p85 ? `85% by ${formatDay(p85)}` : "no 85% date"}</span>
                {gap ? (
                  <RagChip tone={gap.tone} className="h-5 px-2 text-[11px]">
                    {gap.text}
                  </RagChip>
                ) : null}
              </p>
            </>
          ) : (
            <>
              <p className="text-[18px] font-bold leading-tight text-grey-body">
                Not enough history
              </p>
              <p className={SUB}>{historyWords(scope.history)}</p>
            </>
          )}
        </Cell>
        <Cell label="Team says">
          {team.latest ? (
            <>
              <p className={BIG}>{formatDate(team.latest)}</p>
              <p className={cn(SUB, "flex flex-wrap items-center gap-2")}>
                <span>{teamWords(team)}</span>
                {teamPast ? (
                  <RagChip tone="danger" className="h-5 px-2 text-[11px]">
                    past
                  </RagChip>
                ) : null}
              </p>
            </>
          ) : (
            <>
              <p className={NONE}>No ETA or due date</p>
              {inScope && team.undated > 0 ? (
                <p className={SUB}>{team.undated} open without a date</p>
              ) : null}
            </>
          )}
        </Cell>
      </dl>
      {cause ? (
        <p className={cn("mt-3 text-[14px] font-bold", CAUSE[rag])}>{cause.because}</p>
      ) : null}
      {caption ? <div className="mt-2 text-[12px] text-grey-secondary">{caption}</div> : null}
      {children}
    </Card>
  );
}

function Cell({
  label,
  quiet = false,
  children,
}: {
  label: string;
  /** No forecast to show yet: the cell is grey, and never coloured. */
  quiet?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cn("min-w-0 rounded-2xl p-3", quiet ? "bg-grey-fill" : "")}>
      <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        {label}
      </dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

/** One line: "15 Dec 2026 · forecast: not enough history · [At risk]". */
export function CompactDateStrip({
  scope,
  className,
}: {
  scope: ScopeDeliveryResponse;
  className?: string;
}) {
  const chip = verdictChip(scope);
  return (
    <p
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-grey-body",
        className,
      )}
    >
      <strong className={scope.target ? "text-ink tabular-nums" : "font-bold text-grey-secondary"}>
        {scope.target ? formatDate(scope.target) : "No committed date"}
      </strong>
      <span aria-hidden>·</span>
      <span>{compactForecast(scope)}</span>
      <RagChip tone={chip.tone} className="h-5 px-2 text-[11px]">
        {chip.label}
      </RagChip>
    </p>
  );
}
