import type { ReactNode } from "react";

import type { Rag } from "../../api/schema";
import { useShownDay } from "../../app/viewingDate";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import { ragWords, toneForRag, type BadgeTone } from "../../lib/status";
import { pastDue } from "./dateStripWords";
import { RagChip } from "./RagChip";

const dotTone: Record<BadgeTone, string> = {
  success: "bg-rag-green",
  warning: "bg-rag-amber",
  danger: "bg-rag-red",
  info: "bg-rag-info",
  neutral: "bg-rag-unknown",
};

/** A status dot; `unknown` and missing are grey, never green. */
export function RagDot({ rag, className }: { rag: Rag | null | undefined; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-2.5 w-2.5 flex-none rounded-full",
        dotTone[toneForRag(rag)],
        className,
      )}
      aria-hidden
    />
  );
}

/**
 * A colour as a chip, in words ("On track", "At risk", "Off track", "Status
 * unknown"), never the enum: a colour nobody reported is "Status unknown". A
 * `label` says something else, such as a verdict. `quiet` draws an unreported
 * colour as plain grey text: on a task list most rows are unknown, and colour is
 * kept for bad news.
 */
export function RagBadge({
  rag,
  label,
  quiet = false,
}: {
  rag: Rag | null | undefined;
  label?: string;
  quiet?: boolean;
}) {
  const words = label ?? ragWords(rag);
  if (quiet && (!rag || rag === "unknown")) {
    return <span className="text-[12px] font-bold text-grey-secondary">{words}</span>;
  }
  return (
    <RagChip tone={toneForRag(rag)} className="h-6 px-2.5 text-[12px]">
      {words}
    </RagChip>
  );
}

/**
 * A task's due date in bold ink, and a red "past due" chip once its day has gone
 * by on the day shown (and the tracker does not have it done). "—" with none.
 */
export function DueDate({
  deadline,
  trackerStatus,
  prefix = "",
}: {
  deadline: string | null | undefined;
  trackerStatus?: string | null;
  /** Words before the date, such as "Due ". */
  prefix?: string;
}) {
  const shownDay = useShownDay();
  if (!deadline) return <>—</>;
  const late = pastDue(deadline, shownDay, trackerStatus);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="font-bold text-ink">
        {prefix}
        {formatDay(deadline)}
      </span>
      {late ? (
        <RagChip tone="danger" className="h-5 px-2 text-[11px]">
          past due
        </RagChip>
      ) : null}
    </span>
  );
}

/**
 * An open blocker, the same everywhere: a danger chip with its age, whose dot
 * pulses once it has been open a week (the pulse stops for reduced motion).
 */
export function BlockerChip({
  description,
  ageDays,
  detail,
}: {
  description: string;
  ageDays: number | null;
  /** A work item or owner to name after the text. */
  detail?: string | null;
}) {
  const age = ageDays === null ? null : ageDays > 0 ? `${ageDays}d` : "new";
  return (
    <RagChip
      tone="danger"
      dot
      pulse={(ageDays ?? 0) >= 7}
      className="h-auto min-h-6 max-w-full whitespace-normal px-2.5 py-0.5 text-[12px]"
    >
      <span className="min-w-0">
        {description}
        {detail ? ` · ${detail}` : ""}
        {age ? ` · ${age}` : ""}
      </span>
    </RagChip>
  );
}

/** The greeting block every Today opens with. */
export function Greeting({ eyebrow, title, sub }: { eyebrow: string; title: string; sub: string }) {
  return (
    <div className="mb-6">
      <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
        {eyebrow}
      </p>
      <h1 className="mt-1 text-[34px] font-extrabold tracking-tight text-balance">{title}</h1>
      <p className="mt-1 text-[15px] text-grey-body">{sub}</p>
    </div>
  );
}

/** A row of selectable chips (pods, projects, brief kinds), keyboard-reachable buttons. */
export function ChipPicker<T extends string>({
  options,
  value,
  onChange,
  label,
  note,
}: {
  options: { value: T; label: string; rag?: Rag | null }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  note?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-9 items-center gap-2 rounded-full border px-4 text-[13px] font-bold",
              on
                ? "border-ink bg-ink text-white"
                : "border-grey-border bg-white text-grey-body hover:bg-grey-fill",
            )}
          >
            {option.rag !== undefined ? <RagDot rag={option.rag} /> : null}
            {option.label}
          </button>
        );
      })}
      {note ? <span className="text-[12px] text-grey-secondary">{note}</span> : null}
    </div>
  );
}

