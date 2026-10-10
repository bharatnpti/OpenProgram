import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { PullRequestFlowResponse, RequestType } from "../../api/schema";
import { usePods, usePrograms, useProjects } from "../../app/directory";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { cn } from "../../lib/utils";
import { formatDay } from "../../lib/format";
import { plural } from "../../lib/words";
import { FlowBand } from "./FlowBand";
import { useReducedMotion } from "./useReducedMotion";
import {
  STAGE_DEFINITIONS,
  STAGE_LABELS,
  STAGES,
  TYPES,
  countsFor,
  formatHours,
  formatShare,
  investment,
  noStageTimeWords,
  noneWords,
  parseScope,
  requestReference,
  sampleWords,
  scopeWords,
  standingWords,
  stageValue,
  stagesFor,
  typeColorVar,
  typeLabel,
  worstJam,
  type Percentile,
} from "./prFlow";

const WINDOWS = [30, 90] as const;

/**
 * Signals › Flow: how long pull and merge requests spend coding, waiting for
 * review, in review and waiting to merge, and what kind of work they are. The
 * scope, window and percentile live in the URL; the backend times the stages.
 */
export function PrFlowSection({
  full,
  enabled,
  defaultScope = "all",
}: {
  full: boolean;
  enabled: boolean;
  /** The scope with no `?scope=`: the viewer's own pod or project, else "all". */
  defaultScope?: string;
}) {
  const [search, setSearch] = useSearchParams();
  const scopeValue = search.get("scope") ?? defaultScope;
  const scope = parseScope(scopeValue);
  const days = search.get("days") === "90" ? 90 : 30;
  const pct: Percentile = search.get("pct") === "p50" ? "p50" : "p75";
  const [paused, setPaused] = useState(false);
  const [highlight, setHighlight] = useState<RequestType | null>(null);
  const [asTable, setAsTable] = useState(false);

  const flow = useQuery({
    queryKey: ["portfolio", "pr-flow", days, scope.kind, scope.id],
    queryFn: () =>
      apiClient.pullRequestFlow({
        days,
        programId: scope.kind === "program" ? scope.id : null,
        projectId: scope.kind === "project" ? scope.id : null,
        podId: scope.kind === "pod" ? scope.id : null,
      }),
    enabled,
    placeholderData: (previous) => previous,
  });

  const set = (key: string, value: string | null) =>
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === null) next.delete(key);
        else next.set(key, value);
        if (!next.get("view")) next.set("view", "flow");
        return next;
      },
      { replace: true },
    );
  const toggleType = (type: RequestType) =>
    setHighlight((current) => (current === type ? null : type));

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {full ? (
        <Controls
          scope={scopeValue}
          days={days}
          pct={pct}
          asTable={asTable}
          onScope={(value) => set("scope", value === defaultScope ? null : value)}
          onDays={(value) => set("days", value === 30 ? null : String(value))}
          onPct={(value) => set("pct", value === "p75" ? null : value)}
          onTable={() => setAsTable((on) => !on)}
        />
      ) : null}
      <PanelState isLoading={flow.isLoading} error={flow.error} onRetry={() => void flow.refetch()}>
        {flow.data ? (
          <div
            className={cn(
              "grid grid-cols-[minmax(0,1fr)] gap-4 transition-opacity",
              flow.isFetching && flow.isPlaceholderData ? "opacity-60" : "",
            )}
          >
            <BandCard
              flow={flow.data}
              pct={pct}
              paused={paused}
              onTogglePause={() => setPaused((on) => !on)}
              highlight={highlight}
              onType={toggleType}
              full={full}
            />
            {full ? (
              <InvestmentCard flow={flow.data} highlight={highlight} onType={toggleType} />
            ) : null}
            {full && asTable ? (
              <FlowTables flow={flow.data} pct={pct} highlight={highlight} />
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}

function Segmented<T extends string | number>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex rounded-full border border-grey-border bg-white p-0.5"
    >
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cn(
            "h-8 rounded-full px-3.5 text-[13px] font-bold",
            option.value === value ? "bg-ink text-white" : "text-grey-body hover:bg-grey-fill",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Controls({
  scope,
  days,
  pct,
  asTable,
  onScope,
  onDays,
  onPct,
  onTable,
}: {
  scope: string;
  days: number;
  pct: Percentile;
  asTable: boolean;
  onScope: (value: string) => void;
  onDays: (value: number) => void;
  onPct: (value: Percentile) => void;
  onTable: () => void;
}) {
  const programs = usePrograms().data ?? [];
  const projects = useProjects().data ?? [];
  const pods = usePods().data ?? [];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex min-w-0 items-center gap-2 text-[13px] font-bold text-grey-body">
        <span className="sr-only sm:not-sr-only">Scope</span>
        <select
          value={scope}
          onChange={(event) => onScope(event.target.value)}
          className="h-9 max-w-[min(72vw,320px)] min-w-0 rounded-full border border-grey-border bg-white px-3 text-[13px] font-bold text-ink"
        >
          <option value="all">All repositories</option>
          {programs.length ? (
            <optgroup label="Programs">
              {programs.map((item) => (
                <option key={item.id} value={`program:${item.id}`}>
                  {item.name}
                </option>
              ))}
            </optgroup>
          ) : null}
          {projects.length ? (
            <optgroup label="Projects">
              {projects.map((item) => (
                <option key={item.id} value={`project:${item.id}`}>
                  {item.name}
                </option>
              ))}
            </optgroup>
          ) : null}
          {pods.length ? (
            <optgroup label="Pods">
              {pods.map((item) => (
                <option key={item.id} value={`pod:${item.id}`}>
                  {item.name}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
      </label>
      <Segmented
        label="Window"
        value={days}
        onChange={onDays}
        options={WINDOWS.map((value) => ({ value, label: `${value} days` }))}
      />
      <Segmented
        label="Percentile"
        value={pct}
        onChange={onPct}
        options={[
          { value: "p50", label: "p50" },
          { value: "p75", label: "p75" },
        ]}
      />
      <button
        type="button"
        aria-pressed={asTable}
        onClick={onTable}
        className={cn(
          "h-9 rounded-full border px-3.5 text-[13px] font-bold",
          asTable
            ? "border-ink bg-ink text-white"
            : "border-grey-border bg-white text-grey-body hover:bg-grey-fill",
        )}
      >
        Show as table
      </button>
    </div>
  );
}

function BandCard({
  flow,
  pct,
  paused,
  onTogglePause,
  highlight,
  onType,
  full,
}: {
  flow: PullRequestFlowResponse;
  pct: Percentile;
  paused: boolean;
  onTogglePause: () => void;
  highlight: RequestType | null;
  onType: (type: RequestType) => void;
  full: boolean;
}) {
  const drawn = flow.items.filter((item) => item.timed).length;
  const nothing = flow.merged_count === 0 && flow.open_count === 0;
  const still = useReducedMotion();
  // The picked type's own figures; every request's with none picked.
  const counts = countsFor(flow, highlight);
  const noTime =
    highlight === null || nothing
      ? null
      : noStageTimeWords(
          typeLabel(highlight),
          counts.merged,
          flow.window_days,
          stagesFor(flow, highlight),
        );
  return (
    <section
      aria-labelledby="pr-flow-title"
      className="op-flow min-w-0 rounded-3xl border border-(--op-flow-border) bg-(--op-flow-surface) p-4 text-(--op-flow-ink) sm:p-5"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 id="pr-flow-title" className="text-[17px] font-extrabold">
            Flow through review
          </h2>
          <p className="mt-0.5 text-[12px] text-(--op-flow-ink-2)">
            {scopeWords(flow.scope)} · last {flow.window_days} days
            {flow.as_of ? ` to ${formatDay(flow.as_of)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] text-(--op-flow-ink-2)">
            <span className="text-[15px] font-extrabold text-(--op-flow-ink)">
              {counts.merged.toLocaleString("en-GB")}
            </span>{" "}
            merged · {counts.open.toLocaleString("en-GB")} open
          </p>
          <span className="rounded-full border border-(--op-flow-border) px-2.5 py-1 text-[12px] font-bold text-(--op-flow-ink-2)">
            {pct}
          </span>
          {drawn > 0 && !still ? <PauseButton paused={paused} onToggle={onTogglePause} /> : null}
          {drawn > 0 && still ? (
            <span className="text-[12px] text-(--op-flow-ink-2)">
              Still: your device asks for less motion
            </span>
          ) : null}
        </div>
      </div>
      {nothing ? (
        <p className="rounded-2xl border border-dashed border-(--op-flow-border) px-4 py-6 text-[14px] text-(--op-flow-ink-2)">
          No request merged in these {flow.window_days} days and none is open
          {flow.scope.kind === "tenant" ? "" : " in this scope"}.
        </p>
      ) : (
        <FlowBand
          flow={flow}
          pct={pct}
          paused={paused}
          onTogglePause={onTogglePause}
          highlight={highlight}
        />
      )}
      {noTime ? <p className="mt-2 text-[12px] text-(--op-flow-ink-2)">{noTime}</p> : null}
      <Legend highlight={highlight} onType={onType} />
      <FlowNotes flow={flow} drawn={drawn} byType={highlight !== null} />
      {full ? (
        <details className="mt-3 text-[12px] text-(--op-flow-ink-2)">
          <summary className="cursor-pointer font-bold text-(--op-flow-ink)">
            What each stage measures
          </summary>
          <ul className="mt-2 grid gap-1">
            {STAGES.map((stage) => (
              <li key={stage}>
                <span className="font-bold text-(--op-flow-ink)">{STAGE_LABELS[stage]}</span>:{" "}
                {STAGE_DEFINITIONS[stage]}.
              </li>
            ))}
            <li>
              Times are the {pct === "p50" ? "median (p50)" : "75th percentile (p75)"} of the
              requests merged in the window, or of the picked type&apos;s requests when one is
              picked; the count beside a time is how many requests it rests on. Open requests are
              dots where they stand now. A request merged with no review counts for coding only.
            </li>
          </ul>
        </details>
      ) : (
        <p className="mt-3 text-[12px]">
          <Link to="/signals?view=flow">Types, scope and every request: Flow</Link>
        </p>
      )}
    </section>
  );
}

function PauseButton({ paused, onToggle }: { paused: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={paused}
      aria-keyshortcuts="Space"
      className="inline-flex h-8 items-center gap-1.5 rounded-full border border-(--op-flow-border) px-3 text-[12px] font-bold text-(--op-flow-ink) hover:bg-(--op-flow-grid)"
    >
      <span aria-hidden className="text-[11px]">
        {paused ? "▶" : "❚❚"}
      </span>
      {paused ? "Play" : "Pause"}
    </button>
  );
}

function Legend({
  highlight,
  onType,
}: {
  highlight: RequestType | null;
  onType: (type: RequestType) => void;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-(--op-flow-ink-2)">
      <span className="inline-flex items-center gap-2">
        flows freely
        <span
          aria-hidden
          className="inline-block h-1.5 w-16 rounded-full"
          style={{
            background:
              "linear-gradient(to right, var(--op-flow-free), var(--op-flow-busy), var(--op-flow-jam))",
          }}
        />
        jammed
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full bg-(--op-flow-ink-2)" />
        merged
        <span
          aria-hidden
          className="ml-2 inline-block h-2.5 w-2.5 rounded-full border-2 border-(--op-flow-ink-2)"
        />
        open now
      </span>
      <ul
        className="flex flex-wrap gap-1"
        aria-label="Request types: pick one to see its requests and its times in each stage"
      >
        {TYPES.map(({ type, label }) => (
          <li key={type}>
            <button
              type="button"
              aria-pressed={highlight === type}
              onClick={() => onType(type)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-full border px-2 font-bold",
                highlight === type
                  ? "border-(--op-flow-ink) text-(--op-flow-ink)"
                  : "border-transparent text-(--op-flow-ink-2) hover:border-(--op-flow-border)",
                highlight !== null && highlight !== type ? "opacity-55" : "",
              )}
            >
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: `var(${typeColorVar(type)})` }}
              />
              {label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FlowNotes({
  flow,
  drawn,
  byType,
}: {
  flow: PullRequestFlowResponse;
  drawn: number;
  /** A type is picked: the notes still cover every type, and say so. */
  byType: boolean;
}) {
  const notes = [...flow.notes];
  if (flow.items_truncated) {
    notes.push(
      `The newest ${plural(flow.items.length, "request is", "requests are")} drawn and listed; the times and counts cover all of them.`,
    );
  }
  if (drawn === 0 && (flow.merged_count > 0 || flow.open_count > 0)) {
    notes.push("No request here has its review history read yet, so there are no dots to draw.");
  }
  if (notes.length === 0) return null;
  return (
    <ul role="note" className="mt-3 grid gap-1 text-[12px] text-(--op-flow-ink-2)">
      {byType ? <li>These notes cover every type, not only the one picked.</li> : null}
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

function InvestmentCard({
  flow,
  highlight,
  onType,
}: {
  flow: PullRequestFlowResponse;
  highlight: RequestType | null;
  onType: (type: RequestType) => void;
}) {
  const { bars: rows, none } = investment(flow);
  const max = Math.max(1, ...rows.map((row) => row.merged));
  const noneLine = noneWords(none);
  return (
    <section
      aria-labelledby="pr-investment-title"
      className="op-flow min-w-0 rounded-3xl border border-(--op-flow-border) bg-(--op-flow-surface) p-4 text-(--op-flow-ink) sm:p-5"
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pr-investment-title" className="text-[17px] font-extrabold">
          Investment distribution
        </h2>
        <span className="text-[12px] text-(--op-flow-ink-2)">
          {plural(flow.merged_count, "request", "requests")} merged in {flow.window_days} days, by
          type
        </span>
      </div>
      <ul className="grid gap-1.5">
        {rows.map((row) => {
          const on = highlight === row.type;
          return (
            <li key={row.type}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onType(row.type)}
                aria-label={`${row.label}: ${row.merged} merged, ${formatShare(row.share)}${
                  row.open ? `, ${row.open} open` : ""
                }`}
                className={cn(
                  "grid w-full grid-cols-[minmax(0,9.75rem)_minmax(0,1fr)_auto] items-center gap-2.5 rounded-lg px-1.5 py-1 text-left hover:bg-(--op-flow-grid)/60 sm:grid-cols-[10rem_minmax(0,1fr)_auto]",
                  on ? "outline-2 outline-(--op-flow-ink)" : "",
                  highlight !== null && !on ? "opacity-55" : "",
                )}
              >
                <span className="flex min-w-0 items-center gap-2 text-[13px] font-bold">
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-2.5 flex-none rounded-[3px]"
                    style={{ background: `var(${typeColorVar(row.type)})` }}
                  />
                  <span className="truncate">{row.label}</span>
                </span>
                <span className="relative h-3.5" aria-hidden>
                  <span className="absolute inset-y-[6px] left-0 right-0 bg-(--op-flow-grid)" />
                  <span
                    className="animate-op-bar absolute inset-y-0 left-0 rounded-r-[4px]"
                    style={{
                      width: `${Math.max(1.5, (row.merged / max) * 100)}%`,
                      background: `var(${typeColorVar(row.type)})`,
                    }}
                  />
                </span>
                <span className="text-right text-[12px] tabular-nums text-(--op-flow-ink-2)">
                  <span className="font-extrabold text-(--op-flow-ink)">{row.merged}</span>{" "}
                  {formatShare(row.share)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {noneLine ? (
        <p className="mt-2 px-1.5 text-[12px] text-(--op-flow-muted)">{noneLine}</p>
      ) : null}
      <p className="mt-3 text-[12px] text-(--op-flow-ink-2)">
        A type comes from the first rule that names one: a dependency bot wrote it, the linked Jira
        issue&apos;s type, a label, a title prefix (feat:, fix:, docs: …), a branch prefix. Pick a
        type to see its requests and its times in each stage.
      </p>
    </section>
  );
}

function FlowTables({
  flow,
  pct,
  highlight,
}: {
  flow: PullRequestFlowResponse;
  pct: Percentile;
  highlight: RequestType | null;
}) {
  // The stage times follow the picked type, as the band does.
  const stages = stagesFor(flow, highlight);
  const jam = worstJam(flow, pct, highlight);
  const forType = highlight === null ? "" : ` for ${typeLabel(highlight)} requests`;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <TableBox>
        <table className="w-full min-w-[520px] border-collapse">
          <caption className="sr-only">Time in each stage{forType}</caption>
          <thead>
            <tr>
              <th className={th}>Stage</th>
              <th className={`${th} text-right`}>p50</th>
              <th className={`${th} text-right`}>p75</th>
              <th className={`${th} text-right`}>Merged, timed</th>
              <th className={`${th} text-right`}>Open now</th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <tr key={stage.stage}>
                <td className={td}>
                  <span className="font-bold">{stage.label}</span>
                  {jam === stage.stage ? (
                    <span className="ml-2 text-[12px] text-grey-secondary">worst jam at {pct}</span>
                  ) : null}
                </td>
                <td className={`${td} text-right tabular-nums`}>{formatHours(stage.p50_hours)}</td>
                <td className={`${td} text-right tabular-nums`}>{formatHours(stage.p75_hours)}</td>
                <td className={`${td} text-right tabular-nums`}>{stage.measured_count}</td>
                <td className={`${td} text-right tabular-nums`}>{stage.open_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      <TableBox>
        <table className="w-full min-w-[920px] border-collapse">
          <caption className="sr-only">
            Every request, merged ones newest first, then open ones longest waiting first
          </caption>
          <thead>
            <tr>
              <th className={th}>Request</th>
              <th className={th}>Type</th>
              <th className={th}>Author</th>
              <th className={th}>Now</th>
              {STAGES.map((stage) => (
                <th key={stage} className={`${th} text-right`}>
                  {STAGE_LABELS[stage]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {flow.items.map((item) => (
              <tr key={`${item.repo}!${item.number}`}>
                <td className={td}>
                  <span className="block max-w-[280px] font-bold">{item.title}</span>
                  <span className="text-[12px] text-grey-secondary">
                    {item.web_url ? (
                      <a href={item.web_url} target="_blank" rel="noreferrer">
                        {requestReference(item)}
                      </a>
                    ) : (
                      requestReference(item)
                    )}
                  </span>
                </td>
                <td className={td}>
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ background: `var(${typeColorVar(item.request_type)})` }}
                    />
                    {typeLabel(item.request_type)}
                  </span>
                </td>
                <td className={td}>{item.author_name ?? "—"}</td>
                <td className={td}>
                  {standingWords(item)}
                  {item.state === "merged" ? (
                    <span className="block text-[12px] text-grey-secondary">
                      {formatDay(item.merged_at)}
                    </span>
                  ) : null}
                </td>
                {STAGES.map((stage) => (
                  <td key={stage} className={`${td} text-right tabular-nums`}>
                    {formatHours(item.stage_hours[stage])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      <p className="text-[12px] text-grey-secondary">
        Stage times at {pct}
        {forType}:{" "}
        {stages
          .map((stage) => {
            const value = stageValue(stage, pct);
            const sample = sampleWords(value, stage.measured_count);
            return `${stage.label} ${formatHours(value)}${sample ? ` · ${sample}` : ""}`;
          })
          .join(", ")}
        . A dash is a stage the request has not finished, skipped, or whose history was not read.
      </p>
    </div>
  );
}
