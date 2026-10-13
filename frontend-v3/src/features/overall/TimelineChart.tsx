import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import type { TimelineResponse } from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS } from "../../components/viz/stages";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import {
  axisLabels,
  changeList,
  drawn,
  foldReleases,
  itemNote,
  longDay,
  moveFocus,
  openingScroll,
  rowDots,
  rowLine,
  rowShape,
  sentenceOf,
  tableRow,
  timeScale,
  verdictChip,
  xOf,
  type Dot,
  type Focus,
  type Row,
} from "./timeline";
import { Legend, Swatch } from "./viz";

/*
 * The timeline's drawing, to be read in five seconds (decision D78): per row a
 * grey track from planning to the committed date with a dark fill for what is
 * done, the likely finish as one segment in its verdict's colour (past the
 * committed date, with its dates, when it is late), the committed date as a
 * tick, and one dot on each day something changed, red when it pushed the
 * date. The row's label says it in one plain line. Labels are HTML (a column
 * on a wide screen, above each bar on a phone); the axis is SVG in its own box
 * that scrolls sideways when it must. Each dot is a button that says what
 * changed. The SVG is role="img" with the server's sentence for every row.
 */

const AXIS_H = 26;
const BAR_H = 12;
const DOT_R = 7;

const TONE: Record<BadgeTone, { fill: string; stroke: string }> = {
  success: { fill: "var(--op-green-bg)", stroke: "var(--op-green)" },
  warning: { fill: "var(--op-amber-bg)", stroke: "var(--op-amber)" },
  danger: { fill: "var(--op-red-bg)", stroke: "var(--op-red)" },
  info: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
  neutral: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
};

/** Below 640 px the labels sit above each bar, so the axis gets the card's width. */
function useNarrow(): boolean {
  const query = "(max-width: 639px)";
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches),
  );
  useEffect(() => {
    const list = window.matchMedia?.(query);
    if (!list) return;
    const changed = () => setNarrow(list.matches);
    list.addEventListener("change", changed);
    return () => list.removeEventListener("change", changed);
  }, []);
  return narrow;
}

export function TimelineChart({
  data,
  selected,
  onSelect,
  compact = false,
  actions,
  label = "Timeline",
  heading,
}: {
  data: TimelineResponse;
  /** The row Why the date moved explains (`row` id); highlighted. */
  selected?: string;
  onSelect?: (row: Row) => void;
  /** Delivery's panels: a shorter change list. */
  compact?: boolean;
  /** Set date or Change date beside a row, for whoever may. */
  actions?: (row: Row) => ReactNode;
  label?: string;
  /** A card's own title and links, on the toolbar's line (Delivery's panels). */
  heading?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  // The project's releases come folded under its row (DESIGN §5.2), open when one is picked.
  const [unfolded, setUnfolded] = useState(() => selected?.startsWith("release:") ?? false);
  const { rows: drawnRows, releases } = foldReleases(data.rows, unfolded);
  const drawnData = drawnRows === data.rows ? data : { ...data, rows: drawnRows };
  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {heading ? <div className="mr-auto min-w-0">{heading}</div> : null}
        {releases > 0 && !asTable ? (
          <button
            type="button"
            aria-expanded={unfolded}
            onClick={() => setUnfolded((now) => !now)}
            className="h-8 rounded-full border border-grey-border bg-(--op-viz-surface) px-3.5 text-[13px] font-bold text-grey-body hover:bg-grey-fill max-sm:h-10"
          >
            {unfolded
              ? releases === 1
                ? "Hide the release"
                : "Hide the releases"
              : releases === 1
                ? "Show its release"
                : `Show its ${releases} releases`}
          </button>
        ) : null}
        <button
          type="button"
          aria-pressed={asTable}
          onClick={() => setAsTable((now) => !now)}
          className={cn(
            "h-8 rounded-full border px-3.5 text-[13px] font-bold max-sm:h-10",
            asTable
              ? "border-ink bg-ink text-(--op-viz-surface)"
              : "border-grey-border bg-(--op-viz-surface) text-grey-body hover:bg-grey-fill",
          )}
        >
          Table
        </button>
      </div>
      {asTable ? (
        <TimelineTable data={data} label={label} />
      ) : (
        <Drawing
          data={drawnData}
          selected={selected}
          onSelect={onSelect}
          compact={compact}
          actions={actions}
          label={label}
        />
      )}
    </div>
  );
}

