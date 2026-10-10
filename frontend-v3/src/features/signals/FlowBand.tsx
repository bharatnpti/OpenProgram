import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

import type { PullRequestFlowResponse, RequestType } from "../../api/schema";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import {
  STAGE_LABELS,
  STAGES,
  bandProfile,
  bandSummary,
  dotX,
  formatHours,
  leadHours,
  planDots,
  requestReference,
  sampleWords,
  stagesFor,
  standingWords,
  thicknessAt,
  typeColorVar,
  typeLabel,
  typeSourceWords,
  worstJam,
  type Dot,
  type Percentile,
} from "./prFlow";
import { useReducedMotion } from "./useReducedMotion";

/*
 * The flow band: four stages left to right, the band narrowing where work
 * jams, one dot per request coloured by its type. Merged requests travel it,
 * lingering where they waited; open ones stand where they are now. SVG draws
 * the band and its words, a canvas the dots (hundreds of moving SVG circles
 * would cost a layout per frame).
 */

// Room for the stage names and times above the band. A narrow band stacks the
// request count under each time (and a name may wrap), so it keeps more.
const LABELS_H = 50;
const LABELS_H_NARROW = 82;
const MARKER_H = 40;
const DOT_R = 4;
const HIT_R = 12;

type Palette = Record<string, string>;

function readPalette(element: HTMLElement): Palette {
  const style = getComputedStyle(element);
  const names = [
    "--op-flow-surface",
    "--op-flow-ink",
    "--op-flow-type-none",
    ...[1, 2, 3, 4, 5, 6, 7, 8].map((slot) => `--op-flow-type-${slot}`),
  ];
  return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()]));
}

function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setWidth(Math.round(element.getBoundingClientRect().width));
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, []);
  return [ref, width];
}

