import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";

import type { Rag, ScopeDeliveryResponse } from "../../api/schema";
import { useShownDay } from "../../app/viewingDate";
import { verdictCause } from "../../features/overall/overallWords";
import { formatDate, formatDay } from "../../lib/format";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { Card } from "./Card";
import { RagChip } from "./RagChip";
import {
  committedBy,
  compactParts,
  counted,
  forecastGap,
  forecastTone,
  historyWords,
  missingDate,
  teamTone,
  teamWords,
  verdictChip,
  verdictRag,
} from "./dateStripWords";
import { TONE_FILL, TONE_TEXT } from "./tone";

const EDGE: Record<Rag, string> = {
  red: "border-l-rag-red",
  amber: "border-l-rag-amber",
  green: "border-l-rag-green",
  unknown: "border-l-grey-border",
};
const BIG = "text-[26px] font-extrabold leading-tight text-ink tabular-nums";
const NONE = "text-[18px] font-bold leading-tight text-grey-secondary";
const SUB = "mt-1 text-[12px] text-grey-secondary";

/**
 * The dates first, wherever a project or pod is shown: what was committed, what
 * the completion rate forecasts against it (or that there is not enough history
 * yet), and the latest date the team itself gives. The edge and the chip carry
 * the verdict's colour. Red is for what needs action: no committed date, and the
 * verdict's cause in a red box under the cells. Each forecast is coloured by
 * whether it meets the committed date, by the server's own rule (green, amber,
 * red); with no forecast, or no date to meet, it stays grey.
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
  const inScope = counted(scope);
  const rag = verdictRag(scope.verdict, inScope);
  const chip = verdictChip(scope);
  const cause = inScope ? verdictCause(scope) : null;

  return (
    <Card padding="p-5" className={cn("border-l-[6px]", EDGE[rag], className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
          {title ?? "Delivery dates"}
        </h2>
        {/* "No committed date" is what the first cell says; the chip does not repeat it. */}
        {inScope && scope.verdict === "no_date" ? null : (
          <RagChip tone={chip.tone} className="h-6 px-2.5 text-[12px]">
            {chip.label}
          </RagChip>
        )}
      </div>
      <DateCells scope={scope} action={action} className="mt-3" />
      {/* Only a verdict that needs action has a cause: it is red, and boxed so it is seen. */}
      {cause ? (
        <p className="mt-3 flex items-start gap-2 rounded-2xl bg-rag-red-bg px-3 py-2 text-[14px] font-bold text-rag-red">
          <CircleAlert size={16} aria-hidden className="mt-0.5 flex-none" />
          <span>{cause.because}</span>
        </p>
      ) : null}
      {caption ? <div className="mt-2 text-[12px] text-grey-secondary">{caption}</div> : null}
      {children}
    </Card>
  );
}

/**
 * The strip's three cells, on their own: the committed date (with Set or Change
 * date for someone who may), the forecast against it, and the team's own latest
 * date. Overall's answer card puts its sentence above them instead of the
 * strip's title and cause.
 */
