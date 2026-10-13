import {
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
  drawn,
  foldReleases,
  glyphLegend,
  itemNote,
  longDay,
  moveFocus,
  movesLabel,
  openingScroll,
  progressLabel,
  rowGlyphs,
  rowShape,
  scopeLabel,
  sentenceOf,
  tableRow,
  timeScale,
  verdictChip,
  xOf,
  type Focus,
  type Glyph,
  type GlyphKind,
  type Row,
} from "./timeline";
import { Legend, Swatch } from "./viz";

/*
 * The timeline's drawing: row labels in HTML in a left column that never
 * scrolls, the time axis in SVG in its own box that scrolls sideways on a phone
 * (28 px a week at the least), and each marker an HTML button over the drawing,
 * so it takes focus, hover and a tap and says what it marks. The SVG is
 * role="img" and speaks the server's sentence for every row. Every colour is a
 * theme token, so the .op-viz dark remap reaches it; green, amber and red appear
 * only on the forecast band, as its verdict.
 */

const AXIS_H = 26;
const LANE_GAP = 4;

const TONE: Record<BadgeTone, { fill: string; stroke: string }> = {
  success: { fill: "var(--op-green-bg)", stroke: "var(--op-green)" },
  warning: { fill: "var(--op-amber-bg)", stroke: "var(--op-amber)" },
  danger: { fill: "var(--op-red-bg)", stroke: "var(--op-red)" },
  info: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
  neutral: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
};