export function FlowBand({
  flow,
  pct,
  paused,
  onTogglePause,
  highlight,
}: {
  flow: PullRequestFlowResponse;
  pct: Percentile;
  paused: boolean;
  onTogglePause: () => void;
  /**
   * A type the legend picked: its dots stay lit, the others fade, and the stage
   * times and the band's thickness are its own.
   */
  highlight: RequestType | null;
}) {
  const [box, width] = useWidth();
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();
  const helpId = useId();
  const gradientId = useId().replace(/:/g, "");
  const narrow = width > 0 && width < 560;
  const labelsH = narrow ? LABELS_H_NARROW : LABELS_H;
  const bandH = narrow ? 120 : 150;
  const height = labelsH + bandH + MARKER_H;
  const centerY = labelsH + bandH / 2;

  const profile = useMemo(
    () => bandProfile(stagesFor(flow, highlight), pct),
    [flow, highlight, pct],
  );
  const dots = useMemo(() => planDots(flow, pct), [flow, pct]);
  const jam = worstJam(flow, pct, highlight);
  const jamIndex = jam ? STAGES.indexOf(jam) : -1;

  // The animation clock: seconds of motion so far. It only runs while nothing
  // holds it: not paused, not asked for less motion, no dot pointed at.
  const elapsed = useRef(7.3);
  const [active, setActive] = useState<{ key: string; by: "pointer" | "keyboard" } | null>(null);
  const held = paused || reduced || active !== null;
  const palette = useRef<Palette>({});

  const position = useCallback(
    (dot: Dot, seconds: number) => {
      const x01 = dotX(dot, seconds);
      const half = (thicknessAt(profile, x01) * bandH) / 2 - DOT_R - 2;
      return { x: 6 + x01 * (width - 12), y: centerY + dot.lane * Math.max(2, half) };
    },
    [profile, bandH, width, centerY],
  );

  const draw = useCallback(() => {
    const element = canvas.current;
    if (!element || width === 0) return;
    const ratio = window.devicePixelRatio || 1;
    if (
      element.width !== Math.round(width * ratio) ||
      element.height !== Math.round(height * ratio)
    ) {
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
    }
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const colors = palette.current;
    const surface = colors["--op-flow-surface"] || "#ffffff";
    for (const dot of dots) {
      const { x, y } = position(dot, elapsed.current);
      const color = colors[typeColorVar(dot.item.request_type)] || "#888888";
      const faded = highlight !== null && dot.item.request_type !== highlight;
      const isActive = active?.key === dot.key;
      context.globalAlpha = faded ? 0.14 : 1;
      context.beginPath();
      context.arc(x, y, isActive ? DOT_R + 2.5 : DOT_R, 0, Math.PI * 2);
      if (dot.open) {
        // Open now: a ring, so "still waiting" never rests on colour alone.
        context.fillStyle = surface;
        context.fill();
        context.lineWidth = 2;
        context.strokeStyle = color;
        context.stroke();
      } else {
        context.lineWidth = 1.5;
        context.strokeStyle = surface;
        context.stroke();
        context.fillStyle = color;
        context.fill();
      }
      if (isActive) {
        context.globalAlpha = 1;
        context.lineWidth = 2;
        context.strokeStyle = colors["--op-flow-ink"] || "#000000";
        context.beginPath();
        context.arc(x, y, DOT_R + 4.5, 0, Math.PI * 2);
        context.stroke();
      }
    }
    context.globalAlpha = 1;
  }, [dots, position, width, height, highlight, active]);

  // The theme's colours, read again when the document's theme changes.
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const refresh = () => {
      palette.current = readPalette(element);
      draw();
    };
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, [box, draw]);

  useEffect(() => {
    if (held) {
      draw();
      return;
    }
    let frame = 0;
    let last: number | null = null;
    const tick = (now: number) => {
      // A hidden tab spends nothing; time does not jump when it comes back.
      if (last !== null && !document.hidden) elapsed.current += Math.min(0.05, (now - last) / 1000);
      last = now;
      draw();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [held, draw]);

  const nearest = useCallback(
    (px: number, py: number): Dot | null => {
      let best: Dot | null = null;
      let bestDistance = HIT_R * HIT_R;
      for (const dot of dots) {
        if (highlight !== null && dot.item.request_type !== highlight) continue;
        const { x, y } = position(dot, elapsed.current);
        const distance = (x - px) ** 2 + (y - py) ** 2;
        if (distance <= bestDistance) {
          best = dot;
          bestDistance = distance;
        }
      }
      return best;
    },
    [dots, position, highlight],
  );

  const ordered = useCallback(
    () =>
      dots
        .filter((dot) => highlight === null || dot.item.request_type === highlight)
        .map((dot) => ({ dot, x: position(dot, elapsed.current).x }))
        .sort((a, b) => a.x - b.x)
        .map((entry) => entry.dot),
    [dots, highlight, position],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === " " || event.key === "k" || event.key === "K") {
      event.preventDefault();
      onTogglePause();
      return;
    }
    if (event.key === "Escape") {
      setActive(null);
      return;
    }
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const list = ordered();
    if (list.length === 0) return;
    const at = active ? list.findIndex((dot) => dot.key === active.key) : -1;
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next =
      at === -1 ? (step === 1 ? 0 : list.length - 1) : (at + step + list.length) % list.length;
    setActive({ key: list[next].key, by: "keyboard" });
  };

  const activeDot = active ? (dots.find((dot) => dot.key === active.key) ?? null) : null;
  const activePoint = activeDot ? position(activeDot, elapsed.current) : null;
  const stageW = width / STAGES.length;
  const edge = (side: 1 | -1) => {
    const points: string[] = [];
    const steps = 48;
    for (let i = 0; i <= steps; i++) {
      const x01 = i / steps;
      const y = centerY - side * ((thicknessAt(profile, x01) * bandH) / 2);
      points.push(`${(x01 * width).toFixed(1)},${y.toFixed(1)}`);
    }
    return points;
  };
  const top = edge(1);
  const bottom = edge(-1);
  const bandPath = width ? `M${top.join(" L")} L${bottom.reverse().join(" L")} Z` : "";
  const jamColor = (level: number) =>
    level >= 0.8
      ? "var(--op-flow-jam)"
      : level >= 0.45
        ? "var(--op-flow-busy)"
        : "var(--op-flow-free)";

  return (
    <div className="min-w-0">
      <div
        ref={box}
        tabIndex={0}
        role="group"
        aria-roledescription="flow chart"
        aria-label={bandSummary(flow, pct, highlight)}
        aria-describedby={helpId}
        onKeyDown={onKeyDown}
        onBlur={() => setActive((current) => (current?.by === "keyboard" ? null : current))}
        className="relative w-full rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-4"
        style={{ height }}
      >
        {width > 0 ? (
          <svg width={width} height={height} className="absolute inset-0" aria-hidden>
            <defs>
              <linearGradient id={gradientId} x1="0" x2="1" y1="0" y2="0">
                {profile.map((stage, i) => (
                  <stop
                    key={stage.stage}
                    offset={(i + 0.5) / STAGES.length}
                    stopColor={jamColor(stage.jam)}
                  />
                ))}
              </linearGradient>
            </defs>
            {STAGES.slice(1).map((stage, i) => (
              <line
                key={stage}
                x1={stageW * (i + 1)}
                x2={stageW * (i + 1)}
                y1={labelsH - 6}
                y2={height - MARKER_H + 4}
                stroke="var(--op-flow-grid)"
                strokeWidth={1}
              />
            ))}
            <path d={bandPath} fill={`url(#${gradientId})`} fillOpacity={0.13} />
            <polyline
              points={top.join(" ")}
              fill="none"
              stroke={`url(#${gradientId})`}
              strokeWidth={2}
              strokeLinejoin="round"
            />
            <polyline
              points={edge(-1).join(" ")}
              fill="none"
              stroke={`url(#${gradientId})`}
              strokeWidth={2}
              strokeLinejoin="round"
            />
            {jamIndex >= 0 ? (
              <line
                x1={stageW * (jamIndex + 0.5)}
                x2={stageW * (jamIndex + 0.5)}
                y1={centerY + (profile[jamIndex].thickness * bandH) / 2 + 2}
                y2={height - MARKER_H + 10}
                stroke="var(--op-flow-ink-2)"
                strokeWidth={1}
              />
            ) : null}
          </svg>
        ) : null}
        <canvas
          ref={canvas}
          aria-hidden
          className="absolute inset-0"
          style={{ width, height }}
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const hit = nearest(event.clientX - rect.left, event.clientY - rect.top);
            setActive((current) =>
              hit ? { key: hit.key, by: "pointer" } : current?.by === "pointer" ? null : current,
            );
          }}
          onPointerLeave={() =>
            setActive((current) => (current?.by === "pointer" ? null : current))
          }
        />
        <div className="pointer-events-none absolute inset-x-0 top-0 grid grid-cols-4" aria-hidden>
          {profile.map((stage, i) => (
            <div
              key={stage.stage}
              className={
                i === 0 ? "pl-1" : i === STAGES.length - 1 ? "pr-1 text-right" : "text-center"
              }
            >
              <p className="text-[11px] font-bold leading-tight text-(--op-flow-ink-2) sm:text-[12px]">
                {STAGE_LABELS[stage.stage]}
              </p>
              <p className="mt-0.5 text-[16px] font-extrabold text-(--op-flow-ink) sm:text-[18px]">
                {formatHours(stage.hours)}
                <Sample hours={stage.hours} count={stage.count} stacked={narrow} />
              </p>
            </div>
          ))}
        </div>
        {jamIndex >= 0 ? (
          <div
            className="pointer-events-none absolute flex -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border border-(--op-flow-border) bg-(--op-flow-surface) px-2.5 py-1 text-[12px] font-bold text-(--op-flow-ink)"
            style={{
              left: Math.min(Math.max(stageW * (jamIndex + 0.5), 64), width - 64),
              top: height - MARKER_H + 8,
            }}
            aria-hidden
          >
            <span className="inline-block h-2 w-2 rounded-full bg-(--op-flow-jam)" />
            Worst jam · {formatHours(profile[jamIndex].hours)}
          </div>
        ) : null}
        {activeDot && activePoint ? (
          <Tooltip dot={activeDot} x={activePoint.x} y={activePoint.y} width={width} />
        ) : null}
      </div>
      <p id={helpId} className="sr-only">
        Left and right arrows step through the requests, Space pauses or plays, Escape lets go. Show
        as table lists every request.
      </p>
      <p aria-live="polite" className="sr-only">
        {activeDot && active?.by === "keyboard" ? tooltipWords(activeDot) : ""}
      </p>
    </div>
  );
}

