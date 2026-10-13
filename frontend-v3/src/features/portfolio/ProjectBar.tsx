import type { PortfolioProjectDto } from "../../api/schema";
import { toneForVerdict, type BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { barOf, barSummary } from "./portfolioWords";

const band: Record<BadgeTone, string> = {
  success: "bg-rag-green",
  warning: "bg-rag-amber",
  danger: "bg-rag-red",
  info: "bg-rag-info",
  neutral: "bg-rag-unknown",
};

const pct = (value: number) => `${(value * 100).toFixed(2)}%`;

/**
 * A project's bar: time runs left to right from planning to the latest date.
 * The likely range (50% to 85%) in the verdict's colour, the committed date as
 * an ink tick, today as a dashed line, and a dot for each change: red where it
 * pushed the 85% date later, grey for any other. Below it, done of total. Drawn
 * once, read once: the picture's words are its aria-label.
 */
export function ProjectBar({ project, today }: { project: PortfolioProjectDto; today: string }) {
  const bar = barOf(project, today);
  const tone = project.verdict ? toneForVerdict(project.verdict) : "neutral";
  return (
    <div role="img" aria-label={barSummary(project)} className="grid gap-2">
      <div aria-hidden className="relative h-7">
        {bar.marks.map((mark) => (
          <span
            key={mark.key}
            title={mark.label}
            className={cn(
              "absolute top-0 h-2.5 w-2.5 -translate-x-1/2 rounded-full border-2 border-(--op-viz-surface)",
              mark.pushed ? "bg-rag-red" : "bg-rag-unknown",
            )}
            style={{ left: pct(mark.at) }}
          />
        ))}
        <span className="absolute inset-x-0 top-4 h-2.5 rounded-full bg-grey-fill" />
        {bar.likelyFrom !== null && bar.likelyTo !== null ? (
          <span
            className={cn("absolute top-4 h-2.5 rounded-full opacity-80", band[tone])}
            style={{
              left: pct(bar.likelyFrom),
              width: `max(6px, ${pct(Math.max(0, bar.likelyTo - bar.likelyFrom))})`,
            }}
          />
        ) : null}
        <span
          className="absolute top-3 h-4.5 border-l-2 border-dashed border-grey-secondary"
          style={{ left: pct(bar.today) }}
        />
        {bar.committed !== null ? (
          <span
            className="absolute top-2.5 h-5 w-[3px] -translate-x-1/2 rounded-sm bg-ink"
            style={{ left: pct(bar.committed) }}
          />
        ) : null}
      </div>
      <div aria-hidden className="flex items-center gap-2">
        <span className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-grey-fill">
          <span
            className="absolute inset-y-0 left-0 rounded-full bg-rag-green"
            style={{ width: pct(bar.done ?? 0) }}
          />
        </span>
        <span className="flex-none text-[12px] font-bold text-grey-secondary tabular-nums">
          {project.total > 0 ? `${project.done} of ${project.total} done` : "Nothing counted yet"}
        </span>
      </div>
    </div>
  );
}

/** What the bar's marks mean, said once at the end of the list. */
export function BarLegend() {
  const item = "inline-flex items-center gap-1.5";
  return (
    <ul
      className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-[12px] text-grey-secondary"
      aria-label="What the bars show"
    >
      <li className={item}>
        <span className="h-1.5 w-5 rounded-full bg-rag-green" aria-hidden /> Done
      </li>
      <li className={item}>
        <span className="h-4 w-[3px] rounded-sm bg-ink" aria-hidden /> Committed
      </li>
      <li className={item}>
        <span className="h-2.5 w-5 rounded-full bg-rag-amber opacity-80" aria-hidden /> Likely (50%
        – 85%)
      </li>
      <li className={item}>
        <span className="h-3.5 border-l-2 border-dashed border-grey-secondary" aria-hidden /> Today
      </li>
      <li className={item}>
        <span className="h-2.5 w-2.5 rounded-full bg-rag-red" aria-hidden /> Change that pushed the
        date
      </li>
      <li className={item}>
        <span className="h-2.5 w-2.5 rounded-full bg-rag-unknown" aria-hidden /> Other change
      </li>
    </ul>
  );
}