export function TimelineChart({
  data,
  selected,
  onSelect,
  shade,
  compact = false,
  actions,
  label = "Timeline",
  heading,
}: {
  data: TimelineResponse;
  /** The row the waterfall below explains (`row` id); highlighted. */
  selected?: string;
  onSelect?: (row: Row) => void;
  /** The waterfall's period, shaded on the axis. */
  shade?: { from: string; to: string } | null;
  /** Delivery's panels: shorter rows, no scope line. */
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
          shade={shade ?? null}
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
  shade,
  compact,
  actions,
  label,
}: {
  data: TimelineResponse;
  selected?: string;
  onSelect?: (row: Row) => void;
  shade: { from: string; to: string } | null;
  compact: boolean;
  actions?: (row: Row) => ReactNode;
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(0);
  const [open, setOpen] = useState<{ row: number; glyph: number } | null>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const glyphRefs = useRef<(HTMLButtonElement | null)[][]>([]);
  // A row with Set date or Change date under its label needs the room for it.
  const acting = actions !== undefined && data.rows.some((row) => Boolean(actions(row)));
  const rowH = compact ? 62 : acting ? 96 : 72;
  const laneY = compact ? 44 : acting ? 58 : 50;
  const barY = compact ? 16 : acting ? 26 : 20;

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
  const glyphs = rows.map((row) => rowGlyphs(row, scale, data.change_label));
  const marks = drawn(rows, scale, data.change_label);
  const height = AXIS_H + rows.length * rowH;
  const todayX = xOf(scale, data.axis.today);
  // A past day is drawn as of that day, and its line says which day it is.
  const todayLabel = useReadOnly().readOnly ? longDay(data.axis.today) : "Today";

  // On a phone the box opens on today and the committed dates ahead of it.
  const opened = useRef(false);
  useEffect(() => {
    const element = box.current;
    if (!element || opened.current || available === 0) return;
    opened.current = true;
    if (window.matchMedia?.("(max-width: 639px)").matches) {
      element.scrollLeft = openingScroll(
        scale,
        data.axis.today,
        element.clientWidth,
        data.rows.map((row) => row.committed.date),
      );
    }
  }, [available, scale, data.axis.today, data.rows]);

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
      glyphs.map((list) => list.length),
    );
    if (next === null) return;
    event.preventDefault();
    focusOn(next);
  };
  const shown = open ? glyphs[open.row]?.[open.glyph] : undefined;
  const spoken = rows.map((row) => sentenceOf(data, row)).join(" ");

  return (
    <figure className="m-0 grid min-w-0 gap-3">
      <div className="grid grid-cols-[120px_minmax(0,1fr)] sm:grid-cols-[208px_minmax(0,1fr)]">
        <div className="min-w-0 border-r border-(--op-viz-grid)">
          <div style={{ height: AXIS_H }} aria-hidden />
          {rows.map((row, index) => (
            <RowLabel
              key={`${row.row}:${row.project_id}`}
              row={row}
              height={rowH}
              compact={compact}
              selected={row.row === selected}
              buttonRef={(element) => {
                rowRefs.current[index] = element;
              }}
              // Only a row the reader has whole is explained below; a dates row is not.
              onSelect={row.access === "panel" ? onSelect : undefined}
              onKeyDown={onKey({ row: index, glyph: null })}
              action={actions?.(row)}
            />
          ))}
        </div>
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
              {shade ? (
                <rect
                  x={xOf(scale, shade.from)}
                  y={0}
                  width={Math.max(2, xOf(scale, shade.to) - xOf(scale, shade.from))}
                  height={height}
                  fill="var(--op-grey-fill)"
                  opacity={0.7}
                />
              ) : null}
              {axisLabels(scale, { x: todayX, label: todayLabel }).map((tick) => (
                <g key={tick.label}>
                  <line
                    x1={tick.x}
                    x2={tick.x}
                    y1={AXIS_H - 6}
                    y2={height}
                    stroke="var(--op-viz-grid)"
                  />
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
                    <RowDrawing row={row} scale={scale} top={top} barY={barY} laneY={laneY} />
                  </g>
                );
              })}
              <line
                x1={todayX}
                x2={todayX}
                y1={AXIS_H - 8}
                y2={height}
                stroke="var(--op-black)"
                strokeWidth={1.2}
                strokeDasharray="2 3"
              />
              <text
                x={todayX}
                y={AXIS_H - 10}
                textAnchor={todayX > scale.width - 40 ? "end" : todayX < 40 ? "start" : "middle"}
                style={{ fill: "var(--op-black)", fontSize: 11.5, fontWeight: 700 }}
              >
                {todayLabel}
              </text>
            </svg>
            {glyphs.map((list, rowIndex) =>
              list.map((glyph, glyphIndex) => (
                <GlyphButton
                  key={glyph.id}
                  glyph={glyph}
                  top={AXIS_H + rowIndex * rowH + laneY}
                  pressed={open?.row === rowIndex && open.glyph === glyphIndex}
                  buttonRef={(element) => {
                    glyphRefs.current[rowIndex] ??= [];
                    glyphRefs.current[rowIndex][glyphIndex] = element;
                  }}
                  onOpen={() => setOpen({ row: rowIndex, glyph: glyphIndex })}
                  onKeyDown={onKey({ row: rowIndex, glyph: glyphIndex })}
                />
              )),
            )}
          </div>
        </div>
      </div>
      <div aria-live="polite">
        {shown ? (
          <MarkerList glyph={shown} changeLabel={data.change_label} onClose={() => setOpen(null)} />
        ) : marks.glyphs.length > 0 ? (
          <p className="text-[12px] text-grey-secondary">
            A marker opens its requirements: hover, tap or move to it with the arrow keys.
          </p>
        ) : null}
      </div>
      <TimelineLegend marks={marks} changeLabel={data.change_label} />
    </figure>
  );
}

function RowLabel({
  row,
  height,
  compact,
  selected,
  buttonRef,
  onSelect,
  onKeyDown,
  action,
}: {
  row: Row;
  height: number;
  compact: boolean;
  selected: boolean;
  buttonRef: (element: HTMLButtonElement | null) => void;
  onSelect?: (row: Row) => void;
  onKeyDown: (event: KeyboardEvent) => void;
  action?: ReactNode;
}) {
  const chip = verdictChip(row.verdict);
  const whole = row.access === "panel";
  const detail = whole && !compact ? (scopeLabel(row) ?? movesLabel(row)) : movesLabel(row);
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col justify-center gap-1 border-t border-(--op-viz-grid) py-1.5 pr-2",
        selected && "bg-grey-header",
      )}
      style={{ height }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-pressed={onSelect ? selected : undefined}
        aria-label={`${row.name}${row.scope_kind === "pod" && compact ? ` for ${row.project_name}` : ""}: ${chip.label}${whole && row.progress ? `, ${progressLabel(row)} done` : ""}${onSelect ? ". Explain this row below" : ""}`}
        onClick={() => onSelect?.(row)}
        onKeyDown={onKeyDown}
        className="min-w-0 rounded-md text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        {/* Two lines on a phone rather than a cut name; the full name on hover. */}
        <span
          title={row.name}
          className="line-clamp-2 break-words text-[13.5px] leading-tight font-extrabold text-ink max-sm:text-[12.5px]"
        >
          {row.name}
        </span>
        <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <RagChip tone={chip.tone} className="h-5 max-w-full truncate px-1.5 text-[10.5px]">
            {chip.label}
          </RagChip>
          {whole && row.progress ? (
            <span className="text-[11.5px] font-bold tabular-nums text-grey-body">
              <span className="max-sm:hidden">{progressLabel(row)}</span>
              <span className="sm:hidden">{progressLabel(row, true)}</span>
            </span>
          ) : null}
        </span>
        {detail ? (
          <span className="mt-0.5 block truncate text-[11px] text-grey-secondary max-sm:hidden">
            {detail}
          </span>
        ) : null}
      </button>
      {action ? <div className="flex">{action}</div> : null}
    </div>
  );
}