/**
 * How many requests a stage time rests on, small beside it ("24 h · 1 request")
 * and under it on a narrow band. A stage with no time shows none.
 */
function Sample({
  hours,
  count,
  stacked,
}: {
  hours: number | null;
  count: number;
  /** On its own line under the time, not beside it. */
  stacked: boolean;
}) {
  const words = sampleWords(hours, count);
  if (words === null) return null;
  return (
    <span
      className={cn(
        "font-medium leading-tight text-(--op-flow-ink-2)",
        stacked ? "block text-[11px]" : "text-[12px]",
      )}
    >
      {stacked ? null : <span aria-hidden>{" · "}</span>}
      {words}
    </span>
  );
}

function tooltipWords(dot: Dot): string {
  const item = dot.item;
  return `${item.title}, ${requestReference(item)}, ${typeLabel(item.request_type)}, by ${
    item.author_name ?? "someone not named"
  }. ${standingWords(item)}${item.state === "merged" ? ` on ${formatDay(item.merged_at)}` : ""}.`;
}

function Tooltip({ dot, x, y, width }: { dot: Dot; x: number; y: number; width: number }) {
  const item = dot.item;
  const lead = leadHours(item);
  const left = Math.min(Math.max(x, 130), Math.max(130, width - 130));
  const below = y < 120;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 w-[248px] rounded-xl border border-(--op-flow-border) bg-(--op-flow-surface) p-3 text-left shadow-op-menu"
      style={{
        left,
        top: below ? y + 14 : y - 14,
        transform: `translate(-50%, ${below ? "0" : "-100%"})`,
      }}
    >
      <p className="text-[13px] font-extrabold leading-snug text-(--op-flow-ink)">{item.title}</p>
      <p className="mt-1 text-[12px] text-(--op-flow-ink-2)">
        {requestReference(item)} · {item.author_name ?? "author not named"}
      </p>
      <p className="mt-2 flex items-center gap-2 text-[12px] text-(--op-flow-ink)">
        <span
          aria-hidden
          className="inline-block h-0.5 w-3 flex-none rounded-full"
          style={{ background: `var(${typeColorVar(item.request_type)})` }}
        />
        <span>
          <span className="font-bold">{typeLabel(item.request_type)}</span>{" "}
          <span className="text-(--op-flow-muted)">
            {typeSourceWords(item.type_source, item.type_evidence)}
          </span>
        </span>
      </p>
      <p className="mt-1 text-[12px] font-bold text-(--op-flow-ink)">
        {standingWords(item)}
        {item.state === "merged" ? (
          <span className="font-medium text-(--op-flow-ink-2)">
            {" "}
            {formatDay(item.merged_at)}
            {lead !== null ? ` · ${formatHours(lead)} from first commit` : ""}
          </span>
        ) : null}
      </p>
    </div>
  );
}