export function DateCells({
  scope,
  action,
  className,
}: {
  scope: ScopeDeliveryResponse;
  action?: ReactNode;
  className?: string;
}) {
  const shownDay = useShownDay();
  const inScope = counted(scope);
  const { p50, p85 } = scope.history;
  const gap = forecastGap(scope.target, p50, p85);
  const forecast = inScope ? forecastTone(scope.target, p50, p85) : "neutral";
  const by = committedBy(scope);
  const team = scope.team;
  const teamPast = Boolean(team.latest && shownDay && team.latest < shownDay);
  const teamSays = inScope ? teamTone(scope.target, team) : "neutral";
  const noDate = missingDate(scope);

  return (
    <dl className={cn("grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3", className)}>
      <Cell label="Committed" tone={noDate ? "danger" : "neutral"}>
        {scope.target ? (
          <>
            <p className={BIG}>{formatDate(scope.target)}</p>
            {by ? <p className={SUB}>{by}</p> : null}
          </>
        ) : noDate ? (
          <p className="flex items-center gap-1.5 text-[18px] font-extrabold leading-tight text-rag-red">
            <CircleAlert size={18} aria-hidden className="flex-none" />
            No committed date
          </p>
        ) : (
          <p className={NONE}>No committed date</p>
        )}
        {action ? <div className="mt-2">{action}</div> : null}
      </Cell>
      <Cell label="Forecast" quiet={inScope && !p50} tone={forecast}>
        {!inScope ? (
          <>
            <p className={NONE}>Nothing to forecast</p>
            <p className={SUB}>No requirements are counted yet.</p>
          </>
        ) : p50 ? (
          <>
            <p className={cn(BIG, TONE_TEXT[forecast])}>{formatDate(p50)}</p>
            <p className={cn(SUB, "flex flex-wrap items-center gap-2")}>
              <span>{p85 ? `85% by ${formatDay(p85)}` : "no 85% date"}</span>
              {gap ? <OnFill tone={gap.tone}>{gap.text}</OnFill> : null}
            </p>
          </>
        ) : (
          <>
            <p className="text-[18px] font-bold leading-tight text-grey-body">Not enough history</p>
            <p className={SUB}>{historyWords(scope.history)}</p>
          </>
        )}
      </Cell>
      <Cell label="Team says" tone={teamSays}>
        {team.latest ? (
          <>
            <p className={cn(BIG, TONE_TEXT[teamSays])}>{formatDate(team.latest)}</p>
            <p className={cn(SUB, "flex flex-wrap items-center gap-2")}>
              <span>{teamWords(team)}</span>
              {teamPast ? <OnFill tone="danger">past</OnFill> : null}
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
  );
}

function Cell({
  label,
  quiet = false,
  tone = "neutral",
  children,
}: {
  label: string;
  /** No forecast to show yet: the cell is grey, and never coloured. */
  quiet?: boolean;
  /** A forecast against the committed date, or a missing date: the cell is tinted in it. */
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return (
    <div className={cn("min-w-0 rounded-2xl p-3", quiet ? "bg-grey-fill" : TONE_FILL[tone])}>
      <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        {label}
      </dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

/** A chip that reads on a tinted cell: white, in its tone's text colour. */
function OnFill({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <RagChip tone={tone} className="h-5 bg-(--op-viz-surface) px-2 text-[11px]">
      {children}
    </RagChip>
  );
}

/**
 * One line: "15 Dec 2026 · forecast: Wed 4 Nov, +5 days · [Off track]". No
 * committed date is a red chip; the forecast words take their colour.
 */
export function CompactDateStrip({
  scope,
  className,
  verdict = true,
}: {
  scope: ScopeDeliveryResponse;
  className?: string;
  /** False where the verdict is shown elsewhere on the same card, so it is not said twice. */
  verdict?: boolean;
}) {
  const { date, forecast, chip: verdictChipOf } = compactParts(scope);
  const chip = verdict ? verdictChipOf : null;
  return (
    <p
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-grey-body",
        className,
      )}
    >
      {date ? (
        <strong className="text-ink tabular-nums">{formatDate(date)}</strong>
      ) : missingDate(scope) ? (
        <RagChip tone="danger" className="h-5 gap-1 px-2 text-[11px]">
          <CircleAlert size={12} aria-hidden />
          No committed date
        </RagChip>
      ) : (
        <strong className="font-bold text-grey-secondary">No committed date</strong>
      )}
      {(forecast ?? []).map((part) => (
        <span key={part.text} className="contents">
          <span aria-hidden>·</span>
          <span className={cn(part.tone !== "neutral" && "font-bold", TONE_TEXT[part.tone])}>
            {part.text}
          </span>
        </span>
      ))}
      {chip ? (
        <RagChip tone={chip.tone} className="h-5 px-2 text-[11px]">
          {chip.label}
        </RagChip>
      ) : null}
    </p>
  );
}