/** Completion as a ring; nothing known draws an empty ring, not 0% green. */
export function ProgressRing({ percent, size = 96 }: { percent: number | null; size?: number }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const value = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      role="img"
      aria-label={percent === null ? "Completion unknown" : `${Math.round(value)} percent complete`}
      className="flex-none"
    >
      <circle cx="50" cy="50" r={r} fill="none" stroke="var(--op-grey-fill)" strokeWidth="10" />
      <circle
        cx="50"
        cy="50"
        r={r}
        fill="none"
        stroke="var(--op-magenta)"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - value / 100)}
        transform="rotate(-90 50 50)"
      />
      <text x="50" y="57" textAnchor="middle" fontSize="20" fontWeight="800" fill="var(--op-black)">
        {percent === null ? "—" : `${Math.round(value)}%`}
      </text>
    </svg>
  );
}

/**
 * A small line over time with a faint baseline, area fill and an emphasised
 * endpoint. Values are 0..1 scores; a gap (null) breaks the line.
 */
export function Sparkline({
  values,
  label,
  color = "var(--op-magenta)",
}: {
  values: (number | null)[];
  label: string;
  color?: string;
}) {
  const w = 320;
  const h = 72;
  const pad = 6;
  const known = values.map((v, i) => ({ v, i })).filter((p) => p.v !== null) as {
    v: number;
    i: number;
  }[];
  if (known.length < 2) {
    return (
      <p className="text-[13px] text-grey-secondary">
        Not enough reported days to draw a line yet.
      </p>
    );
  }
  const x = (i: number) => pad + (i / Math.max(1, values.length - 1)) * (w - 2 * pad);
  const y = (v: number) => pad + (1 - v) * (h - 2 * pad);
  const path = known
    .map((p, k) => `${k === 0 ? "M" : "L"}${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`)
    .join(" ");
  const last = known[known.length - 1];
  const first = known[0];
  const area = `${path} L${x(last.i).toFixed(1)} ${h - pad} L${x(first.i).toFixed(1)} ${h - pad} Z`;
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="block h-auto w-full max-w-full"
      role="img"
      aria-label={label}
    >
      <line x1={pad} x2={w - pad} y1={h - pad} y2={h - pad} stroke="var(--op-grey-border)" />
      <path d={area} fill={color} opacity={0.12} />
      <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      <circle cx={x(last.i)} cy={y(last.v)} r={3.5} fill={color} />
    </svg>
  );
}

const ACCENT = {
  red: "border-l-4 border-l-rag-red pl-3",
  amber: "border-l-4 border-l-rag-amber pl-3",
};

/**
 * A list row: optional dot, title and meta on the left, something short on the
 * right. `accent` draws a bar on its left edge, for the rows that are bad news:
 * red for blocked work and blockers a week old, amber for work past due or
 * likely to be, and younger blockers.
 */
export function Row({
  rag,
  title,
  meta,
  right,
  accent,
  children,
}: {
  rag?: Rag | null;
  title: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
  accent?: "red" | "amber";
  /** More under the meta line, such as blocker chips. */
  children?: ReactNode;
}) {
  return (
    <li
      className={cn(
        "flex min-w-0 items-start gap-3 border-t border-grey-border py-3 first:border-t-0",
        accent ? ACCENT[accent] : null,
      )}
    >
      {rag !== undefined ? <RagDot rag={rag} className="mt-1.5" /> : null}
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-bold text-ink">{title}</p>
        {meta ? <p className="mt-0.5 text-[12px] text-grey-secondary">{meta}</p> : null}
        {children}
      </div>
      {right ? (
        <div className="flex-none text-right text-[12px] font-bold text-grey-secondary">
          {right}
        </div>
      ) : null}
    </li>
  );
}

/** A titled card holding a list or a block, with an optional note at the top right. */
export function Panel({
  title,
  note,
  children,
  className,
  variant = "white",
}: {
  title: ReactNode;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  variant?: "white" | "grey";
}) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-3xl p-5",
        variant === "white" ? "border border-grey-border bg-white" : "bg-grey-fill",
        className,
      )}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-extrabold">{title}</h2>
        {note ? <span className="text-[12px] text-grey-secondary">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}