function Drawing({
  data,
  selected,
  onSelect,
  compact,
  actions,
  label,
}: {
  data: TimelineResponse;
  selected?: string;
  onSelect?: (row: Row) => void;
  compact: boolean;
  actions?: (row: Row) => ReactNode;
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  // A phone, or a Delivery panel's card: each label above its bar, so the axis gets the width.
  const narrow = useNarrow() || compact;
  const [available, setAvailable] = useState(0);
  const [open, setOpen] = useState<{ row: number; glyph: number } | null>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const glyphRefs = useRef<(HTMLButtonElement | null)[][]>([]);
  const acting = actions !== undefined && data.rows.some((row) => Boolean(actions(row)));
  // A wide screen: the label beside the bar. A phone: the label above it.
  const labelH = narrow ? (acting ? 66 : 58) : 0;
  const barY = narrow ? labelH + 16 : acting ? 26 : 22;
  const laneY = barY + BAR_H + 16;
  const rowH = narrow ? labelH + 58 : acting ? 100 : 78;

  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const measure = () => setAvailable(element.clientWidth);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, []);

  const scale = timeScale(data.axis, available);
  const rows = data.rows;
  const today = data.axis.today;
  const dots = rows.map((row) => rowDots(row, scale, data.change_label));
  const marks = drawn(rows, scale, today);
  const height = AXIS_H + rows.length * rowH;
  const todayX = xOf(scale, today);
  // A past day is drawn as of that day, and its line says which day it is.
  const todayLabel = useReadOnly().readOnly ? longDay(today) : "Today";

  // On a phone the box opens on today and the committed dates ahead of it.
  const opened = useRef(false);
  useEffect(() => {
    const element = box.current;
    if (!element || opened.current || available === 0) return;
    opened.current = true;
    if (element.scrollWidth > element.clientWidth) {
      element.scrollLeft = openingScroll(
        scale,
        today,
        element.clientWidth,
        rows.map((row) => row.committed.date),
      );
    }
  }, [available, scale, today, rows]);

  const focusOn = (focus: Focus) => {
    if (focus.glyph === null) {
      rowRefs.current[focus.row]?.focus();
      return;
    }
    glyphRefs.current[focus.row]?.[focus.glyph]?.focus();
  };
  const onKey = (focus: Focus) => (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      setOpen(null);
      return;
    }
    const next = moveFocus(
      focus,
      event.key,
      dots.map((list) => list.length),
    );
    if (next === null) return;
    event.preventDefault();
    focusOn(next);
  };
  const shown = open ? dots[open.row]?.[open.glyph] : undefined;
  const spoken = rows.map((row) => sentenceOf(data, row)).join(" ");
  const labels = rows.map((row, index) => (
    <RowLabel
      key={`${row.row}:${row.project_id}`}
      row={row}
      height={narrow ? labelH : rowH}
      narrow={narrow}
      selected={row.row === selected}
      buttonRef={(element) => {
        rowRefs.current[index] = element;
      }}
      // Only a row the reader has whole is explained below; a dates row is not.
      onSelect={row.access === "panel" ? onSelect : undefined}
      onKeyDown={onKey({ row: index, glyph: null })}
      action={actions?.(row)}
    />
  ));

  return (
    <figure className="m-0 grid min-w-0 gap-3">
      <div
        className={cn(
          "relative grid",
          narrow ? "grid-cols-[minmax(0,1fr)]" : "grid-cols-[280px_minmax(0,1fr)]",
        )}
      >
        {narrow ? null : (
          <div className="min-w-0 border-r border-(--op-viz-grid)">
            <div style={{ height: AXIS_H }} aria-hidden />
            {labels}
          </div>
        )}
        <div
          ref={box}
          className="relative min-w-0 overflow-x-auto overflow-y-hidden"
          tabIndex={0}
          role="region"
          aria-label={`${label}, scrolls sideways`}
        >
          <div className="relative" style={{ width: scale.width, height }}>
            <svg
              width={scale.width}
              height={height}
              viewBox={`0 0 ${scale.width} ${height}`}
              className="block overflow-visible"
              role="img"
              aria-label={spoken}
            >
              {rows.map((row, index) => {
                const top = AXIS_H + index * rowH;
                return (
                  <g key={`${row.row}:${row.project_id}`}>
                    {row.row === selected ? (
                      <rect
                        x={0}
                        y={top}
                        width={scale.width}
                        height={rowH}
                        fill="var(--op-grey-table-header)"
                      />
                    ) : null}
                    <line x1={0} x2={scale.width} y1={top} y2={top} stroke="var(--op-viz-grid)" />
                  </g>
                );
              })}
              {axisLabels(scale, { x: todayX, label: todayLabel }).map((tick) => (
                <g key={tick.label}>
                  {rows.map((row, index) => (
                    <line
                      key={row.row}
                      x1={tick.x}
                      x2={tick.x}
                      y1={AXIS_H + index * rowH + labelH}
                      y2={AXIS_H + (index + 1) * rowH}
                      stroke="var(--op-viz-grid)"
                    />
                  ))}
                  <text
                    x={tick.x}
                    y={14}
                    textAnchor={tick.anchor}
                    style={{ fill: "var(--op-grey-secondary)", fontSize: 11.5 }}
                  >
                    {tick.label}
                  </text>
                </g>
              ))}
              {rows.map((row, index) => (
                <RowDrawing
                  key={`${row.row}:${row.project_id}`}
                  row={row}
                  scale={scale}
                  today={today}
                  top={AXIS_H + index * rowH}
                  barY={barY}
                />
              ))}
              {/* Today: one thin line across the bars, its label above the axis. */}
              {rows.map((row, index) => (
                <line
                  key={row.row}
                  x1={todayX}
                  x2={todayX}
                  y1={AXIS_H + index * rowH + labelH + (index === 0 && !narrow ? -6 : 0)}
                  y2={AXIS_H + (index + 1) * rowH}
                  stroke="var(--op-black)"
                  strokeWidth={1}
                  strokeOpacity={0.55}
                />
              ))}
              <text
                x={todayX}
                y={14}
                textAnchor={todayX > scale.width - 40 ? "end" : todayX < 40 ? "start" : "middle"}
                style={{ fill: "var(--op-black)", fontSize: 11.5, fontWeight: 700 }}
              >
                {todayLabel}
              </text>
            </svg>
            {dots.map((list, rowIndex) =>
              list.map((dot, dotIndex) => (
                <DotButton
                  key={dot.id}
                  dot={dot}
                  top={AXIS_H + rowIndex * rowH + laneY}
                  pressed={open?.row === rowIndex && open.glyph === dotIndex}
                  buttonRef={(element) => {
                    glyphRefs.current[rowIndex] ??= [];
                    glyphRefs.current[rowIndex][dotIndex] = element;
                  }}
                  onOpen={() => setOpen({ row: rowIndex, glyph: dotIndex })}
                  onKeyDown={onKey({ row: rowIndex, glyph: dotIndex })}
                />
              )),
            )}
          </div>
        </div>
        {narrow ? (
          // On a phone each label sits above its bar, over the box, and does not scroll.
          <div className="pointer-events-none absolute inset-x-0 top-0">
            {labels.map((element, index) => (
              <div
                key={rows[index].row}
                className="absolute inset-x-0"
                style={{ top: AXIS_H + index * rowH, height: labelH }}
              >
                {element}
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div aria-live="polite">
        {shown ? (
          <DotList dot={shown} changeLabel={data.change_label} onClose={() => setOpen(null)} />
        ) : null}
      </div>
      <TimelineLegend marks={marks} />
      <WhatChanged rows={rows} changeLabel={data.change_label} compact={compact} />
    </figure>
  );
}

function RowLabel({
  row,
  height,
  narrow,
  selected,
  buttonRef,
  onSelect,
  onKeyDown,
  action,
}: {
  row: Row;
  height: number;
  narrow: boolean;
  selected: boolean;
  buttonRef: (element: HTMLButtonElement | null) => void;
  onSelect?: (row: Row) => void;
  onKeyDown: (event: KeyboardEvent) => void;
  action?: ReactNode;
}) {
  const chip = verdictChip(row.verdict);
  const line = rowLine(row);
  return (
    <div
      className={cn(
        "pointer-events-auto flex min-w-0 gap-2",
        narrow
          ? "items-start justify-between pt-1.5"
          : "flex-col justify-center gap-1 border-t border-(--op-viz-grid) py-1.5 pr-3",
        selected && !narrow && "bg-grey-header",
      )}
      style={{ height }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-pressed={onSelect ? selected : undefined}
        aria-label={`${row.name}: ${chip.label}. ${line.join(", ")}${onSelect ? ". Explain this row below" : ""}`}
        onClick={() => onSelect?.(row)}
        onKeyDown={onKeyDown}
        className="min-w-0 rounded-md text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <span
            title={row.name}
            className="truncate text-[14px] leading-tight font-extrabold text-ink max-sm:text-[13px]"
          >
            {row.name}
          </span>
          <RagChip tone={chip.tone} className="h-5 px-1.5 text-[10.5px]">
            {chip.label}
          </RagChip>
        </span>
        {/* One plain line; it breaks between its parts, never inside one. */}
        <span className="mt-0.5 block text-[12px] leading-snug text-grey-body">
          {line.map((part, index) => (
            <Fragment key={part}>
              {index > 0 ? " · " : ""}
              <span className="whitespace-nowrap">{part}</span>
            </Fragment>
          ))}
        </span>
      </button>
      {action ? <div className="flex flex-none">{action}</div> : null}
    </div>
  );
}

function RowDrawing({
  row,
  scale,
  today,
  top,
  barY,
}: {
  row: Row;
  scale: ReturnType<typeof timeScale>;
  today: string;
  top: number;
  barY: number;
}) {
  const shape = rowShape(row, scale, today);
  const y = top + barY;
  const likely = shape.likely;
  const tone = likely ? TONE[likely.tone] : null;
  // The likely dates beside a late segment: after it when there is room, else before it
  // when that keeps clear of the track, else just above it, ending where it ends.
  const labelWidth = likely ? likely.label.length * 6.2 : 0;
  const place = !likely
    ? null
    : likely.to + 6 + labelWidth <= scale.width
      ? { x: likely.to + 6, y: y + BAR_H - 1, anchor: "start" as const }
      : likely.from - 6 - labelWidth >= shape.end + 4
        ? { x: likely.from - 6, y: y + BAR_H - 1, anchor: "end" as const }
        : { x: likely.to, y: y - 6, anchor: "end" as const };
  return (
    <g>
      <rect
        x={shape.start}
        y={y}
        width={Math.max(2, shape.end - shape.start)}
        height={BAR_H}
        rx={3}
        fill="var(--op-grey-fill)"
        stroke="var(--op-viz-axis)"
        strokeDasharray={shape.open ? "4 3" : undefined}
      />
      {shape.fill !== null && shape.fill > shape.start ? (
        <rect
          x={shape.start}
          y={y}
          width={shape.fill - shape.start}
          height={BAR_H}
          rx={3}
          fill="var(--op-black)"
          opacity={0.85}
        />
      ) : null}
      {likely && tone ? (
        <>
          <rect
            x={likely.from}
            y={y - 2}
            width={likely.to - likely.from}
            height={BAR_H + 4}
            rx={4}
            fill={tone.fill}
            stroke={tone.stroke}
            strokeWidth={1.5}
          />
          {likely.late && place ? (
            <text
              x={place.x}
              y={place.y}
              textAnchor={place.anchor}
              style={{ fill: tone.stroke, fontSize: 11.5, fontWeight: 700 }}
            >
              {likely.label}
            </text>
          ) : null}
        </>
      ) : null}
      {/* Last, so the likely finish never hides the committed date. */}
      {shape.committed !== null ? (
        <rect
          x={shape.committed - 1.5}
          y={y - 5}
          width={3}
          height={BAR_H + 10}
          fill="var(--op-black)"
        />
      ) : null}
    </g>
  );
}

function DotButton({
  dot,
  top,
  pressed,
  buttonRef,
  onOpen,
  onKeyDown,
}: {
  dot: Dot;
  top: number;
  pressed: boolean;
  buttonRef: (element: HTMLButtonElement | null) => void;
  onOpen: () => void;
  onKeyDown: (event: KeyboardEvent) => void;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      aria-label={dot.label}
      aria-expanded={pressed}
      onFocus={onOpen}
      onMouseEnter={onOpen}
      onClick={onOpen}
      onKeyDown={onKeyDown}
      className={cn(
        "absolute grid place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ink",
        pressed && "ring-2 ring-ink ring-offset-1",
      )}
      style={{ left: dot.x - DOT_R, top: top - DOT_R, width: DOT_R * 2, height: DOT_R * 2 }}
    >
      <ChangeDot pushed={dot.pushed} count={dot.count} />
    </button>
  );
}

/** A change: red when it pushed the date, grey otherwise; its count inside when more than one. */
function ChangeDot({ pushed, count = 1 }: { pushed: boolean; count?: number }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid h-3.5 w-3.5 place-items-center rounded-full text-[9px] leading-none font-extrabold tabular-nums text-white",
        pushed ? "bg-(--op-red)" : "bg-(--op-grey-secondary)",
      )}
    >
      {count > 1 ? count : null}
    </span>
  );
}

function DotList({
  dot,
  changeLabel,
  onClose,
}: {
  dot: Dot;
  changeLabel: string;
  onClose: () => void;
}) {
  return (
    <div className="grid gap-1.5 rounded-2xl border border-grey-border bg-grey-fill px-3.5 py-3 text-[13px] text-grey-body">
      <div className="flex items-start justify-between gap-3">
        <p className="flex items-center gap-2 font-extrabold text-ink">
          <ChangeDot pushed={dot.pushed} />
          {dot.label.split(":")[0]}
          {dot.pushed ? <span className="font-bold text-(--op-red)">· pushed the date</span> : null}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="h-7 rounded-full px-2 text-[12px] font-bold text-grey-secondary hover:bg-grey-hover max-sm:h-9"
        >
          Close
        </button>
      </div>
      <ul className="m-0 grid list-none gap-1.5 p-0">
        {dot.entries.map((entry) => (
          <li key={`${entry.kind}:${entry.words}`} className="min-w-0">
            <span className="font-bold text-ink">{entry.words}</span>
            {entry.items.length > 0 ? (
              <ul className="m-0 mt-0.5 grid list-none gap-0.5 p-0 pl-3">
                {entry.items.slice(0, 10).map((item, index) => {
                  const note = itemNote(
                    item,
                    entry.kind,
                    changeLabel,
                    (stage) => STAGE_LABELS[stage],
                  );
                  return (
                    <li key={`${item.key}:${index}`}>
                      <b className="font-extrabold text-ink">{item.key}</b> {item.title}
                      {note ? <span className="text-grey-secondary"> · {note}</span> : null}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TimelineLegend({ marks }: { marks: ReturnType<typeof drawn> }) {
  return (
    <Legend>
      {marks.done ? (
        <span>
          <Swatch style={{ background: "var(--op-black)", opacity: 0.85 }} />
          Done
        </span>
      ) : null}
      {marks.toDo ? (
        <span>
          <Swatch
            style={{
              background: "var(--op-grey-fill)",
              boxShadow: "inset 0 0 0 1px var(--op-viz-axis)",
            }}
          />
          To do
        </span>
      ) : null}
      {marks.likely ? (
        <span>
          <span className="inline-flex gap-0.5">
            {marks.likelyTones.map((tone) => (
              <Swatch
                key={tone}
                style={{
                  background: TONE[tone].fill,
                  boxShadow: `inset 0 0 0 1.5px ${TONE[tone].stroke}`,
                }}
              />
            ))}
          </span>
          Likely finish
        </span>
      ) : null}
      {marks.committed ? (
        <span>
          <i aria-hidden className="inline-block h-3 w-[3px] bg-ink" />
          Committed date
        </span>
      ) : null}
      {marks.change ? (
        <span>
          <ChangeDot pushed />
          Change (red = pushed the date)
        </span>
      ) : null}
    </Legend>
  );
}

/** Under the chart: per row, what changed, newest first, in a few short lines. */
function WhatChanged({
  rows,
  changeLabel,
  compact,
}: {
  rows: Row[];
  changeLabel: string;
  compact: boolean;
}) {
  const lists = rows
    .map((row) => ({ row, list: changeList(row, changeLabel, compact ? 3 : 5) }))
    .filter(({ list }) => list.lead !== null || list.lines.length > 0);
  if (lists.length === 0) return null;
  return (
    <section aria-label="What changed" className="grid gap-2 border-t border-grey-border pt-3">
      <h4 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
        What changed
      </h4>
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-[repeat(auto-fit,minmax(260px,1fr))]">
        {lists.map(({ row, list }) => (
          <div key={`${row.row}:${row.project_id}`} className="grid content-start gap-1">
            <p className="text-[13px] font-extrabold text-ink">{row.name}</p>
            {list.lead ? <p className="text-[12.5px] text-grey-body">{list.lead}</p> : null}
            <ul className="m-0 grid list-none gap-0.5 p-0 text-[12.5px] text-grey-body">
              {list.lines.map((line) => (
                <li key={line.day} className="flex items-start gap-1.5">
                  <span className="mt-[3px] flex-none">
                    <ChangeDot pushed={line.pushed} />
                  </span>
                  <span className="min-w-0">{line.text}</span>
                </li>
              ))}
            </ul>
            {list.more > 0 ? (
              <p className="text-[12px] text-grey-secondary">
                and {list.more} earlier {list.more === 1 ? "day" : "days"} (Table lists them)
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

/** The table the drawing stands in for: per row its dates, done, and what changed by date. */
export function TimelineTable({ data, label }: { data: TimelineResponse; label: string }) {
  const rows = data.rows.map((row) => tableRow(row, data.change_label));
  return (
    <TableBox>
      <table className="w-full min-w-[760px] border-collapse">
        <caption className="sr-only">{label}, as a table</caption>
        <thead>
          <tr>
            {[
              "Row",
              "Start",
              "Committed",
              "Moves",
              "Likely finish",
              "Team's date",
              "Done",
              "What changed",
            ].map((head) => (
              <th key={head} scope="col" className={th}>
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.row}:${data.rows[index].project_id}`}>
              <th scope="row" className={`${td} text-left font-extrabold`}>
                {row.name}
              </th>
              <td className={td}>{row.start}</td>
              <td className={td}>{row.committed}</td>
              <td className={td}>{row.moves}</td>
              <td className={td}>{row.likely}</td>
              <td className={td}>{row.team}</td>
              <td className={td}>{row.done}</td>
              <td className={td}>
                {row.events.length > 0 ? (
                  <ul className="m-0 grid list-none gap-0.5 p-0">
                    {row.events.map((event) => (
                      <li key={event}>{event}</li>
                    ))}
                  </ul>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}