function RowDrawing({
  row,
  scale,
  top,
  barY,
  laneY,
}: {
  row: Row;
  scale: ReturnType<typeof timeScale>;
  top: number;
  barY: number;
  laneY: number;
}) {
  const shape = rowShape(row, scale);
  const y = top + barY;
  const h = 12;
  const tone = shape.band ? TONE[shape.band.tone] : null;
  const arrowFrom = shape.earlier.length > 0 ? shape.earlier[shape.earlier.length - 1].x : null;
  return (
    <g>
      {/* The span: planning to the committed date, faded without a baseline, open without a date. */}
      <rect
        x={shape.start}
        y={y}
        width={Math.max(2, shape.end - shape.start)}
        height={h}
        rx={3}
        fill="var(--op-grey-fill)"
        stroke="var(--op-viz-axis)"
        strokeDasharray={shape.open ? "4 3" : undefined}
        opacity={shape.faded ? 0.55 : 1}
      />
      {shape.fill !== null ? (
        <rect
          x={shape.start}
          y={y}
          width={Math.max(0, shape.fill - shape.start)}
          height={h}
          rx={3}
          fill="var(--op-black)"
          opacity={shape.faded ? 0.55 : 0.85}
        />
      ) : null}
      {shape.band && tone ? (
        <rect
          x={shape.band.from}
          y={y - 3}
          width={shape.band.to - shape.band.from}
          height={h + 6}
          rx={3}
          fill={tone.fill}
          stroke={tone.stroke}
          strokeWidth={1.5}
          opacity={0.92}
        />
      ) : null}
      {shape.reference !== null ? (
        <line
          x1={shape.reference}
          x2={shape.reference}
          y1={y - 8}
          y2={y + h + 8}
          stroke="var(--op-grey-secondary)"
          strokeWidth={1.5}
          strokeDasharray="1 2"
        />
      ) : null}
      {shape.jira !== null ? (
        <line
          x1={shape.jira}
          x2={shape.jira}
          y1={y - 6}
          y2={y + h + 6}
          stroke="var(--op-black)"
          strokeWidth={1.5}
          strokeDasharray="1.5 2"
        />
      ) : null}
      {shape.earlier.map((tick) => (
        <rect
          key={`${tick.date}:${tick.on}`}
          x={tick.x - 2}
          y={y - 5}
          width={4}
          height={h + 10}
          rx={1}
          fill="var(--op-viz-surface)"
          stroke="var(--op-black)"
          strokeWidth={1.2}
        />
      ))}
      {arrowFrom !== null &&
      shape.committed !== null &&
      Math.abs(shape.committed - arrowFrom) > 6 ? (
        <path
          d={arrowPath(arrowFrom, shape.committed, y - 8)}
          fill="none"
          stroke="var(--op-black)"
          strokeWidth={1}
        />
      ) : null}
      {shape.team ? (
        <path
          d={`M${shape.team.x} ${y - 1} l5 ${h / 2 + 1} l-5 ${h / 2 + 1} l-5 ${-(h / 2 + 1)} Z`}
          fill={shape.team.hollow ? "var(--op-viz-surface)" : "var(--op-black)"}
          stroke="var(--op-black)"
          strokeWidth={1.4}
        />
      ) : null}
      {/* Last, so a team's date on the same day never hides the committed date. */}
      {shape.committed !== null ? (
        <rect
          x={shape.committed - 1.5}
          y={y - 6}
          width={3}
          height={h + 12}
          fill="var(--op-black)"
        />
      ) : null}
      {row.access === "panel" ? (
        <line
          x1={shape.start}
          x2={shape.open ? scale.width : Math.max(shape.end, shape.start + 2)}
          y1={top + laneY}
          y2={top + laneY}
          stroke="var(--op-viz-grid)"
          strokeWidth={1}
          transform={`translate(0 ${LANE_GAP})`}
        />
      ) : null}
      {/* The open end says "no date", unless the row's chip says it already. */}
      {shape.open && row.verdict !== "no_date" ? (
        <text
          x={scale.width - 4}
          y={y + h + 14}
          textAnchor="end"
          style={{ fill: "var(--op-grey-secondary)", fontSize: 11 }}
        >
          no date
        </text>
      ) : null}
      {shape.faded ? (
        <text
          x={shape.start + 4}
          y={y - 4}
          style={{ fill: "var(--op-grey-secondary)", fontSize: 10.5 }}
        >
          no baseline
        </text>
      ) : null}
    </g>
  );
}

