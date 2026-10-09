import { useId } from "react";

import { formatDay, progressWidth } from "../../lib/format";
import { stageColor } from "../../lib/status";
import { type ProgressFacts, progressParts, stageStrip, tileName } from "./dailyViz";

/** A tile's width on the 600-wide strip: six tiles, five 8 px gaps. */
const TILE = (600 - 5 * 8) / 6;

/**
 * D2, the stage strip, under Where we stand: what moved since yesterday? Progress
 * is said here once (it was in the header and again under Progress), then each
 * stage's count with its change and an arrow per move. It replaces the stage-count
 * line and the "What changed since" lines; a scope change or a moved date stays in
 * the report's words under it. On a phone: six tiles, and the moves as a list.
 */
export function StageStrip({
  progress,
  progressLine,
}: {
  progress: ProgressFacts;
  progressLine: string;
}) {
  const view = stageStrip(progress);
  const parts = progressParts(progressLine);
  const marker = useId();
  const since = progress.since ? `, and what changed since ${formatDay(progress.since)}` : "";

  return (
    <figure className="dv-viz" aria-label="Progress and what changed">
      <p className="dv-grp">Progress{since}</p>
      <div className="mb-2.5 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        {parts.lead ? (
          <span className="text-[22px] font-extrabold tabular-nums">{parts.lead}</span>
        ) : null}
        <span className="dv-sub">{parts.rest}</span>
      </div>
      {progress.total > 0 ? (
        <div
          className="mb-3.5 h-2 max-w-[660px] overflow-hidden rounded bg-(--op-day-surface-2)"
          role="img"
          aria-label={parts.lead ? `${parts.lead} complete` : progressLine}
        >
          <span
            className="block h-full rounded-r bg-(--op-day-magenta)"
            style={{ width: `${progressWidth(progress.percent)}%` }}
          />
        </div>
      ) : null}

      {view.tiles.length ? (
        <>
          <div className="dv2-wide">
            <div
              className="dv-scroll"
              tabIndex={0}
              role="region"
              aria-label="Stage strip, scrolls sideways"
            >
              <svg
                className="dv-chart dv-d2"
                // The room above the tiles is for the arrows; without them it is cut.
                viewBox={view.arrows.length ? "0 0 600 184" : "0 56 600 128"}
                role="img"
                aria-label={view.label}
              >
                <defs>
                  <marker
                    id={marker}
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M0,0 L10,5 L0,10 z" fill="var(--op-day-ink)" />
                  </marker>
                </defs>
                {view.tiles.map((tile, index) => {
                  const left = index * (TILE + 8);
                  const name = tileName(tile.label);
                  return (
                    <g key={tile.stage}>
                      <title>{tile.title}</title>
                      <rect
                        x={left}
                        y={64}
                        width={TILE}
                        height={112}
                        rx={14}
                        fill="var(--op-day-surface-2)"
                      />
                      <rect
                        x={left + 12}
                        y={76}
                        width={24}
                        height={4}
                        rx={2}
                        fill={stageColor(tile.stage)}
                      />
                      {name.map((line, i) => (
                        <text
                          key={line}
                          x={left + 11}
                          y={97 + i * 15}
                          className="dv-tx dv-b dv-m"
                          textAnchor="start"
                        >
                          {line}
                        </text>
                      ))}
                      <text
                        x={left + 12}
                        y={146}
                        className="dv-tx dv-tx-ink dv-xb dv-xl dv-num"
                        textAnchor="start"
                      >
                        {tile.count}
                      </text>
                      <text
                        x={left + 12}
                        y={166}
                        className={
                          tile.changed ? "dv-tx dv-tx-ink dv-b dv-s dv-num" : "dv-tx dv-tx-m"
                        }
                        textAnchor="start"
                      >
                        {tile.change}
                      </text>
                    </g>
                  );
                })}
                {view.arrows.map((arrow) => (
                  <g key={arrow.path}>
                    <path
                      d={arrow.path}
                      fill="none"
                      stroke="var(--op-day-ink)"
                      strokeWidth={1.5}
                      markerEnd={`url(#${marker})`}
                    >
                      <title>{arrow.title}</title>
                    </path>
                    <text
                      x={arrow.label.x}
                      y={arrow.label.y}
                      className="dv-tx dv-tx-ink dv-b dv-s dv-halo"
                      textAnchor="middle"
                    >
                      {arrow.label.text}
                    </text>
                  </g>
                ))}
              </svg>
            </div>
            {view.listed.length ? (
              <Moves items={view.listed} label="Other changes since the previous day" />
            ) : null}
          </div>
          <div className="dv2-narrow">
            <div className="dv-tiles">
              {view.tiles.map((tile) => (
                <div key={tile.stage} className="dv-tile" title={tile.title}>
                  <i
                    className="block h-1 w-[22px] rounded-sm"
                    style={{ background: stageColor(tile.stage) }}
                    aria-hidden
                  />
                  <p className="dv-tile-name">{tile.label}</p>
                  <p className="text-[24px] font-extrabold tabular-nums">{tile.count}</p>
                  <p
                    className={
                      tile.changed
                        ? "text-[11.5px] font-bold"
                        : "text-[11.5px] text-(--op-day-muted)"
                    }
                  >
                    {tile.change || " "}
                  </p>
                </div>
              ))}
            </div>
            {view.all.length ? (
              <Moves items={view.all} label="Moves since the previous day" />
            ) : null}
          </div>
        </>
      ) : null}

      {view.summary ? <p className="dv-note mt-2">{view.summary}</p> : null}
      {[...progress.other_changes, ...progress.notes].map((line) => (
        <p key={line} className="dv-note mt-1">
          {line}
        </p>
      ))}
    </figure>
  );
}

function Moves({ items, label }: { items: { move: string; what: string }[]; label: string }) {
  return (
    <ul className="dv-moves" aria-label={label}>
      {items.map((item, index) => (
        <li key={`${item.move}-${index}`}>
          <span className="font-bold text-(--op-day-ink)">{item.move}</span>
          {item.what ? <span>{item.what}</span> : null}
        </li>
      ))}
    </ul>
  );
}