/** A thin arrow from the last earlier committed date to the current one, above the bar. */
function arrowPath(from: number, to: number, y: number): string {
  const head = to > from ? -4 : 4;
  return `M${from} ${y} L${to} ${y} M${to + head} ${y - 3} L${to} ${y} L${to + head} ${y + 3}`;
}

function GlyphButton({
  glyph,
  top,
  pressed,
  buttonRef,
  onOpen,
  onKeyDown,
}: {
  glyph: Glyph;
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
      aria-label={glyph.label}
      aria-expanded={pressed}
      onFocus={onOpen}
      onMouseEnter={onOpen}
      onClick={onOpen}
      onKeyDown={onKeyDown}
      className={cn(
        "absolute grid h-5 w-5 place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-ink max-sm:h-6 max-sm:w-6",
        pressed && "bg-grey-fill",
      )}
      style={{ left: glyph.x - 10, top: top - 10 + LANE_GAP }}
    >
      <GlyphMark kind={glyph.kind} ringed={glyph.ringed} />
      {glyph.count > 1 ? (
        <span
          aria-hidden
          className="pointer-events-none absolute -right-1.5 -top-1.5 text-[9.5px] font-extrabold tabular-nums text-ink"
        >
          {glyph.count}
        </span>
      ) : null}
    </button>
  );
}

/** Each marker kind's own shape, in ink: never colour alone. */
export function GlyphMark({ kind, ringed = false }: { kind: GlyphKind; ringed?: boolean }) {
  const ink = "var(--op-black)";
  const surface = "var(--op-viz-surface)";
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" aria-hidden className="block overflow-visible">
      {ringed ? (
        <circle
          cx={8}
          cy={8.5}
          r={8}
          fill="none"
          stroke={ink}
          strokeWidth={1}
          strokeDasharray="2 1.5"
        />
      ) : null}
      {kind === "added_label" ? <path d="M8 2.5 L13.5 12.5 L2.5 12.5 Z" fill={ink} /> : null}
      {kind === "added" ? (
        <path d="M8 3 L13 12.2 L3 12.2 Z" fill={surface} stroke={ink} strokeWidth={1.6} />
      ) : null}
      {kind === "removed" ? <path d="M2.5 3.5 L13.5 3.5 L8 13.5 Z" fill={ink} /> : null}
      {kind === "reopened" ? (
        <path
          d="M12.5 8 A4.5 4.5 0 1 1 8 3.5 M8 3.5 L10.5 1.8 M8 3.5 L10.2 5.6"
          fill="none"
          stroke={ink}
          strokeWidth={1.6}
          strokeLinecap="round"
        />
      ) : null}
      {kind === "late" ? (
        <>
          <line x1={4} y1={14} x2={4} y2={2} stroke={ink} strokeWidth={1.6} />
          <path d="M4 2.5 L12.5 5 L4 7.8 Z" fill={ink} />
        </>
      ) : null}
      {kind === "bug" ? (
        <path
          d="M3.5 3.5 L12.5 12.5 M12.5 3.5 L3.5 12.5"
          stroke={ink}
          strokeWidth={2}
          strokeLinecap="round"
        />
      ) : null}
      {kind === "blocker_opened" ? <circle cx={8} cy={8} r={4.5} fill={ink} /> : null}
      {kind === "blocker_closed" ? (
        <circle cx={8} cy={8} r={4.2} fill={surface} stroke={ink} strokeWidth={1.6} />
      ) : null}
    </svg>
  );
}

function MarkerList({
  glyph,
  changeLabel,
  onClose,
}: {
  glyph: Glyph;
  changeLabel: string;
  onClose: () => void;
}) {
  return (
    <div className="grid gap-1.5 rounded-2xl border border-grey-border bg-grey-fill px-3.5 py-3 text-[13px] text-grey-body">
      <div className="flex items-start justify-between gap-3">
        <p className="flex items-center gap-2 font-extrabold text-ink">
          <GlyphMark kind={glyph.kind} ringed={glyph.ringed} />
          {glyph.label}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="h-7 rounded-full px-2 text-[12px] font-bold text-grey-secondary hover:bg-grey-hover max-sm:h-9"
        >
          Close
        </button>
      </div>
      {glyph.items.length > 0 ? (
        <ul className="m-0 grid list-none gap-1 p-0">
          {glyph.items.map((item, index) => {
            const note = itemNote(item, glyph.kind, changeLabel, (stage) => STAGE_LABELS[stage]);
            return (
              <li key={`${item.key}:${index}`} className="min-w-0">
                <b className="font-extrabold text-ink">{item.key}</b> {item.title}
                {note ? <span className="text-grey-secondary"> · {note}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {glyph.more > 0 ? (
        <p className="text-[12px] text-grey-secondary">and {glyph.more} more</p>
      ) : null}
    </div>
  );
}

function TimelineLegend({
  marks,
  changeLabel,
}: {
  marks: ReturnType<typeof drawn>;
  changeLabel: string;
}) {
  return (
    <Legend>
      {marks.done ? (
        <span>
          <Swatch style={{ background: "var(--op-black)", opacity: 0.85 }} />
          Done
        </span>
      ) : null}
      {marks.committed || marks.open ? (
        <span>
          <Swatch
            style={{
              background: "var(--op-grey-fill)",
              boxShadow: "inset 0 0 0 1px var(--op-viz-axis)",
            }}
          />
          To go
        </span>
      ) : null}
      {marks.band ? (
        <span>
          <span className="inline-flex gap-0.5">
            {marks.bandTones.map((tone) => (
              <Swatch
                key={tone}
                style={{
                  background: TONE[tone].fill,
                  boxShadow: `inset 0 0 0 1.5px ${TONE[tone].stroke}`,
                }}
              />
            ))}
          </span>
          Forecast, 50% to 85% likely, in its verdict&apos;s colour
        </span>
      ) : null}
      {marks.committed ? (
        <span>
          <i aria-hidden className="inline-block h-3 w-[3px] bg-ink" />
          Committed date
        </span>
      ) : null}
      {marks.earlier ? (
        <span>
          <i aria-hidden className="inline-block h-3 w-1 rounded-[1px] border border-ink" />
          Earlier committed date
        </span>
      ) : null}
      {marks.team ? (
        <span>
          <i aria-hidden className="inline-block h-2 w-2 rotate-45 bg-ink" />
          Team&apos;s latest date{marks.teamHollow ? " (hollow: some have none)" : ""}
        </span>
      ) : null}
      {marks.jira ? (
        <span>
          <i aria-hidden className="inline-block h-3 w-0 border-l-2 border-dotted border-ink" />
          Jira release date
        </span>
      ) : null}
      {marks.reference ? (
        <span>
          <i
            aria-hidden
            className="inline-block h-3 w-0 border-l-2 border-dotted border-grey-secondary"
          />
          The project&apos;s date
        </span>
      ) : null}
      {marks.open ? (
        <span>
          <i
            aria-hidden
            className="inline-block h-0 w-[18px] border-t-2 border-dashed border-grey-secondary"
          />
          No committed date
        </span>
      ) : null}
      {marks.faded ? <span>Faded: no planning baseline</span> : null}
      {marks.glyphs.map((kind) => (
        <span key={kind}>
          <GlyphMark kind={kind} />
          {glyphLegend(kind, changeLabel)}
        </span>
      ))}
      {marks.ringed ? (
        <span>
          <GlyphMark kind="added" ringed />
          Open in Jira hygiene
        </span>
      ) : null}
    </Legend>
  );
}

/** The table the drawing stands in for: per row its dates, done, and events by date. */
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
              "50%",
              "85%",
              "Team's date",
              "Done",
              "Events",
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
              <td className={td}>{row.p50}</td>
              <td className={td}>{row.p85}</td>
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
